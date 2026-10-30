"""Local-only category prediction shadow mode and model evaluation.

Shadow suggestions are separate aggregates.  They never participate in the
effective transaction classification, cashflow, forecasting, or rule engine.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .categories import CATEGORY_CODES, CUSTOM_CATEGORY_PATTERN, require_category
from .classification import active_classifications, confirm_classification, create_rule
from .classification_memory import (
    MEMORY_POLICY_VERSION,
    RETRIEVAL_POLICY_VERSION,
    memory_fingerprint,
    retrieve_classification_context,
)
from .local_model import (
    INFERENCE_POLICY_VERSION,
    LocalModelError,
    LocalModelProvider,
    StructuredInferenceRequest,
)
from .reconciliation import reconciled_transactions
from .store import LocalFinanceStore, StoreInvariantError

CATEGORY_SUGGESTION_VERSION = "1.0.0"
CATEGORY_EVALUATION_VERSION = "1.0.0"
TAXONOMY_VERSION = "1.0.0"
MINIMUM_METRIC_SAMPLE = 30
MAX_CONCURRENT_INFERENCES = 1
DEFAULT_MAX_BATCH_SIZE = 500

SUGGESTION_MODES = frozenset({"LIVE_SHADOW", "EVALUATION_REPLAY", "SUGGESTION"})
REASON_CODES = frozenset(
    {
        "MERCHANT_SEMANTICS",
        "DESCRIPTION_SEMANTICS",
        "RECURRING_PATTERN",
        "TRANSACTION_DIRECTION",
        "AMOUNT_PATTERN",
        "KNOWN_SERVICE_TYPE",
        "AMBIGUOUS_DESCRIPTION",
        "INSUFFICIENT_INFORMATION",
    }
)
EXCLUDED_TRANSACTION_TYPES = frozenset(
    {
        "TRANSFER",
        "INVESTMENT_FUNDING",
        "INVESTMENT_PURCHASE_FUNDING",
        "REFUND_RELATION",
        "CANCELLED",
        "SUPERSEDED",
    }
)
CATEGORY_LABELS = {
    "INCOME_SALARY": "Gehalt",
    "INCOME_OTHER": "Sonstige Einnahmen",
    "HOUSING_RENT": "Miete",
    "HOUSING_UTILITIES": "Wohnen und Nebenkosten",
    "FOOD_GROCERIES": "Lebensmittel",
    "FOOD_RESTAURANTS": "Restaurants",
    "MOBILITY_PUBLIC_TRANSPORT": "Öffentlicher Verkehr",
    "MOBILITY_FUEL": "Kraftstoff",
    "HEALTH": "Gesundheit",
    "INSURANCE": "Versicherungen",
    "LEISURE": "Freizeit",
    "SUBSCRIPTIONS": "Abonnements",
    "EDUCATION": "Bildung",
    "FEES": "Gebühren",
    "TAXES": "Steuern",
    "OTHER_EXPENSE": "Sonstige Ausgaben",
}

CATEGORY_SUGGESTION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["category_code", "raw_confidence", "alternatives", "reason_codes"],
    "properties": {
        "category_code": {"type": "string", "minLength": 1, "maxLength": 64},
        "raw_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "alternatives": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category_code", "confidence"],
                "properties": {
                    "category_code": {"type": "string", "minLength": 1, "maxLength": 64},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "reason_codes": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"enum": sorted(REASON_CODES)},
        },
    },
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    command_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "occurred_at": _now(),
        "correlation_id": command_id,
        "causation_id": command_id,
        "payload": payload,
    }


def _append(
    store: LocalFinanceStore,
    command_type: str,
    idempotency_key: str,
    events: list[dict[str, Any]],
) -> list[int]:
    if not events:
        return []
    return store.append_events(
        {
            "command_id": events[0]["correlation_id"],
            "command_type": command_type,
            "idempotency_key": idempotency_key,
        },
        events,
    )


def category_shadow_status(store: LocalFinanceStore) -> dict[str, Any]:
    enabled = False
    changed_at: str | None = None
    for event in store.events():
        if event["event_type"] == "CategoryShadowModeEnabled":
            enabled, changed_at = True, event["occurred_at"]
        elif event["event_type"] == "CategoryShadowModeDisabled":
            enabled, changed_at = False, event["occurred_at"]
    suggestions = len(store.events("CategorySuggestionGenerated"))
    active_evaluations = _active_evaluations(store)
    return {
        "enabled": enabled,
        "mode": "SHADOW_ONLY",
        "predictions": suggestions,
        "evaluated": len(active_evaluations),
        "pending_ground_truth": max(0, suggestions - len(active_evaluations)),
        "last_changed_at": changed_at,
        "max_concurrent_inferences": MAX_CONCURRENT_INFERENCES,
        "classification_side_effects": "FORBIDDEN",
    }


def enable_category_shadow_mode(store: LocalFinanceStore) -> dict[str, Any]:
    if category_shadow_status(store)["enabled"]:
        return category_shadow_status(store)
    command_id = _id("cmd")
    event = _event(
        "CategoryShadowModeEnabled",
        "CategoryShadowSettings",
        "category_shadow",
        store.next_aggregate_version("CategoryShadowSettings", "category_shadow"),
        command_id,
        {"enabled": True, "mode": "SHADOW_ONLY", "changed_at": _now()},
    )
    _append(
        store,
        "EnableCategoryShadowMode",
        f"category-shadow:enable:{event['aggregate_version']}",
        [event],
    )
    return category_shadow_status(store)


def disable_category_shadow_mode(store: LocalFinanceStore) -> dict[str, Any]:
    if not category_shadow_status(store)["enabled"]:
        return category_shadow_status(store)
    command_id = _id("cmd")
    event = _event(
        "CategoryShadowModeDisabled",
        "CategoryShadowSettings",
        "category_shadow",
        store.next_aggregate_version("CategoryShadowSettings", "category_shadow"),
        command_id,
        {"enabled": False, "mode": "SHADOW_ONLY", "changed_at": _now()},
    )
    _append(store, "DisableCategoryShadowMode", f"category-shadow:disable:{event['aggregate_version']}", [event])
    return category_shadow_status(store)


def _transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["payload"]["transaction_id"]: event["payload"]
        for event in store.events("TransactionNormalized")
    }


def _active_categories(store: LocalFinanceStore) -> list[str]:
    custom: set[str] = set()
    for event in store.events():
        code = event["payload"].get("category_code")
        if isinstance(code, str) and CUSTOM_CATEGORY_PATTERN.fullmatch(code):
            custom.add(code)
    return [code for code in CATEGORY_CODES if code != "UNCLASSIFIED"] + sorted(custom)


def _direction_categories(store: LocalFinanceStore, direction: str) -> list[str]:
    categories = _active_categories(store)
    if direction == "CREDIT":
        return [code for code in categories if code.startswith("INCOME_")]
    return [code for code in categories if not code.startswith("INCOME_")]


def transaction_eligibility(
    store: LocalFinanceStore, transaction_id: str
) -> dict[str, Any]:
    transaction = _transactions(store).get(transaction_id)
    if transaction is None:
        raise StoreInvariantError("FINANCE_TRANSACTION_NOT_FOUND")
    relation = reconciled_transactions(store, {transaction_id: transaction}).get(
        transaction_id, {}
    )
    return _transaction_eligibility(transaction, relation)


def _transaction_eligibility(
    transaction: dict[str, Any], relation: dict[str, Any]
) -> dict[str, Any]:
    direction = transaction.get("direction")
    transaction_type = str(transaction.get("transaction_type", ""))
    reason: str | None = None
    if direction not in {"DEBIT", "CREDIT"}:
        reason = "DIRECTION_UNSUPPORTED"
    elif transaction_type in EXCLUDED_TRANSACTION_TYPES or transaction.get("status") in {
        "CANCELLED",
        "SUPERSEDED",
    }:
        reason = "TRANSACTION_TYPE_EXCLUDED"
    elif transaction_type.startswith("INVESTMENT_"):
        reason = "INVESTMENT_EXCLUDED"
    elif relation and (
        not relation.get("category_relevant", True)
        or relation.get("refund_status") != "NONE"
    ):
        reason = "RELATION_EXCLUDED"
    return {"eligible": reason is None, "reason": reason}


def _amount_bucket(amount: str) -> str:
    value = abs(Decimal(amount))
    boundaries = (
        (Decimal("10"), "LT_10"),
        (Decimal("25"), "10_25"),
        (Decimal("50"), "25_50"),
        (Decimal("100"), "50_100"),
        (Decimal("250"), "100_250"),
        (Decimal("500"), "250_500"),
        (Decimal("1000"), "500_1000"),
    )
    return next((name for maximum, name in boundaries if value < maximum), "GT_1000")


def _redact_text(value: Any) -> str:
    text = str(value or "")[:240]
    text = re.sub(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", "[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED]", text)
    text = re.sub(r"\b\d{6,}\b", "[REDACTED]", text)
    return text.strip()


def _suggestion_request(
    store: LocalFinanceStore,
    transaction: dict[str, Any],
    memory_context: dict[str, Any],
) -> dict[str, Any]:
    categories = _direction_categories(store, transaction["direction"])
    return {
        "purpose": "CATEGORY_SUGGESTION",
        "transaction": {
            "merchant": _redact_text(transaction.get("counterparty")),
            "description": _redact_text(transaction.get("normalized_description")),
            "direction": transaction["direction"],
            "amount_bucket": _amount_bucket(transaction["amount"]),
            "currency": transaction["currency"],
            "recurring": False,
        },
        "classification_memory_context": memory_context,
        "allowed_categories": [
            {"code": code, "label": CATEGORY_LABELS.get(code, code.replace("_", " ").title())}
            for code in categories
        ],
    }


def validate_category_suggestion(
    candidate: dict[str, Any], *, allowed_categories: list[str]
) -> bool:
    if not isinstance(candidate, dict):
        return False
    category = candidate.get("category_code")
    alternatives = candidate.get("alternatives")
    reasons = candidate.get("reason_codes")
    confidence = candidate.get("raw_confidence")
    if category not in allowed_categories:
        return False
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        return False
    if not isinstance(alternatives, list) or not isinstance(reasons, list):
        return False
    alternative_codes: list[str] = []
    for alternative in alternatives:
        if not isinstance(alternative, dict) or set(alternative) != {"category_code", "confidence"}:
            return False
        alt_code = alternative["category_code"]
        alt_confidence = alternative["confidence"]
        if (
            alt_code not in allowed_categories
            or alt_code == category
            or alt_code in alternative_codes
            or not isinstance(alt_confidence, (int, float))
            or isinstance(alt_confidence, bool)
            or not 0 <= alt_confidence <= 1
        ):
            return False
        alternative_codes.append(alt_code)
    return bool(reasons) and len(reasons) == len(set(reasons)) and all(
        reason in REASON_CODES for reason in reasons
    )


def _existing_suggestion(
    store: LocalFinanceStore,
    *,
    transaction_id: str,
    mode: str,
    model: dict[str, Any],
    memory_context: dict[str, Any],
) -> dict[str, Any] | None:
    states = category_suggestion_states(store)
    return next(
        (
            event["payload"]
            for event in reversed(store.events("CategorySuggestionGenerated"))
            if event["payload"]["transaction_id"] == transaction_id
            and event["payload"]["mode"] == mode
            and event["payload"]["model_id"] == model["model_id"]
            and event["payload"]["model_version"] == model["model_version"]
            and event["payload"]["inference_policy_version"]
            == str(model.get("inference_policy_version", INFERENCE_POLICY_VERSION))
            and event["payload"]["taxonomy_version"] == TAXONOMY_VERSION
            and event["payload"].get("retrieval_policy_version")
            == memory_context["retrieval_policy_version"]
            and event["payload"].get("memory_snapshot_version")
            == memory_context["memory_snapshot_version"]
            and (
                mode != "SUGGESTION"
                or states.get(event["payload"]["suggestion_id"], {}).get("status")
                == "PENDING"
            )
        ),
        None,
    )


def category_suggestion_states(
    store: LocalFinanceStore, *, derive_stale: bool = True
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    lifecycle_status = {
        "CategorySuggestionConfirmed": "CONFIRMED",
        "CategorySuggestionCorrected": "CORRECTED",
        "CategorySuggestionRejected": "REJECTED",
        "CategorySuggestionSuperseded": "SUPERSEDED",
    }
    for event in store.events():
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == "CategorySuggestionGenerated" and payload["mode"] == "SUGGESTION":
            states[payload["suggestion_id"]] = {**payload, "status": "PENDING"}
        elif event_type in lifecycle_status:
            suggestion_id = payload["suggestion_id"]
            if suggestion_id in states:
                states[suggestion_id].update(payload)
                states[suggestion_id]["status"] = lifecycle_status[event_type]
    if derive_stale and any(state["status"] == "PENDING" for state in states.values()):
        normalized_events = {
            str(event["payload"]["transaction_id"]): event
            for event in store.events("TransactionNormalized")
        }
        transactions = {
            transaction_id: event["payload"]
            for transaction_id, event in normalized_events.items()
        }
        relations = reconciled_transactions(store, transactions)
        classifications = active_classifications(store)
        memory_snapshot = memory_fingerprint(store)
        for state in states.values():
            if state["status"] != "PENDING":
                continue
            reason = _suggestion_stale_reason_from_context(
                state,
                normalized_events=normalized_events,
                relations=relations,
                classifications=classifications,
                memory_snapshot=memory_snapshot,
            )
            if reason:
                state["status"] = "STALE"
                state["stale_reason"] = reason
    return states


def _suggestion_stale_reason_from_context(
    suggestion: dict[str, Any],
    *,
    normalized_events: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    memory_snapshot: str,
) -> str | None:
    transaction_id = suggestion["transaction_id"]
    normalized = normalized_events.get(transaction_id)
    if normalized is None or normalized["event_id"] != suggestion.get("normalized_event_id"):
        return "TRANSACTION_CHANGED"
    if suggestion.get("taxonomy_version") != TAXONOMY_VERSION:
        return "TAXONOMY_CHANGED"
    if suggestion.get("inference_policy_version") != INFERENCE_POLICY_VERSION:
        return "INFERENCE_POLICY_CHANGED"
    if suggestion.get("retrieval_policy_version") != RETRIEVAL_POLICY_VERSION:
        return "RETRIEVAL_POLICY_CHANGED"
    if suggestion.get("memory_snapshot_version") != memory_snapshot:
        return "CLASSIFICATION_MEMORY_CHANGED"
    if not _transaction_eligibility(
        normalized["payload"], relations.get(transaction_id, {})
    )["eligible"]:
        return "TRANSACTION_NOT_ELIGIBLE"
    current = classifications.get(transaction_id)
    if current and current["event_type"] == "TransactionClassificationConfirmed":
        if current["payload"].get("suggestion_id") == suggestion["suggestion_id"]:
            return None
        return "CLASSIFICATION_CHANGED"
    return None


def _is_unresolved_for_suggestion(
    store: LocalFinanceStore, transaction_id: str
) -> bool:
    current = active_classifications(store).get(transaction_id)
    if current is None or current["event_type"] == "TransactionClassificationRejected":
        return True
    if current["event_type"] == "TransactionClassificationConfirmed":
        return False
    return current["payload"].get("category_code") == "UNCLASSIFIED"


def _supersede_pending_suggestions(
    store: LocalFinanceStore, transaction_id: str, *, reason: str
) -> None:
    pending = [
        item
        for item in category_suggestion_states(store, derive_stale=False).values()
        if item["transaction_id"] == transaction_id and item["status"] == "PENDING"
    ]
    for item in pending:
        command_id = _id("cmd")
        event = _event(
            "CategorySuggestionSuperseded",
            "CategorySuggestion",
            item["suggestion_id"],
            store.next_aggregate_version("CategorySuggestion", item["suggestion_id"]),
            command_id,
            {
                "suggestion_id": item["suggestion_id"],
                "transaction_id": transaction_id,
                "status": "SUPERSEDED",
                "reason": reason,
                "superseded_at": _now(),
            },
        )
        _append(
            store,
            "SupersedeCategorySuggestion",
            f"category-suggestion:supersede:{item['suggestion_id']}:{event['aggregate_version']}",
            [event],
        )


def generate_category_shadow_suggestion(
    store: LocalFinanceStore,
    local_model: LocalModelProvider,
    transaction_id: str,
    *,
    mode: str,
) -> dict[str, Any]:
    if mode not in SUGGESTION_MODES:
        raise StoreInvariantError("CATEGORY_SHADOW_MODE_INVALID")
    if mode != "SUGGESTION" and not category_shadow_status(store)["enabled"]:
        raise StoreInvariantError("CATEGORY_SHADOW_DISABLED")
    eligibility = transaction_eligibility(store, transaction_id)
    if not eligibility["eligible"]:
        raise StoreInvariantError("CATEGORY_TRANSACTION_NOT_ELIGIBLE")
    if mode == "SUGGESTION" and not _is_unresolved_for_suggestion(
        store, transaction_id
    ):
        raise StoreInvariantError("CATEGORY_TRANSACTION_ALREADY_CLASSIFIED")
    status = local_model.status()
    if not status.get("available") or not status.get("category_suggestions"):
        raise LocalModelError("LOCAL_AI_UNAVAILABLE")
    model = status.get("model")
    if not isinstance(model, dict):
        raise LocalModelError("LOCAL_AI_UNAVAILABLE")
    transaction = _transactions(store)[transaction_id]
    memory_context = retrieve_classification_context(store, transaction_id)
    if existing := _existing_suggestion(
        store,
        transaction_id=transaction_id,
        mode=mode,
        model=model,
        memory_context=memory_context,
    ):
        return {"status": "EXISTING", "suggestion": existing}
    if mode == "SUGGESTION":
        _supersede_pending_suggestions(store, transaction_id, reason="NEW_INFERENCE")
    normalized = next(
        event
        for event in reversed(store.events("TransactionNormalized"))
        if event["payload"]["transaction_id"] == transaction_id
    )
    body = _suggestion_request(store, transaction, memory_context)
    allowed = [item["code"] for item in body["allowed_categories"]]

    def semantic_validator(candidate: dict[str, Any]) -> bool:
        return validate_category_suggestion(candidate, allowed_categories=allowed)

    try:
        output = local_model.infer_structured(
            StructuredInferenceRequest(
                purpose="CATEGORY_SUGGESTION",
                prompt=json.dumps(body, ensure_ascii=False, sort_keys=True),
                output_schema=CATEGORY_SUGGESTION_SCHEMA,
                semantic_validator=semantic_validator,
            )
        )
        try:
            Draft202012Validator(
                CATEGORY_SUGGESTION_SCHEMA, format_checker=FormatChecker()
            ).validate(output)
        except ValidationError as exc:
            raise LocalModelError("LOCAL_AI_SCHEMA_VALIDATION_FAILED") from exc
        if not semantic_validator(output):
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED")
    except LocalModelError as exc:
        error_code = str(exc).split(":", 1)[0]
        command_id = _id("cmd")
        failure_id = _id("csvf")
        failure = _event(
            "CategorySuggestionValidationFailed",
            "CategorySuggestionFailure",
            failure_id,
            1,
            command_id,
            {
                "failure_id": failure_id,
                "transaction_id": transaction_id,
                "error_code": error_code,
                "model_id": str(model.get("model_id", "unknown")),
                "model_version": str(model.get("model_version", "unknown")),
                "inference_policy_version": INFERENCE_POLICY_VERSION,
                "retrieval_policy_version": memory_context["retrieval_policy_version"],
                "memory_snapshot_version": memory_context["memory_snapshot_version"],
                "retrieved_example_count": memory_context["retrieved_count"],
                "taxonomy_version": TAXONOMY_VERSION,
                "mode": mode,
                "created_at": _now(),
            },
        )
        _append(store, "GenerateCategoryShadowSuggestion", f"category-failure:{failure_id}", [failure])
        return {"status": "FAILED", "error_code": error_code}

    suggestion_id = _id("csg")
    payload = {
        "suggestion_id": suggestion_id,
        "transaction_id": transaction_id,
        "normalized_event_id": normalized["event_id"],
        "category_code": output["category_code"],
        "raw_confidence": output["raw_confidence"],
        "confidence_kind": "MODEL_SELF_REPORTED_CONFIDENCE",
        "alternatives": output["alternatives"],
        "reason_codes": output["reason_codes"],
        "model_id": model["model_id"],
        "model_version": model["model_version"],
        "runtime_version": str(model.get("runtime_version", "unknown")),
        "structured_output_backend": str(model.get("structured_output_backend", "GBNF")),
        "inference_policy_version": str(
            model.get("inference_policy_version", INFERENCE_POLICY_VERSION)
        ),
        "retrieval_policy_version": memory_context["retrieval_policy_version"],
        "memory_snapshot_version": memory_context["memory_snapshot_version"],
        "retrieved_example_count": memory_context["retrieved_count"],
        "taxonomy_version": TAXONOMY_VERSION,
        "suggestion_schema_version": CATEGORY_SUGGESTION_VERSION,
        "mode": mode,
        "created_at": _now(),
    }
    command_id = _id("cmd")
    generated = _event(
        "CategorySuggestionGenerated",
        "CategorySuggestion",
        suggestion_id,
        1,
        command_id,
        payload,
    )
    _append(store, "GenerateCategoryShadowSuggestion", f"category-suggestion:{suggestion_id}", [generated])
    evaluate_category_suggestions(store, transaction_id)
    return {"status": "GENERATED", "suggestion": payload}


def generate_live_shadow_suggestions(
    store: LocalFinanceStore, local_model: LocalModelProvider
) -> dict[str, int]:
    if not category_shadow_status(store)["enabled"]:
        return {"generated": 0, "failed": 0, "skipped": 0}
    generated = failed = skipped = 0
    for transaction_id in _transactions(store):
        try:
            result = generate_category_shadow_suggestion(
                store, local_model, transaction_id, mode="LIVE_SHADOW"
            )
        except (StoreInvariantError, LocalModelError):
            skipped += 1
            continue
        if result["status"] == "GENERATED":
            generated += 1
        elif result["status"] == "FAILED":
            failed += 1
        else:
            skipped += 1
    return {"generated": generated, "failed": failed, "skipped": skipped}


def generate_visible_category_suggestions(
    store: LocalFinanceStore, local_model: LocalModelProvider
) -> dict[str, int]:
    generated = failed = skipped = 0
    for transaction_id in _transactions(store):
        if not _is_unresolved_for_suggestion(store, transaction_id):
            skipped += 1
            continue
        try:
            result = generate_category_shadow_suggestion(
                store, local_model, transaction_id, mode="SUGGESTION"
            )
        except (StoreInvariantError, LocalModelError):
            skipped += 1
            continue
        if result["status"] == "GENERATED":
            generated += 1
        elif result["status"] == "FAILED":
            failed += 1
        else:
            skipped += 1
    return {"generated": generated, "failed": failed, "skipped": skipped}


def _require_actionable_suggestion(
    store: LocalFinanceStore, suggestion_id: str
) -> dict[str, Any]:
    suggestion = category_suggestion_states(store).get(suggestion_id)
    if suggestion is None:
        raise StoreInvariantError("CATEGORY_SUGGESTION_NOT_FOUND")
    if suggestion["status"] == "STALE":
        raise StoreInvariantError("CATEGORY_SUGGESTION_STALE")
    if suggestion["status"] != "PENDING":
        raise StoreInvariantError("CATEGORY_SUGGESTION_NOT_PENDING")
    return suggestion


def _record_suggestion_decision(
    store: LocalFinanceStore,
    suggestion: dict[str, Any],
    *,
    event_type: str,
    status: str,
    selected_category: str | None,
) -> dict[str, Any]:
    suggestion_id = suggestion["suggestion_id"]
    command_id = _id("cmd")
    payload: dict[str, Any] = {
        "suggestion_id": suggestion_id,
        "transaction_id": suggestion["transaction_id"],
        "suggested_category": suggestion["category_code"],
        "model_id": suggestion["model_id"],
        "model_version": suggestion["model_version"],
        "raw_confidence": suggestion["raw_confidence"],
        "status": status,
        "decided_at": _now(),
    }
    if selected_category is not None:
        payload["selected_category"] = selected_category
    event = _event(
        event_type,
        "CategorySuggestion",
        suggestion_id,
        store.next_aggregate_version("CategorySuggestion", suggestion_id),
        command_id,
        payload,
    )
    _append(
        store,
        event_type,
        f"category-suggestion:decision:{suggestion_id}",
        [event],
    )
    return category_suggestion_states(store)[suggestion_id]


def confirm_category_suggestion(
    store: LocalFinanceStore, suggestion_id: str
) -> dict[str, Any]:
    existing = category_suggestion_states(store).get(suggestion_id)
    if existing and existing["status"] == "CONFIRMED":
        return existing
    suggestion = _require_actionable_suggestion(store, suggestion_id)
    current = active_classifications(store).get(suggestion["transaction_id"])
    if not current or current["payload"].get("suggestion_id") != suggestion_id:
        confirm_classification(
            store,
            suggestion["transaction_id"],
            suggestion["category_code"],
            confirmed_by="LOCAL_LLM_CONFIRMED_BY_USER",
            suggestion_id=suggestion_id,
        )
    result = _record_suggestion_decision(
        store,
        suggestion,
        event_type="CategorySuggestionConfirmed",
        status="CONFIRMED",
        selected_category=suggestion["category_code"],
    )
    evaluate_category_suggestions(store, suggestion["transaction_id"])
    return result


def correct_category_suggestion(
    store: LocalFinanceStore, suggestion_id: str, selected_category: str
) -> dict[str, Any]:
    require_category(selected_category)
    suggestion = _require_actionable_suggestion(store, suggestion_id)
    if selected_category == suggestion["category_code"]:
        return confirm_category_suggestion(store, suggestion_id)
    allowed = _direction_categories(
        store, _transactions(store)[suggestion["transaction_id"]]["direction"]
    )
    if selected_category not in allowed:
        raise StoreInvariantError("CATEGORY_SUGGESTION_CATEGORY_INVALID")
    current = active_classifications(store).get(suggestion["transaction_id"])
    if not current or current["payload"].get("suggestion_id") != suggestion_id:
        confirm_classification(
            store,
            suggestion["transaction_id"],
            selected_category,
            confirmed_by="LOCAL_LLM_CORRECTED_BY_USER",
            suggestion_id=suggestion_id,
        )
    result = _record_suggestion_decision(
        store,
        suggestion,
        event_type="CategorySuggestionCorrected",
        status="CORRECTED",
        selected_category=selected_category,
    )
    evaluate_category_suggestions(store, suggestion["transaction_id"])
    return result


def reject_category_suggestion(
    store: LocalFinanceStore, suggestion_id: str
) -> dict[str, Any]:
    existing = category_suggestion_states(store).get(suggestion_id)
    if existing and existing["status"] == "REJECTED":
        return existing
    suggestion = _require_actionable_suggestion(store, suggestion_id)
    return _record_suggestion_decision(
        store,
        suggestion,
        event_type="CategorySuggestionRejected",
        status="REJECTED",
        selected_category=None,
    )


def create_classification_rule_from_suggestion(
    store: LocalFinanceStore,
    suggestion_id: str,
    *,
    field: str = "counterparty",
) -> dict[str, Any]:
    existing = next(
        (
            event["payload"]
            for event in reversed(store.events("ClassificationRuleCreatedFromSuggestion"))
            if event["payload"]["suggestion_id"] == suggestion_id
        ),
        None,
    )
    if existing:
        return existing
    suggestion = category_suggestion_states(store).get(suggestion_id)
    if suggestion is None:
        raise StoreInvariantError("CATEGORY_SUGGESTION_NOT_FOUND")
    if suggestion["status"] not in {"CONFIRMED", "CORRECTED"}:
        raise StoreInvariantError("CATEGORY_SUGGESTION_DECISION_REQUIRED")
    if field not in {"counterparty", "normalized_description"}:
        raise StoreInvariantError("FINANCE_RULE_FIELD_UNSUPPORTED")
    transaction = _transactions(store)[suggestion["transaction_id"]]
    value = str(transaction.get(field, "")).strip()
    if not value:
        raise StoreInvariantError("CATEGORY_SUGGESTION_RULE_VALUE_MISSING")
    selected_category = suggestion.get("selected_category", suggestion["category_code"])
    rule_id = create_rule(
        store,
        field=field,
        operator="EQUALS",
        value=value,
        category_code=selected_category,
        priority=250,
    )
    command_id = _id("cmd")
    payload = {
        "suggestion_id": suggestion_id,
        "transaction_id": suggestion["transaction_id"],
        "rule_id": rule_id,
        "field": field,
        "category_code": selected_category,
        "source": "USER_DECISION_FROM_AI_SUGGESTION",
        "created_at": _now(),
    }
    event = _event(
        "ClassificationRuleCreatedFromSuggestion",
        "CategorySuggestion",
        suggestion_id,
        store.next_aggregate_version("CategorySuggestion", suggestion_id),
        command_id,
        payload,
    )
    _append(
        store,
        "CreateClassificationRuleFromSuggestion",
        f"category-suggestion:rule:{suggestion_id}",
        [event],
    )
    return payload


def _suggestion_with_context(
    store: LocalFinanceStore,
    suggestion: dict[str, Any],
    *,
    transactions: dict[str, dict[str, Any]] | None = None,
    evaluation_summaries: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    transaction_rows = _transactions(store) if transactions is None else transactions
    transaction = transaction_rows.get(suggestion["transaction_id"], {})
    threshold = next(
        (
            key
            for key in ("0.99", "0.97", "0.95", "0.90")
            if float(suggestion["raw_confidence"]) >= float(key)
        ),
        None,
    )
    metric = None
    if threshold:
        summary_key = (
            str(suggestion["model_id"]),
            str(suggestion["model_version"]),
            str(suggestion["inference_policy_version"]),
            str(suggestion["taxonomy_version"]),
        )
        if evaluation_summaries is not None and summary_key in evaluation_summaries:
            summary = evaluation_summaries[summary_key]
        else:
            summary = category_evaluation_summary(
                store,
                {
                    "model_id": summary_key[0],
                    "model_version": summary_key[1],
                    "policy_version": summary_key[2],
                    "taxonomy_version": summary_key[3],
                },
            )
            if evaluation_summaries is not None:
                evaluation_summaries[summary_key] = summary
        candidate = summary["thresholds"][threshold]
        if candidate["status"] == "READY":
            metric = {"threshold": threshold, **candidate}
    return {
        **suggestion,
        "account_id": transaction.get("account_id"),
        "booking_date": transaction.get("booking_date"),
        "merchant": transaction.get("counterparty", ""),
        "description": transaction.get("normalized_description", ""),
        "amount": transaction.get("amount"),
        "currency": transaction.get("currency"),
        "historical_accuracy": metric,
    }


def list_category_suggestions(
    store: LocalFinanceStore, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    filters = filters or {}
    transactions = _transactions(store)
    evaluation_summaries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    rows = [
        _suggestion_with_context(
            store,
            item,
            transactions=transactions,
            evaluation_summaries=evaluation_summaries,
        )
        for item in category_suggestion_states(store).values()
    ]
    statuses = filters.get("status")
    if isinstance(statuses, str):
        statuses = {statuses}
    elif isinstance(statuses, list):
        statuses = set(statuses)
    for key in ("account_id", "category_code"):
        if filters.get(key):
            rows = [row for row in rows if row.get(key) == filters[key]]
    if statuses:
        rows = [row for row in rows if row["status"] in statuses]
    if filters.get("period"):
        rows = [
            row
            for row in rows
            if str(row.get("booking_date", "")).startswith(str(filters["period"]))
        ]
    if filters.get("confidence_min") is not None:
        rows = [
            row
            for row in rows
            if float(row["raw_confidence"]) >= float(filters["confidence_min"])
        ]
    if filters.get("confidence_max") is not None:
        rows = [
            row
            for row in rows
            if float(row["raw_confidence"]) <= float(filters["confidence_max"])
        ]
    return sorted(
        rows,
        key=lambda row: (
            row["status"] != "PENDING",
            -float(row["raw_confidence"]),
            -abs(float(row.get("amount") or 0)),
            str(row.get("booking_date", "")),
        ),
    )


def get_transaction_category_suggestion(
    store: LocalFinanceStore, transaction_id: str
) -> dict[str, Any] | None:
    rows = [
        row
        for row in list_category_suggestions(store)
        if row["transaction_id"] == transaction_id
    ]
    return rows[0] if rows else None


def category_suggestion_review_summary(store: LocalFinanceStore) -> dict[str, Any]:
    rows = list(category_suggestion_states(store).values())
    today = datetime.now(UTC).date().isoformat()
    classifications = active_classifications(store)
    unclassified = sum(
        1
        for transaction_id in _transactions(store)
        if not classifications.get(transaction_id)
        or classifications[transaction_id]["event_type"]
        != "TransactionClassificationConfirmed"
    )
    return {
        "unclassified_count": unclassified,
        "pending_count": sum(1 for row in rows if row["status"] == "PENDING"),
        "confirmed_today_count": sum(
            1
            for row in rows
            if row["status"] == "CONFIRMED"
            and str(row.get("decided_at", "")).startswith(today)
        ),
        "corrected_count": sum(1 for row in rows if row["status"] == "CORRECTED"),
        "stale_count": sum(1 for row in rows if row["status"] == "STALE"),
        "automatic_acceptance": False,
    }


def list_classification_rules_with_origin(store: LocalFinanceStore) -> list[dict[str, Any]]:
    origins = {
        event["payload"]["rule_id"]: event["payload"]["suggestion_id"]
        for event in store.events("ClassificationRuleCreatedFromSuggestion")
    }
    latest: dict[str, dict[str, Any]] = {}
    for event in store.events("ClassificationRuleCreated"):
        latest[event["payload"]["rule_id"]] = event["payload"]
    return [
        {
            **payload,
            "source": (
                "USER_FROM_AI_SUGGESTION"
                if rule_id in origins
                else "USER"
            ),
            "suggestion_id": origins.get(rule_id),
        }
        for rule_id, payload in latest.items()
    ]


def _ground_truth(store: LocalFinanceStore, transaction_id: str) -> dict[str, Any] | None:
    classification = active_classifications(store).get(transaction_id)
    if (
        classification
        and classification["event_type"] == "TransactionClassificationConfirmed"
        and classification["payload"].get("confirmed_by")
        in {
            "USER",
            "USER_CONFIRMED",
            "USER_CORRECTED",
            "LOCAL_LLM_CONFIRMED_BY_USER",
            "LOCAL_LLM_CORRECTED_BY_USER",
        }
    ):
        return classification
    return None


def _active_evaluations(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    evaluations: dict[str, dict[str, Any]] = {}
    superseded: set[str] = set()
    for event in store.events():
        if event["event_type"] == "CategorySuggestionEvaluated":
            evaluations[event["payload"]["suggestion_id"]] = event["payload"]
        elif event["event_type"] == "CategorySuggestionEvaluationSuperseded":
            superseded.add(event["payload"]["evaluation_id"])
    return {
        suggestion_id: payload
        for suggestion_id, payload in evaluations.items()
        if payload["evaluation_id"] not in superseded
    }


def evaluate_category_suggestions(
    store: LocalFinanceStore, transaction_id: str
) -> int:
    truth = _ground_truth(store, transaction_id)
    if truth is None:
        return 0
    final_category = truth["payload"]["category_code"]
    confirmed_events = [
        event
        for event in store.events("TransactionClassificationConfirmed")
        if event["payload"]["transaction_id"] == transaction_id
    ]
    ground_truth_source = (
        "USER_CORRECTED"
        if len(confirmed_events) > 1
        or truth["payload"].get("confirmed_by") == "LOCAL_LLM_CORRECTED_BY_USER"
        else "USER_CONFIRMED"
    )
    suggestions = [
        event["payload"]
        for event in store.events("CategorySuggestionGenerated")
        if event["payload"]["transaction_id"] == transaction_id
    ]
    active = _active_evaluations(store)
    created = 0
    for suggestion in suggestions:
        previous = active.get(suggestion["suggestion_id"])
        if previous and previous["final_category"] == final_category:
            continue
        command_id = _id("cmd")
        events: list[dict[str, Any]] = []
        if previous:
            events.append(
                _event(
                    "CategorySuggestionEvaluationSuperseded",
                    "ClassificationEvaluation",
                    previous["evaluation_id"],
                    store.next_aggregate_version(
                        "ClassificationEvaluation", previous["evaluation_id"]
                    ),
                    command_id,
                    {
                        "evaluation_id": previous["evaluation_id"],
                        "suggestion_id": suggestion["suggestion_id"],
                        "transaction_id": transaction_id,
                        "status": "SUPERSEDED",
                        "superseded_at": _now(),
                    },
                )
            )
        evaluation_id = _id("cev")
        evaluation = {
            "evaluation_id": evaluation_id,
            "suggestion_id": suggestion["suggestion_id"],
            "transaction_id": transaction_id,
            "predicted_category": suggestion["category_code"],
            "final_category": final_category,
            "raw_confidence": suggestion["raw_confidence"],
            "correct": suggestion["category_code"] == final_category,
            "ground_truth_source": ground_truth_source,
            "evaluated_at": _now(),
            "model_id": suggestion["model_id"],
            "model_version": suggestion["model_version"],
            "policy_version": suggestion["inference_policy_version"],
            "retrieval_policy_version": suggestion.get(
                "retrieval_policy_version", "NONE"
            ),
            "memory_snapshot_version": suggestion.get("memory_snapshot_version"),
            "retrieved_example_count": int(
                suggestion.get("retrieved_example_count", 0)
            ),
            "taxonomy_version": suggestion["taxonomy_version"],
            "evaluation_schema_version": CATEGORY_EVALUATION_VERSION,
            "status": "ACTIVE",
        }
        events.append(
            _event(
                "CategorySuggestionEvaluated",
                "ClassificationEvaluation",
                evaluation_id,
                1,
                command_id,
                evaluation,
            )
        )
        _append(
            store,
            "EvaluateCategorySuggestion",
            f"category-evaluation:{suggestion['suggestion_id']}:{truth['event_id']}",
            events,
        )
        created += 1
    return created


def _latest_runs(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"].startswith("CategoryEvaluationRun"):
            payload = event["payload"]
            run = runs.setdefault(payload["run_id"], {})
            run.update(payload)
            run["last_event_type"] = event["event_type"]
    active_in_process = getattr(store, "_category_evaluation_active_runs", set())
    for run_id, run in runs.items():
        if run.get("status") == "RUNNING" and run_id not in active_in_process:
            run["status"] = "INTERRUPTED"
    return runs


def _mark_run_active(store: LocalFinanceStore, run_id: str, active: bool) -> None:
    active_runs = getattr(store, "_category_evaluation_active_runs", None)
    if active_runs is None:
        active_runs = set()
        setattr(store, "_category_evaluation_active_runs", active_runs)
    if active:
        active_runs.add(run_id)
    else:
        active_runs.discard(run_id)


def _eligible_replay_ids(store: LocalFinanceStore) -> list[str]:
    confirmed = active_classifications(store)
    return sorted(
        transaction_id
        for transaction_id, event in confirmed.items()
        if event["event_type"] == "TransactionClassificationConfirmed"
        and event["payload"].get("confirmed_by")
        in {
            "USER",
            "USER_CONFIRMED",
            "USER_CORRECTED",
            "LOCAL_LLM_CONFIRMED_BY_USER",
            "LOCAL_LLM_CORRECTED_BY_USER",
        }
        and transaction_eligibility(store, transaction_id)["eligible"]
    )


def preview_category_evaluation_run(store: LocalFinanceStore) -> dict[str, Any]:
    eligible = _eligible_replay_ids(store)
    return {
        "eligible_transaction_count": len(eligible),
        "processing_location": "LOCAL_DEVICE",
        "network_egress": "DENY",
        "max_concurrent_inferences": MAX_CONCURRENT_INFERENCES,
    }


def _append_run_state(
    store: LocalFinanceStore,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    key: str,
) -> None:
    command_id = _id("cmd")
    event = _event(
        event_type,
        "CategoryEvaluationRun",
        run_id,
        store.next_aggregate_version("CategoryEvaluationRun", run_id),
        command_id,
        {"run_id": run_id, **payload},
    )
    _append(store, event_type, key, [event])


def start_category_evaluation_run(
    store: LocalFinanceStore,
    local_model: LocalModelProvider,
    *,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    process: bool = True,
) -> dict[str, Any]:
    if not category_shadow_status(store)["enabled"]:
        raise StoreInvariantError("CATEGORY_SHADOW_DISABLED")
    if any(run["status"] in {"RUNNING", "INTERRUPTED", "PAUSED"} for run in _latest_runs(store).values()):
        raise StoreInvariantError("CATEGORY_EVALUATION_ALREADY_RUNNING")
    status = local_model.status()
    if not status.get("available") or not status.get("category_suggestions"):
        raise LocalModelError("LOCAL_AI_UNAVAILABLE")
    model = status["model"]
    eligible = _eligible_replay_ids(store)[:max_batch_size]
    run_id = _id("cer")
    _append_run_state(
        store,
        run_id,
        "CategoryEvaluationRunStarted",
        {
            "status": "RUNNING",
            "mode": "EVALUATION_REPLAY",
            "total": len(eligible),
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "started_at": _now(),
            "last_processed_transaction_id": None,
            "transaction_ids": eligible,
            "model_id": model["model_id"],
            "model_version": model["model_version"],
            "policy_version": str(model.get("inference_policy_version", INFERENCE_POLICY_VERSION)),
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
            "memory_snapshot_version": memory_fingerprint(store),
            "taxonomy_version": TAXONOMY_VERSION,
        },
        key=f"category-run:start:{run_id}",
    )
    _mark_run_active(store, run_id, True)
    if process:
        return process_category_evaluation_run(store, local_model, run_id)
    return get_category_evaluation_run(store, run_id)


def process_category_evaluation_run(
    store: LocalFinanceStore, local_model: LocalModelProvider, run_id: str
) -> dict[str, Any]:
    run = _latest_runs(store).get(run_id)
    if run is None:
        raise StoreInvariantError("CATEGORY_EVALUATION_NOT_FOUND")
    status = local_model.status()
    model = status.get("model") or {}
    if (
        not status.get("available")
        or model.get("model_id") != run["model_id"]
        or model.get("model_version") != run["model_version"]
        or str(model.get("inference_policy_version", INFERENCE_POLICY_VERSION)) != run["policy_version"]
        or run.get("retrieval_policy_version", "NONE") != RETRIEVAL_POLICY_VERSION
    ):
        raise StoreInvariantError("CATEGORY_EVALUATION_INCOMPATIBLE_MODEL")
    transaction_ids = run.get("transaction_ids", [])
    processed = int(run.get("processed", 0))
    succeeded = int(run.get("succeeded", 0))
    failed = int(run.get("failed", 0))
    skipped = int(run.get("skipped", 0))
    for transaction_id in transaction_ids[processed:]:
        result = generate_category_shadow_suggestion(
            store, local_model, transaction_id, mode="EVALUATION_REPLAY"
        )
        processed += 1
        if result["status"] == "GENERATED":
            succeeded += 1
        elif result["status"] == "FAILED":
            failed += 1
        else:
            skipped += 1
        _append_run_state(
            store,
            run_id,
            "CategoryEvaluationRunProgressed",
            {
                "status": "RUNNING",
                "total": len(transaction_ids),
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "last_processed_transaction_id": transaction_id,
            },
            key=f"category-run:progress:{run_id}:{processed}",
        )
    _append_run_state(
        store,
        run_id,
        "CategoryEvaluationRunCompleted",
        {
            "status": "COMPLETED",
            "total": len(transaction_ids),
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "last_processed_transaction_id": transaction_ids[-1] if transaction_ids else None,
            "completed_at": _now(),
        },
        key=f"category-run:complete:{run_id}",
    )
    _mark_run_active(store, run_id, False)
    return get_category_evaluation_run(store, run_id)


def pause_category_evaluation_run(store: LocalFinanceStore, run_id: str) -> dict[str, Any]:
    run = _latest_runs(store).get(run_id)
    if run is None:
        raise StoreInvariantError("CATEGORY_EVALUATION_NOT_FOUND")
    if run["status"] not in {"RUNNING", "INTERRUPTED"}:
        return run
    _append_run_state(store, run_id, "CategoryEvaluationRunPaused", {"status": "PAUSED", "paused_at": _now()}, key=f"category-run:pause:{run_id}:{run.get('processed', 0)}")
    _mark_run_active(store, run_id, False)
    return get_category_evaluation_run(store, run_id)


def resume_category_evaluation_run(
    store: LocalFinanceStore, local_model: LocalModelProvider, run_id: str
) -> dict[str, Any]:
    run = _latest_runs(store).get(run_id)
    if run is None:
        raise StoreInvariantError("CATEGORY_EVALUATION_NOT_FOUND")
    if run["status"] not in {"PAUSED", "INTERRUPTED"}:
        return run
    _append_run_state(store, run_id, "CategoryEvaluationRunResumed", {"status": "RUNNING", "resumed_at": _now()}, key=f"category-run:resume:{run_id}:{run.get('processed', 0)}")
    _mark_run_active(store, run_id, True)
    return process_category_evaluation_run(store, local_model, run_id)


def cancel_category_evaluation_run(store: LocalFinanceStore, run_id: str) -> dict[str, Any]:
    run = _latest_runs(store).get(run_id)
    if run is None:
        raise StoreInvariantError("CATEGORY_EVALUATION_NOT_FOUND")
    if run["status"] in {"COMPLETED", "CANCELLED"}:
        return run
    _append_run_state(store, run_id, "CategoryEvaluationRunCancelled", {"status": "CANCELLED", "cancelled_at": _now()}, key=f"category-run:cancel:{run_id}")
    _mark_run_active(store, run_id, False)
    return get_category_evaluation_run(store, run_id)


def get_category_evaluation_run(store: LocalFinanceStore, run_id: str) -> dict[str, Any]:
    run = _latest_runs(store).get(run_id)
    if run is None:
        raise StoreInvariantError("CATEGORY_EVALUATION_NOT_FOUND")
    return run


def list_category_evaluation_runs(store: LocalFinanceStore) -> list[dict[str, Any]]:
    return sorted(_latest_runs(store).values(), key=lambda item: item.get("started_at", ""), reverse=True)


def get_category_suggestion_detail(
    store: LocalFinanceStore, suggestion_id: str
) -> dict[str, Any] | None:
    suggestion = category_suggestion_states(store).get(suggestion_id)
    if suggestion is None:
        suggestion = next(
            (
                event["payload"]
                for event in store.events("CategorySuggestionGenerated")
                if event["payload"]["suggestion_id"] == suggestion_id
            ),
            None,
        )
    if suggestion is None:
        return None
    evaluation = _active_evaluations(store).get(suggestion_id)
    return {**_suggestion_with_context(store, suggestion), "evaluation": evaluation}


def _metric(value: int, correct: int) -> dict[str, Any]:
    sufficient = value >= MINIMUM_METRIC_SAMPLE
    return {
        "sample_count": value,
        "accuracy": round(correct / value, 4) if sufficient and value else None,
        "status": "READY" if sufficient else "INSUFFICIENT_DATA",
    }


def _calibration_group(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(item.get("model_id", "unknown")),
        str(item.get("model_version", "unknown")),
        str(item.get("policy_version", item.get("inference_policy_version", "unknown"))),
        str(item.get("retrieval_policy_version", "NONE")),
        str(item.get("taxonomy_version", "unknown")),
    )


def _macro_precision(evaluations: list[dict[str, Any]]) -> float | None:
    if len(evaluations) < MINIMUM_METRIC_SAMPLE:
        return None
    predicted_categories = sorted({item["predicted_category"] for item in evaluations})
    values = []
    for category in predicted_categories:
        predicted = [item for item in evaluations if item["predicted_category"] == category]
        true_positive = sum(1 for item in predicted if item["final_category"] == category)
        values.append(true_positive / len(predicted))
    return round(sum(values) / len(values), 4) if values else None


def _cohort_metrics(
    suggestions: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    corrected_suggestion_ids: set[str],
    decided_suggestion_ids: set[str],
) -> dict[str, Any]:
    evaluated_count = len(evaluations)
    correct = sum(1 for item in evaluations if item["correct"])
    suggestion_ids = {item["suggestion_id"] for item in suggestions}
    decisions = suggestion_ids & decided_suggestion_ids
    return {
        "sample_count": evaluated_count,
        "accuracy": (
            round(correct / evaluated_count, 4)
            if evaluated_count >= MINIMUM_METRIC_SAMPLE
            else None
        ),
        "precision": _macro_precision(evaluations),
        "llm_calls": len(suggestions) + len(failures),
        "user_correction_rate": (
            round(len(suggestion_ids & corrected_suggestion_ids) / len(decisions), 4)
            if decisions
            else None
        ),
        "status": "READY" if evaluated_count >= MINIMUM_METRIC_SAMPLE else "INSUFFICIENT_DATA",
    }


def category_evaluation_summary(
    store: LocalFinanceStore, filters: dict[str, str] | None = None
) -> dict[str, Any]:
    filters = filters or {}
    all_suggestions = [
        event["payload"] for event in store.events("CategorySuggestionGenerated")
    ]
    all_evaluations = list(_active_evaluations(store).values())
    all_failures = [
        event["payload"] for event in store.events("CategorySuggestionValidationFailed")
    ]

    def matches(item: dict[str, Any]) -> bool:
        mapping = {
            "model_id": item.get("model_id"),
            "model_version": item.get("model_version"),
            "policy_version": item.get("policy_version", item.get("inference_policy_version")),
            "inference_policy_version": item.get(
                "policy_version", item.get("inference_policy_version")
            ),
            "retrieval_policy_version": item.get("retrieval_policy_version", "NONE"),
            "taxonomy_version": item.get("taxonomy_version"),
        }
        return all(not value or mapping[key] == value for key, value in filters.items() if key in mapping)

    filtered_suggestions = [item for item in all_suggestions if matches(item)]
    filtered_evaluations = [item for item in all_evaluations if matches(item)]
    filtered_failures = [item for item in all_failures if matches(item)]

    group_keys = sorted(
        {
            _calibration_group(item)
            for item in [
                *filtered_suggestions,
                *filtered_evaluations,
                *filtered_failures,
            ]
        }
    )
    current_suggestions = [
        item
        for item in filtered_suggestions
        if _calibration_group(item)[2] == INFERENCE_POLICY_VERSION
        and _calibration_group(item)[3] == RETRIEVAL_POLICY_VERSION
        and _calibration_group(item)[4] == TAXONOMY_VERSION
    ]
    active_group = (
        _calibration_group(current_suggestions[-1])
        if current_suggestions
        else _calibration_group(filtered_suggestions[-1])
        if filtered_suggestions
        else group_keys[-1]
        if group_keys
        else None
    )
    suggestions = [
        item for item in filtered_suggestions if _calibration_group(item) == active_group
    ]
    evaluations = [
        item for item in filtered_evaluations if _calibration_group(item) == active_group
    ]
    failures = [
        item for item in filtered_failures if _calibration_group(item) == active_group
    ]

    decision_events = [
        event
        for event in store.events()
        if event["event_type"]
        in {"CategorySuggestionConfirmed", "CategorySuggestionCorrected"}
    ]
    decided_suggestion_ids = {
        event["payload"]["suggestion_id"] for event in decision_events
    }
    corrected_suggestion_ids = {
        event["payload"]["suggestion_id"]
        for event in decision_events
        if event["event_type"] == "CategorySuggestionCorrected"
    }

    calibration_groups = []
    for key in group_keys:
        group_suggestions = [
            item for item in filtered_suggestions if _calibration_group(item) == key
        ]
        group_evaluations = [
            item for item in filtered_evaluations if _calibration_group(item) == key
        ]
        group_failures = [
            item for item in filtered_failures if _calibration_group(item) == key
        ]
        metrics = _cohort_metrics(
            group_suggestions,
            group_evaluations,
            group_failures,
            corrected_suggestion_ids,
            decided_suggestion_ids,
        )
        calibration_groups.append(
            {
                "model_id": key[0],
                "model_version": key[1],
                "inference_policy_version": key[2],
                "retrieval_policy_version": key[3],
                "taxonomy_version": key[4],
                "memory_mode": (
                    "WITH_CLASSIFICATION_MEMORY"
                    if key[3] != "NONE"
                    else "WITHOUT_CLASSIFICATION_MEMORY"
                ),
                "historical": (
                    key[2] != INFERENCE_POLICY_VERSION
                    or key[3] != RETRIEVAL_POLICY_VERSION
                ),
                **metrics,
            }
        )

    # Compare actual context use only inside the active exact calibration group.
    # Historical policies remain visible below, but never get blended into the
    # current confidence measurements.
    with_memory_ids = {
        item["suggestion_id"]
        for item in suggestions
        if int(item.get("retrieved_example_count", 0)) > 0
    }
    without_memory_ids = {
        item["suggestion_id"]
        for item in suggestions
        if int(item.get("retrieved_example_count", 0)) == 0
    }
    with_memory = _cohort_metrics(
        [item for item in suggestions if item["suggestion_id"] in with_memory_ids],
        [item for item in evaluations if item["suggestion_id"] in with_memory_ids],
        [item for item in failures if int(item.get("retrieved_example_count", 0)) > 0],
        corrected_suggestion_ids,
        decided_suggestion_ids,
    )
    without_memory = _cohort_metrics(
        [item for item in suggestions if item["suggestion_id"] in without_memory_ids],
        [item for item in evaluations if item["suggestion_id"] in without_memory_ids],
        [item for item in failures if int(item.get("retrieved_example_count", 0)) == 0],
        corrected_suggestion_ids,
        decided_suggestion_ids,
    )

    active_classification_rows = list(active_classifications(store).values())
    memory_matches = sum(
        1
        for item in active_classification_rows
        if item["event_type"] == "TransactionClassificationProposed"
        and item["payload"].get("classification_source")
        == "LOCAL_CLASSIFICATION_MEMORY"
        and item["payload"].get("memory_match", {}).get("status") == "MATCHED"
        and item["payload"].get("memory_match", {}).get("policy_version")
        == MEMORY_POLICY_VERSION
    )
    memory_conflicts = sum(
        1
        for item in active_classification_rows
        if item["event_type"] == "TransactionClassificationProposed"
        and item["payload"].get("classification_source")
        == "LOCAL_CLASSIFICATION_MEMORY"
        and item["payload"].get("memory_match", {}).get("status") == "AMBIGUOUS"
        and item["payload"].get("memory_match", {}).get("policy_version")
        == MEMORY_POLICY_VERSION
    )
    correct = sum(1 for item in evaluations if item["correct"])
    thresholds = {}
    for threshold in (0.90, 0.95, 0.97, 0.99):
        selected = [item for item in evaluations if float(item["raw_confidence"]) >= threshold]
        thresholds[f"{threshold:.2f}"] = _metric(
            len(selected), sum(1 for item in selected if item["correct"])
        )
    categories: dict[str, dict[str, Any]] = {}
    for code in _active_categories(store):
        predicted = [item for item in evaluations if item["predicted_category"] == code]
        actual = [item for item in evaluations if item["final_category"] == code]
        true_positive = sum(
            1
            for item in evaluations
            if item["predicted_category"] == code and item["final_category"] == code
        )
        sample_count = max(len(predicted), len(actual))
        sufficient = sample_count >= MINIMUM_METRIC_SAMPLE
        categories[code] = {
            "category_code": code,
            "cases": sample_count,
            "precision": round(true_positive / len(predicted), 4) if sufficient and predicted else None,
            "recall": round(true_positive / len(actual), 4) if sufficient and actual else None,
            "status": "READY" if sufficient else "INSUFFICIENT_DATA",
        }
    total_outputs = len(suggestions) + len(failures)
    confidence_distribution = {
        "LT_090": sum(1 for item in evaluations if float(item["raw_confidence"]) < 0.90),
        "090_095": sum(
            1 for item in evaluations if 0.90 <= float(item["raw_confidence"]) < 0.95
        ),
        "095_097": sum(
            1 for item in evaluations if 0.95 <= float(item["raw_confidence"]) < 0.97
        ),
        "097_099": sum(
            1 for item in evaluations if 0.97 <= float(item["raw_confidence"]) < 0.99
        ),
        "GTE_099": sum(1 for item in evaluations if float(item["raw_confidence"]) >= 0.99),
    }
    return {
        "prediction_count": len(suggestions),
        "evaluated_count": len(evaluations),
        "pending_ground_truth_count": max(0, len(suggestions) - len(evaluations)),
        "correct_count": correct,
        "incorrect_count": len(evaluations) - correct,
        "overall_accuracy": (
            round(correct / len(evaluations), 4)
            if len(evaluations) >= MINIMUM_METRIC_SAMPLE
            else None
        ),
        "overall_status": "READY" if len(evaluations) >= MINIMUM_METRIC_SAMPLE else "INSUFFICIENT_DATA",
        "coverage": round(len(evaluations) / len(suggestions), 4) if suggestions else 0,
        "invalid_output_rate": round(len(failures) / total_outputs, 4) if total_outputs else 0,
        "timeout_rate": round(
            sum(1 for item in failures if item["error_code"] == "LOCAL_AI_TIMEOUT") / total_outputs,
            4,
        ) if total_outputs else 0,
        "thresholds": thresholds,
        "categories": list(categories.values()),
        "confidence_distribution": confidence_distribution,
        "confidence_kind": "MODEL_SELF_REPORTED_CONFIDENCE",
        "minimum_sample": MINIMUM_METRIC_SAMPLE,
        "filters": filters,
        "active_calibration_group": (
            {
                "model_id": active_group[0],
                "model_version": active_group[1],
                "inference_policy_version": active_group[2],
                "retrieval_policy_version": active_group[3],
                "taxonomy_version": active_group[4],
            }
            if active_group
            else None
        ),
        "calibration_groups": calibration_groups,
        "comparison": {
            "accuracy_without_memory": without_memory["accuracy"],
            "accuracy_with_memory": with_memory["accuracy"],
            "precision_without_memory": without_memory["precision"],
            "precision_with_memory": with_memory["precision"],
            "llm_calls_without_memory": without_memory["llm_calls"],
            "llm_calls_with_memory": with_memory["llm_calls"],
            "user_correction_rate_without_memory": without_memory[
                "user_correction_rate"
            ],
            "user_correction_rate_with_memory": with_memory[
                "user_correction_rate"
            ],
            "user_correction_rate": (
                round(
                    len(corrected_suggestion_ids & decided_suggestion_ids & {
                        item["suggestion_id"] for item in suggestions
                    })
                    / len(decided_suggestion_ids & {
                        item["suggestion_id"] for item in suggestions
                    }),
                    4,
                )
                if decided_suggestion_ids
                & {item["suggestion_id"] for item in suggestions}
                else None
            ),
            "memory_hit_rate": (
                round(
                    memory_matches
                    / (
                        memory_matches
                        + len(suggestions)
                        + len(failures)
                    ),
                    4,
                )
                if memory_matches + len(suggestions) + len(failures)
                else 0
            ),
            "memory_conflict_rate": (
                round(memory_conflicts / (memory_matches + memory_conflicts), 4)
                if memory_matches + memory_conflicts
                else 0
            ),
            "without_memory_sample_count": without_memory["sample_count"],
            "with_memory_sample_count": with_memory["sample_count"],
        },
    }


__all__ = [
    "CATEGORY_EVALUATION_VERSION",
    "CATEGORY_SUGGESTION_SCHEMA",
    "CATEGORY_SUGGESTION_VERSION",
    "MAX_CONCURRENT_INFERENCES",
    "REASON_CODES",
    "TAXONOMY_VERSION",
    "cancel_category_evaluation_run",
    "category_suggestion_review_summary",
    "category_suggestion_states",
    "category_evaluation_summary",
    "category_shadow_status",
    "confirm_category_suggestion",
    "correct_category_suggestion",
    "create_classification_rule_from_suggestion",
    "disable_category_shadow_mode",
    "enable_category_shadow_mode",
    "evaluate_category_suggestions",
    "generate_category_shadow_suggestion",
    "generate_live_shadow_suggestions",
    "generate_visible_category_suggestions",
    "get_category_evaluation_run",
    "get_category_suggestion_detail",
    "get_transaction_category_suggestion",
    "list_category_evaluation_runs",
    "list_category_suggestions",
    "list_classification_rules_with_origin",
    "pause_category_evaluation_run",
    "preview_category_evaluation_run",
    "process_category_evaluation_run",
    "resume_category_evaluation_run",
    "reject_category_suggestion",
    "start_category_evaluation_run",
    "transaction_eligibility",
    "validate_category_suggestion",
]
