"""Event-sourced local experience for deterministic classification suggestions.

Classification memory is deliberately weaker than every explicit rule.  It is
derived only from user-confirmed decisions and never mutates the local model.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .store import LocalFinanceStore

MEMORY_POLICY_VERSION = "classification-memory@1.0.0"
RETRIEVAL_POLICY_VERSION = "classification-memory-retrieval@1.0.0"
MAX_CONFLICT_RATE = 0.20

_MERCHANT_NOISE = frozenset(
    {
        "AG",
        "CO",
        "CITY",
        "DANKE",
        "DE",
        "DEUTSCHLAND",
        "FILIALE",
        "GMBH",
        "KG",
        "LTD",
        "MARKT",
        "SAGT",
        "SE",
    }
)
_DESCRIPTION_NOISE = frozenset(
    {
        "EC",
        "FOLGENR",
        "KARTE",
        "KARTENZAHLUNG",
        "LASTSCHRIFT",
        "MANDAT",
        "NR",
        "SEPA",
        "VISA",
    }
)
_MERCHANT_ALIASES = {
    "AMZN": "AMAZON",
    "AMAZON EU": "AMAZON",
    "REWE CITY": "REWE",
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).upper()
    return [token for token in re.findall(r"[A-ZÄÖÜ0-9]+", normalized) if token]


def normalize_merchant(value: str) -> str:
    """Return a conservative merchant identity without volatile branch data."""

    raw = " ".join(_tokens(value))
    if raw in _MERCHANT_ALIASES:
        return _MERCHANT_ALIASES[raw]
    stable = [
        token
        for token in raw.split()
        if token not in _MERCHANT_NOISE and not token.isdigit()
    ]
    if not stable:
        return raw
    candidate = " ".join(stable[:4])
    return _MERCHANT_ALIASES.get(candidate, candidate)


def description_signature(value: str) -> str:
    """Remove volatile references while retaining deterministic wording."""

    stable = [
        token
        for token in _tokens(value)
        if token not in _DESCRIPTION_NOISE
        and not token.isdigit()
        and not re.fullmatch(r"[A-Z0-9]{10,}", token)
    ]
    return " ".join(stable[:8])


def _amount_signature(value: Any) -> str:
    try:
        return str(abs(Decimal(str(value))).quantize(Decimal("0.01")))
    except InvalidOperation:
        return ""


def _booking_day(value: Any) -> int | None:
    try:
        return int(str(value).rsplit("-", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None


def _event(
    event_type: str,
    aggregate_id: str,
    aggregate_version: int,
    command_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": event_type,
        "aggregate_type": "ClassificationMemory",
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "occurred_at": _now(),
        "correlation_id": command_id,
        "causation_id": command_id,
        "payload": payload,
    }


def active_memory_entries(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"] == "ClassificationMemoryRecorded":
            active[event["payload"]["memory_id"]] = event["payload"]
        elif event["event_type"] == "ClassificationMemorySuperseded":
            active.pop(event["payload"]["memory_id"], None)
    return active


def memory_fingerprint(
    store: LocalFinanceStore,
    entries: dict[str, dict[str, Any]] | None = None,
) -> str:
    entries = entries if entries is not None else active_memory_entries(store)
    stable = [
        {
            key: entry.get(key)
            for key in (
                "memory_id",
                "normalized_merchant",
                "description_signature",
                "direction",
                "category_code",
                "amount_signature",
                "booking_day",
            )
        }
        for entry in sorted(entries.values(), key=lambda item: item["memory_id"])
    ]
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_memory_events(
    store: LocalFinanceStore,
    *,
    normalized_event: dict[str, Any],
    classification_event: dict[str, Any],
    command_id: str,
    confirmation_type: str,
) -> list[dict[str, Any]]:
    """Build the memory lifecycle events appended with a user decision."""

    transaction = normalized_event["payload"]
    category_code = classification_event["payload"]["category_code"]
    merchant = normalize_merchant(str(transaction.get("counterparty", "")))
    signature = description_signature(str(transaction.get("normalized_description", "")))
    if not merchant and not signature:
        return []

    active = active_memory_entries(store)
    source_transaction_id = transaction["transaction_id"]
    previous = [
        item
        for item in active.values()
        if item["source_transaction_id"] == source_transaction_id
    ]
    if any(
        item["category_code"] == category_code
        and item["normalized_merchant"] == merchant
        and item["description_signature"] == signature
        and item["direction"] == transaction["direction"]
        for item in previous
    ):
        return []

    memory_id = _id("mem")
    events = [
        _event(
            "ClassificationMemorySuperseded",
            item["memory_id"],
            store.next_aggregate_version("ClassificationMemory", item["memory_id"]),
            command_id,
            {
                "memory_id": item["memory_id"],
                "source_transaction_id": source_transaction_id,
                "superseded_by_memory_id": memory_id,
                "reason": "USER_CORRECTION",
                "superseded_at": _now(),
            },
        )
        for item in previous
    ]
    events.append(
        _event(
            "ClassificationMemoryRecorded",
            memory_id,
            1,
            command_id,
            {
                "memory_id": memory_id,
                "normalized_merchant": merchant,
                "description_signature": signature,
                "direction": transaction["direction"],
                "category_code": category_code,
                "amount_signature": _amount_signature(transaction.get("amount")),
                "booking_day": _booking_day(transaction.get("booking_date")),
                "source_transaction_id": source_transaction_id,
                "source_classification_id": classification_event["event_id"],
                "confirmation_type": confirmation_type,
                "policy_version": MEMORY_POLICY_VERSION,
                "created_at": classification_event["occurred_at"],
            },
        )
    )
    return events


def _resolve_candidates(
    candidates: list[dict[str, Any]], *, match_type: str
) -> dict[str, Any]:
    counts = Counter(item["category_code"] for item in candidates)
    if not counts:
        return {"status": "NONE"}
    ordered = counts.most_common()
    dominant, support_count = ordered[0]
    conflict_count = sum(counts.values()) - support_count
    tied = len(ordered) > 1 and ordered[1][1] == support_count
    conflict_rate = conflict_count / sum(counts.values())
    status = "AMBIGUOUS" if tied or conflict_rate > MAX_CONFLICT_RATE else "MATCHED"
    return {
        "status": status,
        "category_code": dominant if status == "MATCHED" else None,
        "support_count": support_count,
        "conflict_count": conflict_count,
        "conflict_rate": round(conflict_rate, 4),
        "match_type": match_type,
        "memory_ids": sorted(item["memory_id"] for item in candidates),
        "policy_version": MEMORY_POLICY_VERSION,
    }


def match_classification_memory(
    store: LocalFinanceStore,
    transaction: dict[str, Any],
    *,
    entries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    memory_entries = list(
        (entries if entries is not None else active_memory_entries(store)).values()
    )
    direction = transaction.get("direction")
    merchant = normalize_merchant(str(transaction.get("counterparty", "")))
    signature = description_signature(str(transaction.get("normalized_description", "")))
    amount = _amount_signature(transaction.get("amount"))
    day = _booking_day(transaction.get("booking_date"))

    merchant_matches = [
        item
        for item in memory_entries
        if merchant
        and item["direction"] == direction
        and item["normalized_merchant"] == merchant
    ]
    if merchant_matches:
        result = _resolve_candidates(merchant_matches, match_type="NORMALIZED_MERCHANT")
        if result["status"] == "AMBIGUOUS" and signature:
            refined = [
                item
                for item in merchant_matches
                if item["description_signature"] == signature
            ]
            if refined:
                return _resolve_candidates(refined, match_type="MERCHANT_DESCRIPTION")
        return result

    recurring_matches = [
        item
        for item in memory_entries
        if signature
        and item["direction"] == direction
        and item["description_signature"] == signature
        and item.get("amount_signature") == amount
        and day is not None
        and item.get("booking_day") is not None
        and abs(int(item["booking_day"]) - day) <= 4
    ]
    if recurring_matches:
        return _resolve_candidates(recurring_matches, match_type="RECURRING_PATTERN")

    description_matches = [
        item
        for item in memory_entries
        if signature
        and item["direction"] == direction
        and item["description_signature"] == signature
    ]
    if len(description_matches) >= 2:
        return _resolve_candidates(description_matches, match_type="NORMALIZED_DESCRIPTION")
    return {"status": "NONE"}


def classification_memory_summary(store: LocalFinanceStore) -> dict[str, Any]:
    entries = list(active_memory_entries(store).values())
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(
            (entry["normalized_merchant"], entry["direction"]), []
        ).append(entry)
    return {
        "policy_version": MEMORY_POLICY_VERSION,
        "active_entry_count": len(entries),
        "groups": [
            {
                "normalized_merchant": merchant,
                "direction": direction,
                **_resolve_candidates(items, match_type="NORMALIZED_MERCHANT"),
                "last_confirmed_at": max(item["created_at"] for item in items),
            }
            for (merchant, direction), items in sorted(groups.items())
        ],
    }


def retrieve_classification_context(
    store: LocalFinanceStore,
    transaction_id: str,
    *,
    max_examples: int = 5,
) -> dict[str, Any]:
    """Return a privacy-minimized, leakage-safe few-shot context."""

    max_examples = max(2, min(max_examples, 5))
    transactions = {
        event["payload"]["transaction_id"]: event["payload"]
        for event in store.events("TransactionNormalized")
    }
    target = transactions.get(transaction_id)
    entries = active_memory_entries(store)
    snapshot = memory_fingerprint(store, entries)
    if target is None:
        return {
            "examples": [],
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
            "memory_snapshot_version": snapshot,
            "retrieved_count": 0,
        }

    target_identity = (
        normalize_merchant(str(target.get("counterparty", ""))),
        description_signature(str(target.get("normalized_description", ""))),
        target.get("direction"),
        _amount_signature(target.get("amount")),
    )
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for entry in entries.values():
        if entry.get("confirmation_type") not in {"USER_CONFIRMED", "USER_CORRECTED"}:
            continue
        source_id = entry["source_transaction_id"]
        source = transactions.get(source_id)
        if source_id == transaction_id or source is None:
            continue
        source_identity = (
            entry["normalized_merchant"],
            entry["description_signature"],
            entry["direction"],
            entry.get("amount_signature", _amount_signature(source.get("amount"))),
        )
        # Exact duplicates are not legitimate historical evidence for evaluation.
        if source_identity == target_identity:
            continue
        if entry["direction"] != target.get("direction"):
            continue
        score = 0
        if entry["normalized_merchant"] == target_identity[0]:
            score += 100
        if entry["description_signature"] and entry["description_signature"] == target_identity[1]:
            score += 60
        if entry.get("amount_signature") == target_identity[3]:
            score += 15
        if not score:
            continue
        ranked.append((score, entry["created_at"], entry))

    ranked.sort(
        key=lambda item: (item[0], item[1], item[2]["memory_id"]), reverse=True
    )
    examples: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for _, _, entry in ranked:
        identity = (
            entry["normalized_merchant"],
            entry["description_signature"],
            entry["category_code"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        examples.append(
            {
                "merchant": entry["normalized_merchant"],
                "description_pattern": entry["description_signature"],
                "category_code": entry["category_code"],
            }
        )
        if len(examples) >= max_examples:
            break
    return {
        "examples": examples,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "memory_snapshot_version": snapshot,
        "retrieved_count": len(examples),
    }


__all__ = [
    "MEMORY_POLICY_VERSION",
    "RETRIEVAL_POLICY_VERSION",
    "active_memory_entries",
    "build_memory_events",
    "classification_memory_summary",
    "description_signature",
    "match_classification_memory",
    "memory_fingerprint",
    "normalize_merchant",
    "retrieve_classification_context",
]
