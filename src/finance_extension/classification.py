"""Deterministic, event-sourced transaction classification."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .categories import CATEGORY_CODES, require_category
from .store import LocalFinanceStore, StoreInvariantError

RULE_VERSION = "1.0.0"
CLASSIFICATION_POLICY_VERSION = "rules@1.0.0"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    field: str
    operator: str
    value: str
    category_code: str
    priority: int
    version: int = 1


BUILTIN_RULES = (
    Rule(
        "rule_builtin_salary", "normalized_description", "CONTAINS", "gehalt", "INCOME_SALARY", 100
    ),
    Rule("rule_builtin_groceries", "counterparty", "CONTAINS", "supermarkt", "FOOD_GROCERIES", 100),
    Rule(
        "rule_builtin_restaurant",
        "normalized_description",
        "CONTAINS",
        "restaurant",
        "FOOD_RESTAURANTS",
        90,
    ),
    Rule("rule_builtin_rent", "normalized_description", "CONTAINS", "miete", "HOUSING_RENT", 90),
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _event(
    kind: str,
    aggregate_type: str,
    aggregate_id: str,
    version: int,
    command_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": kind,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": command_id,
        "causation_id": command_id,
        "payload": payload,
    }


def _normalized_transactions(
    store: LocalFinanceStore, month: str | None = None
) -> list[dict[str, Any]]:
    return [
        event
        for event in store.events("TransactionNormalized")
        if month is None or event["payload"]["booking_date"].startswith(month)
    ]


def _user_rules(store: LocalFinanceStore) -> list[Rule]:
    latest: dict[str, dict[str, Any]] = {}
    for event in store.events("ClassificationRuleCreated"):
        latest[event["payload"]["rule_id"]] = event
    return [
        Rule(
            rule_id=event["payload"]["rule_id"],
            field=event["payload"]["field"],
            operator=event["payload"]["operator"],
            value=event["payload"]["value"],
            category_code=event["payload"]["category_code"],
            priority=event["payload"]["priority"],
            version=event["payload"]["rule_version"],
        )
        for event in latest.values()
    ]


def active_classifications(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    relevant = {
        "TransactionClassificationProposed",
        "TransactionClassificationConfirmed",
        "TransactionClassificationRejected",
    }
    for event in store.events():
        if event["event_type"] in relevant:
            active[event["payload"]["transaction_id"]] = event
    return active


def _matches(rule: Rule, transaction: dict[str, Any]) -> bool:
    candidate = str(transaction[rule.field]).casefold()
    expected = rule.value.casefold()
    if rule.operator == "CONTAINS":
        return expected in candidate
    if rule.operator == "EQUALS":
        return expected == candidate
    if rule.operator == "STARTS_WITH":
        return candidate.startswith(expected)
    raise StoreInvariantError("FINANCE_RULE_OPERATOR_UNSUPPORTED")


def classify_transactions(store: LocalFinanceStore, month: str | None = None) -> int:
    state = active_classifications(store)
    rules = [*BUILTIN_RULES, *_user_rules(store)]
    ruleset_hash = hashlib.sha256(
        json.dumps(
            [rule.__dict__ for rule in sorted(rules, key=lambda item: item.rule_id)],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for normalized in _normalized_transactions(store, month):
        transaction = normalized["payload"]
        transaction_id = transaction["transaction_id"]
        current = state.get(transaction_id)
        if current and current["event_type"] == "TransactionClassificationConfirmed":
            continue
        if (
            current
            and current["event_type"] == "TransactionClassificationProposed"
            and current["payload"]["ruleset_hash"] == ruleset_hash
        ):
            continue
        matches = [rule for rule in rules if _matches(rule, transaction)]
        best_priority = max((rule.priority for rule in matches), default=None)
        winners = [rule for rule in matches if rule.priority == best_priority]
        categories = {rule.category_code for rule in winners}
        version = store.next_aggregate_version("Transaction", transaction_id)
        if len(categories) == 1:
            winner = sorted(winners, key=lambda rule: rule.rule_id)[0]
            events.append(
                _event(
                    "ClassificationRuleApplied",
                    "Transaction",
                    transaction_id,
                    version,
                    command_id,
                    {
                        "transaction_id": transaction_id,
                        "rule_id": winner.rule_id,
                        "rule_version": winner.version,
                        "category_code": winner.category_code,
                    },
                )
            )
            version += 1
            category, confidence, status = winner.category_code, "1.0", "PROPOSED"
        elif len(categories) > 1:
            category, confidence, status = "UNCLASSIFIED", "0.0", "CONFLICT"
        else:
            category, confidence, status = "UNCLASSIFIED", "0.0", "UNCLASSIFIED"
        events.append(
            _event(
                "TransactionClassificationProposed",
                "Transaction",
                transaction_id,
                version,
                command_id,
                {
                    "transaction_id": transaction_id,
                    "normalized_event_id": normalized["event_id"],
                    "category_code": category,
                    "confidence": confidence,
                    "status": status,
                    "policy_version": CLASSIFICATION_POLICY_VERSION,
                    "ruleset_hash": ruleset_hash,
                    "matching_rule_ids": sorted(rule.rule_id for rule in winners),
                },
            )
        )
    if not events:
        return 0
    key = f"classify:{month or 'all'}:{store.events()[-1]['sequence_number']}"
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "ClassifyTransactions",
                "idempotency_key": key,
            },
            events,
        )
    )


def confirm_classification(
    store: LocalFinanceStore,
    transaction_id: str,
    category_code: str,
    *,
    create_rule_from: str | None = None,
    priority: int = 200,
) -> int:
    require_category(category_code)
    normalized = next(
        (
            event
            for event in _normalized_transactions(store)
            if event["payload"]["transaction_id"] == transaction_id
        ),
        None,
    )
    if normalized is None:
        raise StoreInvariantError("FINANCE_TRANSACTION_NOT_FOUND")
    if create_rule_from and create_rule_from not in {"counterparty", "normalized_description"}:
        raise StoreInvariantError("FINANCE_RULE_FIELD_UNSUPPORTED")
    command_id = _id("cmd")
    version = store.next_aggregate_version("Transaction", transaction_id)
    events = [
        _event(
            "TransactionClassificationConfirmed",
            "Transaction",
            transaction_id,
            version,
            command_id,
            {
                "transaction_id": transaction_id,
                "normalized_event_id": normalized["event_id"],
                "category_code": category_code,
                "status": "CONFIRMED",
                "confirmed_by": "USER",
                "policy_version": CLASSIFICATION_POLICY_VERSION,
            },
        )
    ]
    if create_rule_from:
        rule_id = _id("rule")
        events.append(
            _event(
                "ClassificationRuleCreated",
                "ClassificationRule",
                rule_id,
                1,
                command_id,
                {
                    "rule_id": rule_id,
                    "rule_version": 1,
                    "field": create_rule_from,
                    "operator": "EQUALS",
                    "value": normalized["payload"][create_rule_from],
                    "category_code": category_code,
                    "priority": priority,
                },
            )
        )
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "ConfirmClassification",
            "idempotency_key": f"confirm:{transaction_id}:{category_code}:{version}",
        },
        events,
    )
    return version


def reject_classification(store: LocalFinanceStore, transaction_id: str) -> int:
    current = active_classifications(store).get(transaction_id)
    if not current or current["event_type"] != "TransactionClassificationProposed":
        raise StoreInvariantError("FINANCE_CLASSIFICATION_NOT_REVIEWABLE")
    version = store.next_aggregate_version("Transaction", transaction_id)
    command_id = _id("cmd")
    event = _event(
        "TransactionClassificationRejected",
        "Transaction",
        transaction_id,
        version,
        command_id,
        {
            "transaction_id": transaction_id,
            "proposed_event_id": current["event_id"],
            "category_code": current["payload"]["category_code"],
            "status": "REJECTED",
            "rejected_by": "USER",
        },
    )
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "RejectClassification",
            "idempotency_key": f"reject:{transaction_id}:{version}",
        },
        [event],
    )
    return version


def create_rule(
    store: LocalFinanceStore,
    *,
    field: str,
    operator: str,
    value: str,
    category_code: str,
    priority: int,
    rule_id: str | None = None,
) -> str:
    require_category(category_code)
    if field not in {"counterparty", "normalized_description"} or operator not in {
        "CONTAINS",
        "EQUALS",
        "STARTS_WITH",
    }:
        raise StoreInvariantError("FINANCE_RULE_UNSUPPORTED")
    rule_id = rule_id or _id("rule")
    version = store.next_aggregate_version("ClassificationRule", rule_id)
    command_id = _id("cmd")
    event = _event(
        "ClassificationRuleCreated",
        "ClassificationRule",
        rule_id,
        version,
        command_id,
        {
            "rule_id": rule_id,
            "rule_version": version,
            "field": field,
            "operator": operator,
            "value": value,
            "category_code": category_code,
            "priority": priority,
        },
    )
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "CreateClassificationRule",
            "idempotency_key": f"create-rule:{rule_id}:{version}",
        },
        [event],
    )
    return rule_id


def classification_review(store: LocalFinanceStore) -> list[dict[str, Any]]:
    normalized = {
        event["payload"]["transaction_id"]: event["payload"]
        for event in _normalized_transactions(store)
    }
    state = active_classifications(store)
    result = []
    for transaction_id, transaction in normalized.items():
        current = state.get(transaction_id)
        if not current or current["event_type"] != "TransactionClassificationConfirmed":
            result.append({"transaction": transaction, "classification": current})
    return result


def category_breakdown(store: LocalFinanceStore, month: str) -> dict[str, Any]:
    from decimal import Decimal

    state = active_classifications(store)
    totals = {code: Decimal("0") for code in CATEGORY_CODES}
    count = 0
    for normalized in _normalized_transactions(store, month):
        transaction = normalized["payload"]
        current = state.get(transaction["transaction_id"])
        category = (
            current["payload"]["category_code"]
            if current and current["event_type"] == "TransactionClassificationConfirmed"
            else "UNCLASSIFIED"
        )
        totals[category] += Decimal(transaction["amount"])
        count += 1
    return {
        "period": month,
        "categories": {key: value for key, value in totals.items() if value},
        "transaction_count": count,
    }
