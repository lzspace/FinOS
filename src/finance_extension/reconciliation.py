"""Event-sourced duplicate, transfer and refund reconciliation for 0.4.0."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

from .classification import active_classifications
from .store import LocalFinanceStore, StoreInvariantError

RECONCILIATION_POLICY_VERSION = "reconciliation@1.0.0"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _relation_id(prefix: str, *transaction_ids: str) -> str:
    value = "\x1f".join(transaction_ids).encode()
    return f"{prefix}_{hashlib.sha256(value).hexdigest()[:32]}"


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


def _transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events("TransactionNormalized"):
        result[event["payload"]["transaction_id"]] = {
            **event["payload"],
            "event_id": event["event_id"],
        }
    return result


def _raw_hashes(store: LocalFinanceStore) -> dict[str, str]:
    return {
        event["event_id"]: event["payload"]["content_hash"]
        for event in store.events("RawTransactionImported")
    }


def _latest_relations(store: LocalFinanceStore, event_prefix: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"].startswith(event_prefix):
            latest[event["aggregate_id"]] = event
    return latest


def _date_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return abs(
        (date.fromisoformat(left["booking_date"]) - date.fromisoformat(right["booking_date"])).days
    )


def detect_duplicates(store: LocalFinanceStore, month: str | None = None) -> int:
    transactions = list(_transactions(store).values())
    raw_hashes = _raw_hashes(store)
    existing = _latest_relations(store, "DuplicateTransaction")
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for left, right in combinations(transactions, 2):
        if month and not (
            left["booking_date"].startswith(month) or right["booking_date"].startswith(month)
        ):
            continue
        same_core = (
            left["account_id"] == right["account_id"]
            and left["amount"] == right["amount"]
            and left["currency"] == right["currency"]
            and left["counterparty"].casefold() == right["counterparty"].casefold()
            and left["normalized_description"].casefold()
            == right["normalized_description"].casefold()
        )
        if not same_core or _date_distance(left, right) > 1:
            continue
        exact = (
            left["booking_date"] == right["booking_date"]
            and left["value_date"] == right["value_date"]
            and raw_hashes.get(left["raw_transaction_event_id"])
            == raw_hashes.get(right["raw_transaction_event_id"])
        )
        primary, duplicate = sorted((left["transaction_id"], right["transaction_id"]))
        relation_id = _relation_id("dup", primary, duplicate)
        if relation_id in existing:
            continue
        payload = {
            "relation_id": relation_id,
            "primary_transaction_id": primary,
            "duplicate_transaction_id": duplicate,
            "match_type": "EXACT_DUPLICATE" if exact else "LIKELY_DUPLICATE",
            "policy_version": RECONCILIATION_POLICY_VERSION,
            "status": "DETECTED",
        }
        events.append(
            _event(
                "DuplicateTransactionDetected",
                "DuplicateRelation",
                relation_id,
                1,
                command_id,
                payload,
            )
        )
        if exact:
            events.append(
                _event(
                    "DuplicateTransactionConfirmed",
                    "DuplicateRelation",
                    relation_id,
                    2,
                    command_id,
                    {**payload, "status": "CONFIRMED", "decided_by": "SYSTEM"},
                )
            )
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "DetectDuplicates",
                "idempotency_key": f"detect-duplicates:{month or 'all'}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def _decide_duplicate(store: LocalFinanceStore, relation_id: str, confirm: bool) -> int:
    current = _latest_relations(store, "DuplicateTransaction").get(relation_id)
    target = "CONFIRMED" if confirm else "REJECTED"
    if current and current["payload"]["status"] == target:
        return 0
    if not current or current["event_type"] != "DuplicateTransactionDetected":
        raise StoreInvariantError("FINANCE_DUPLICATE_NOT_REVIEWABLE")
    command_id = _id("cmd")
    version = store.next_aggregate_version("DuplicateRelation", relation_id)
    kind = "DuplicateTransactionConfirmed" if confirm else "DuplicateTransactionRejected"
    payload = {**current["payload"], "status": target, "decided_by": "USER"}
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "ConfirmDuplicate" if confirm else "RejectDuplicate",
            "idempotency_key": f"duplicate:{target}:{relation_id}",
        },
        [_event(kind, "DuplicateRelation", relation_id, version, command_id, payload)],
    )
    return 1


def confirm_duplicate(store: LocalFinanceStore, relation_id: str) -> int:
    return _decide_duplicate(store, relation_id, True)


def reject_duplicate(store: LocalFinanceStore, relation_id: str) -> int:
    return _decide_duplicate(store, relation_id, False)


def detect_transfers(store: LocalFinanceStore, month: str | None = None) -> int:
    transactions = list(_transactions(store).values())
    existing = _latest_relations(store, "TransferMatch")
    active_members = {
        transaction_id
        for event in existing.values()
        if event["payload"]["status"] == "CONFIRMED"
        for transaction_id in (
            event["payload"]["outgoing_transaction_id"],
            event["payload"]["incoming_transaction_id"],
        )
    }
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for left, right in combinations(transactions, 2):
        if month and not (
            left["booking_date"].startswith(month) or right["booking_date"].startswith(month)
        ):
            continue
        if Decimal(left["amount"]) * Decimal(right["amount"]) >= 0:
            continue
        outgoing, incoming = (left, right) if Decimal(left["amount"]) < 0 else (right, left)
        compatible = (
            outgoing["account_id"] != incoming["account_id"]
            and outgoing["currency"] == incoming["currency"]
            and abs(Decimal(outgoing["amount"])) == Decimal(incoming["amount"])
            and _date_distance(outgoing, incoming) <= 1
        )
        if not compatible:
            continue
        members = (outgoing["transaction_id"], incoming["transaction_id"])
        relation_id = _relation_id("trf", *members)
        if relation_id in existing or any(member in active_members for member in members):
            continue
        confidence = "1.0" if _date_distance(outgoing, incoming) == 0 else "0.9"
        payload = {
            "relation_id": relation_id,
            "outgoing_transaction_id": members[0],
            "incoming_transaction_id": members[1],
            "amount": str(abs(Decimal(outgoing["amount"]))),
            "currency": outgoing["currency"],
            "confidence": confidence,
            "policy_version": RECONCILIATION_POLICY_VERSION,
            "status": "PROPOSED",
        }
        events.append(
            _event("TransferMatchProposed", "TransferRelation", relation_id, 1, command_id, payload)
        )
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "DetectTransfers",
                "idempotency_key": f"detect-transfers:{month or 'all'}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def _active_transfer_members(store: LocalFinanceStore, excluding: str | None = None) -> set[str]:
    result: set[str] = set()
    for relation_id, event in _latest_relations(store, "TransferMatch").items():
        if relation_id != excluding and event["payload"]["status"] == "CONFIRMED":
            result.update(
                (
                    event["payload"]["outgoing_transaction_id"],
                    event["payload"]["incoming_transaction_id"],
                )
            )
    return result


def confirm_transfer(store: LocalFinanceStore, outgoing_id: str, incoming_id: str) -> int:
    relation_id = _relation_id("trf", outgoing_id, incoming_id)
    current = _latest_relations(store, "TransferMatch").get(relation_id)
    if current and current["payload"]["status"] == "CONFIRMED":
        return 0
    if not current or current["event_type"] != "TransferMatchProposed":
        raise StoreInvariantError("FINANCE_TRANSFER_NOT_REVIEWABLE")
    if {outgoing_id, incoming_id} & _active_transfer_members(store, excluding=relation_id):
        raise StoreInvariantError("FINANCE_TRANSFER_MEMBER_ALREADY_ACTIVE")
    transactions = _transactions(store)
    outgoing, incoming = transactions.get(outgoing_id), transactions.get(incoming_id)
    if (
        not outgoing
        or not incoming
        or Decimal(outgoing["amount"]) >= 0
        or Decimal(incoming["amount"]) <= 0
    ):
        raise StoreInvariantError("FINANCE_TRANSFER_INCOMPATIBLE")
    command_id = _id("cmd")
    version = store.next_aggregate_version("TransferRelation", relation_id)
    payload = {**current["payload"], "status": "CONFIRMED", "decided_by": "USER"}
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "ConfirmTransfer",
            "idempotency_key": f"confirm-transfer:{relation_id}",
        },
        [
            _event(
                "TransferMatchConfirmed",
                "TransferRelation",
                relation_id,
                version,
                command_id,
                payload,
            )
        ],
    )
    return 1


def reject_transfer(store: LocalFinanceStore, outgoing_id: str, incoming_id: str) -> int:
    relation_id = _relation_id("trf", outgoing_id, incoming_id)
    current = _latest_relations(store, "TransferMatch").get(relation_id)
    if current and current["payload"]["status"] == "REJECTED":
        return 0
    if not current or current["event_type"] != "TransferMatchProposed":
        raise StoreInvariantError("FINANCE_TRANSFER_NOT_REVIEWABLE")
    command_id = _id("cmd")
    version = store.next_aggregate_version("TransferRelation", relation_id)
    payload = {**current["payload"], "status": "REJECTED", "decided_by": "USER"}
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "RejectTransfer",
            "idempotency_key": f"reject-transfer:{relation_id}",
        },
        [
            _event(
                "TransferMatchRejected",
                "TransferRelation",
                relation_id,
                version,
                command_id,
                payload,
            )
        ],
    )
    return 1


def break_transfer(store: LocalFinanceStore, outgoing_id: str, incoming_id: str) -> int:
    relation_id = _relation_id("trf", outgoing_id, incoming_id)
    current = _latest_relations(store, "TransferMatch").get(relation_id)
    if current and current["payload"]["status"] == "BROKEN":
        return 0
    if not current or current["event_type"] != "TransferMatchConfirmed":
        raise StoreInvariantError("FINANCE_TRANSFER_NOT_BREAKABLE")
    command_id = _id("cmd")
    version = store.next_aggregate_version("TransferRelation", relation_id)
    payload = {**current["payload"], "status": "BROKEN", "decided_by": "USER"}
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "BreakTransferMatch",
            "idempotency_key": f"break-transfer:{relation_id}",
        },
        [
            _event(
                "TransferMatchBroken", "TransferRelation", relation_id, version, command_id, payload
            )
        ],
    )
    return 1


def detect_refunds(store: LocalFinanceStore, month: str | None = None) -> int:
    transactions = list(_transactions(store).values())
    existing = _latest_relations(store, "RefundRelation")
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for original, refund in ((left, right) for left, right in combinations(transactions, 2)):
        if Decimal(original["amount"]) > 0:
            original, refund = refund, original
        if Decimal(original["amount"]) >= 0 or Decimal(refund["amount"]) <= 0:
            continue
        if month and not refund["booking_date"].startswith(month):
            continue
        text = refund["normalized_description"].casefold()
        compatible = (
            original["currency"] == refund["currency"]
            and original["account_id"] == refund["account_id"]
            and date.fromisoformat(refund["booking_date"])
            >= date.fromisoformat(original["booking_date"])
            and Decimal(refund["amount"]) <= abs(Decimal(original["amount"]))
            and (
                original["counterparty"].casefold() == refund["counterparty"].casefold()
                or any(marker in text for marker in ("erstattung", "refund", "gutschrift"))
            )
        )
        if not compatible:
            continue
        relation_id = _relation_id("rfd", refund["transaction_id"], original["transaction_id"])
        if relation_id in existing:
            continue
        payload = {
            "relation_id": relation_id,
            "refund_transaction_id": refund["transaction_id"],
            "original_transaction_id": original["transaction_id"],
            "proposed_amount": refund["amount"],
            "currency": refund["currency"],
            "policy_version": RECONCILIATION_POLICY_VERSION,
            "status": "PROPOSED",
        }
        events.append(
            _event("RefundRelationProposed", "RefundRelation", relation_id, 1, command_id, payload)
        )
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "DetectRefunds",
                "idempotency_key": f"detect-refunds:{month or 'all'}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def confirm_refund(store: LocalFinanceStore, refund_id: str, original_id: str, amount: str) -> int:
    relation_id = _relation_id("rfd", refund_id, original_id)
    current = _latest_relations(store, "RefundRelation").get(relation_id)
    if current and current["payload"]["status"] == "CONFIRMED":
        return 0
    if not current or current["event_type"] != "RefundRelationProposed":
        raise StoreInvariantError("FINANCE_REFUND_NOT_REVIEWABLE")
    try:
        confirmed_amount = Decimal(amount)
    except InvalidOperation as exc:
        raise StoreInvariantError("FINANCE_REFUND_AMOUNT_INVALID") from exc
    transactions = _transactions(store)
    original, refund = transactions.get(original_id), transactions.get(refund_id)
    if (
        not original
        or not refund
        or confirmed_amount <= 0
        or confirmed_amount > Decimal(refund["amount"])
    ):
        raise StoreInvariantError("FINANCE_REFUND_AMOUNT_INVALID")
    active_refund_relations = _latest_relations(store, "RefundRelation")
    if any(
        event["aggregate_id"] != relation_id
        and event["payload"]["status"] == "CONFIRMED"
        and event["payload"]["refund_transaction_id"] == refund_id
        for event in active_refund_relations.values()
    ):
        raise StoreInvariantError("FINANCE_REFUND_ALREADY_ASSIGNED")
    already_refunded = sum(
        (
            Decimal(event["payload"]["confirmed_amount"])
            for event in active_refund_relations.values()
            if event["payload"]["status"] == "CONFIRMED"
            and event["payload"]["original_transaction_id"] == original_id
            and event["aggregate_id"] != relation_id
        ),
        Decimal("0"),
    )
    if already_refunded + confirmed_amount > abs(Decimal(original["amount"])):
        raise StoreInvariantError("FINANCE_REFUND_EXCEEDS_ORIGINAL")
    command_id = _id("cmd")
    version = store.next_aggregate_version("RefundRelation", relation_id)
    payload = {
        **current["payload"],
        "confirmed_amount": str(confirmed_amount),
        "status": "CONFIRMED",
        "decided_by": "USER",
    }
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "ConfirmRefund",
            "idempotency_key": f"confirm-refund:{relation_id}:{confirmed_amount}",
        },
        [
            _event(
                "RefundRelationConfirmed",
                "RefundRelation",
                relation_id,
                version,
                command_id,
                payload,
            )
        ],
    )
    return 1


def reject_refund(store: LocalFinanceStore, refund_id: str, original_id: str) -> int:
    relation_id = _relation_id("rfd", refund_id, original_id)
    current = _latest_relations(store, "RefundRelation").get(relation_id)
    if current and current["payload"]["status"] == "REJECTED":
        return 0
    if not current or current["event_type"] != "RefundRelationProposed":
        raise StoreInvariantError("FINANCE_REFUND_NOT_REVIEWABLE")
    command_id = _id("cmd")
    version = store.next_aggregate_version("RefundRelation", relation_id)
    payload = {**current["payload"], "status": "REJECTED", "decided_by": "USER"}
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "RejectRefund",
            "idempotency_key": f"reject-refund:{relation_id}",
        },
        [
            _event(
                "RefundRelationRejected",
                "RefundRelation",
                relation_id,
                version,
                command_id,
                payload,
            )
        ],
    )
    return 1


def relation_review(store: LocalFinanceStore, relation_type: str) -> list[dict[str, Any]]:
    prefixes = {
        "duplicates": "DuplicateTransaction",
        "transfers": "TransferMatch",
        "refunds": "RefundRelation",
    }
    if relation_type not in prefixes:
        raise StoreInvariantError("FINANCE_RELATION_TYPE_UNKNOWN")
    return [
        event
        for event in _latest_relations(store, prefixes[relation_type]).values()
        if event["payload"]["status"] in {"DETECTED", "PROPOSED"}
    ]


def reconcile(store: LocalFinanceStore, month: str | None = None) -> dict[str, int]:
    return {
        "duplicates": detect_duplicates(store, month),
        "transfers": detect_transfers(store, month),
        "refunds": detect_refunds(store, month),
    }


def reconciled_transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    transactions = _transactions(store)
    duplicates = _latest_relations(store, "DuplicateTransaction")
    transfers = _latest_relations(store, "TransferMatch")
    refunds = _latest_relations(store, "RefundRelation")
    duplicate_ids = {
        event["payload"]["duplicate_transaction_id"]
        for event in duplicates.values()
        if event["payload"]["status"] == "CONFIRMED"
    }
    transfer_ids = {
        transaction_id
        for event in transfers.values()
        if event["payload"]["status"] == "CONFIRMED"
        for transaction_id in (
            event["payload"]["outgoing_transaction_id"],
            event["payload"]["incoming_transaction_id"],
        )
    }
    investment_funding_ids = {
        event["payload"]["cash_transaction_id"]
        for event in store.events("InvestmentFundingRelationConfirmed")
        if event["payload"]["status"] == "CONFIRMED"
    }
    for event in store.events("InvestmentFundingRelationBroken"):
        investment_funding_ids.discard(event["payload"]["cash_transaction_id"])
    refund_by_id: dict[str, dict[str, Any]] = {}
    refund_total_by_original: dict[str, Decimal] = {}
    for event in refunds.values():
        if event["payload"]["status"] == "CONFIRMED":
            refund_by_id[event["payload"]["refund_transaction_id"]] = event
            original_id = event["payload"]["original_transaction_id"]
            refund_total_by_original[original_id] = refund_total_by_original.get(
                original_id, Decimal("0")
            ) + Decimal(event["payload"]["confirmed_amount"])
    result: dict[str, dict[str, Any]] = {}
    for transaction_id, transaction in transactions.items():
        amount = Decimal(transaction["amount"])
        duplicate = transaction_id in duplicate_ids
        transfer = transaction_id in transfer_ids
        investment_funding = transaction_id in investment_funding_ids
        is_refund = transaction_id in refund_by_id
        effective = (
            Decimal("0")
            if duplicate or transfer or investment_funding or is_refund
            else amount
        )
        if (
            transaction_id in refund_total_by_original
            and not duplicate
            and not transfer
            and not investment_funding
        ):
            effective += refund_total_by_original[transaction_id]
        result[transaction_id] = {
            "transaction_id": transaction_id,
            "duplicate_status": "CONFIRMED" if duplicate else "NONE",
            "transfer_status": "CONFIRMED" if transfer else "NONE",
            "investment_funding_status": "CONFIRMED" if investment_funding else "NONE",
            "refund_status": "REFUND"
            if is_refund
            else ("REFUNDED" if transaction_id in refund_total_by_original else "NONE"),
            "effective_amount": effective,
            "cashflow_relevant": not (
                duplicate or transfer or investment_funding or is_refund
            ),
            "category_relevant": not (
                duplicate or transfer or investment_funding or is_refund
            ),
        }
    return result


def reconciled_monthly_cashflow(store: LocalFinanceStore, month: str) -> dict[str, Any]:
    transactions = _transactions(store)
    projected = reconciled_transactions(store)
    refunds = _latest_relations(store, "RefundRelation")
    transfer_relations = _latest_relations(store, "TransferMatch")
    gross_income = gross_expenses = effective_income = effective_expenses = Decimal("0")
    refund_amount = excluded_duplicates = internal_transfers = Decimal("0")
    for transaction_id, transaction in transactions.items():
        if not transaction["booking_date"].startswith(month):
            continue
        amount = Decimal(transaction["amount"])
        gross_income += max(amount, Decimal("0"))
        gross_expenses += max(-amount, Decimal("0"))
        view = projected[transaction_id]
        if view["duplicate_status"] == "CONFIRMED":
            excluded_duplicates += abs(amount)
        elif (
            view["transfer_status"] != "CONFIRMED"
            and view["investment_funding_status"] != "CONFIRMED"
            and view["refund_status"] != "REFUND"
        ):
            effective_income += max(amount, Decimal("0"))
            effective_expenses += max(-amount, Decimal("0"))
    for event in refunds.values():
        refund = transactions[event["payload"]["refund_transaction_id"]]
        if event["payload"]["status"] == "CONFIRMED" and refund["booking_date"].startswith(month):
            value = Decimal(event["payload"]["confirmed_amount"])
            refund_amount += value
            effective_expenses -= value
    for event in transfer_relations.values():
        outgoing = transactions[event["payload"]["outgoing_transaction_id"]]
        if event["payload"]["status"] == "CONFIRMED" and outgoing["booking_date"].startswith(month):
            internal_transfers += Decimal(event["payload"]["amount"])
    return {
        "period": month,
        "gross_income": gross_income,
        "gross_expenses": gross_expenses,
        "internal_transfers": internal_transfers,
        "refunds": refund_amount,
        "excluded_duplicates": excluded_duplicates,
        "effective_income": effective_income,
        "effective_expenses": effective_expenses,
        "net_cashflow": effective_income - effective_expenses,
    }


def reconciled_category_breakdown(store: LocalFinanceStore, month: str) -> dict[str, Any]:
    transactions = _transactions(store)
    projected = reconciled_transactions(store)
    classifications = active_classifications(store)
    refunds = _latest_relations(store, "RefundRelation")
    categories: dict[str, dict[str, Any]] = {}

    def bucket(code: str) -> dict[str, Any]:
        return categories.setdefault(
            code,
            {
                "category_code": code,
                "gross_expense": Decimal("0"),
                "refund_amount": Decimal("0"),
                "effective_expense": Decimal("0"),
                "transaction_count": 0,
            },
        )

    for transaction_id, transaction in transactions.items():
        if not transaction["booking_date"].startswith(month) or Decimal(transaction["amount"]) >= 0:
            continue
        view = projected[transaction_id]
        if not view["category_relevant"]:
            continue
        classification = classifications.get(transaction_id)
        code = (
            classification["payload"]["category_code"]
            if classification
            and classification["event_type"] == "TransactionClassificationConfirmed"
            else "UNCLASSIFIED"
        )
        value = abs(Decimal(transaction["amount"]))
        item = bucket(code)
        item["gross_expense"] += value
        item["effective_expense"] += value
        item["transaction_count"] += 1
    for event in refunds.values():
        if event["payload"]["status"] != "CONFIRMED":
            continue
        refund = transactions[event["payload"]["refund_transaction_id"]]
        if not refund["booking_date"].startswith(month):
            continue
        original_id = event["payload"]["original_transaction_id"]
        classification = classifications.get(original_id)
        code = (
            classification["payload"]["category_code"]
            if classification
            and classification["event_type"] == "TransactionClassificationConfirmed"
            else "UNCLASSIFIED"
        )
        value = Decimal(event["payload"]["confirmed_amount"])
        item = bucket(code)
        item["refund_amount"] += value
        item["effective_expense"] -= value
    return {"period": month, "categories": categories}
