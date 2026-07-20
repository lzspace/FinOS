"""Strict GenericFinanceCsvV1 import and deterministic normalization."""

from __future__ import annotations

import csv
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .storage_policy import validate_runtime_path
from .store import LocalFinanceStore

REQUIRED_COLUMNS = (
    "booking_date",
    "value_date",
    "amount",
    "currency",
    "counterparty",
    "description",
)
PARSER_VERSION = "1.0.0"


class ImportErrorSafe(ValueError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    kind: str,
    aggregate_type: str,
    aggregate_id: str,
    version: int,
    correlation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": kind,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
        "occurred_at": _now(),
        "correlation_id": correlation_id,
        "causation_id": correlation_id,
        "payload": payload,
    }


def import_csv(store: LocalFinanceStore, source: str | Path, account_id: str) -> str:
    path = validate_runtime_path(source, repository_roots=store.repository_roots)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ImportErrorSafe("FINANCE_IMPORT_UNREADABLE") from exc
    if not rows or tuple(rows[0].keys()) != REQUIRED_COLUMNS:
        raise ImportErrorSafe("FINANCE_IMPORT_PROFILE_INVALID")
    for row in rows:
        try:
            Decimal(row["amount"])
            datetime.fromisoformat(row["booking_date"])
            datetime.fromisoformat(row["value_date"])
            if len(row["currency"]) != 3 or not row["currency"].isupper():
                raise ValueError
        except (InvalidOperation, ValueError) as exc:
            raise ImportErrorSafe("FINANCE_IMPORT_ROW_INVALID") from exc
    batch_id, command_id = "imp_" + hashlib.sha256(raw).hexdigest(), _id("cmd")
    command = {
        "command_id": command_id,
        "command_type": "ImportTransactions",
        "idempotency_key": hashlib.sha256(raw).hexdigest(),
    }
    events = [
        _event(
            "ImportBatchStarted",
            "ImportBatch",
            batch_id,
            1,
            command_id,
            {
                "import_batch_id": batch_id,
                "file_hash": hashlib.sha256(raw).hexdigest(),
                "parser_version": PARSER_VERSION,
            },
        )
    ]
    for index, row in enumerate(rows, start=1):
        events.append(
            _event(
                "RawTransactionImported",
                "ImportBatch",
                batch_id,
                index + 1,
                command_id,
                {
                    "import_batch_id": batch_id,
                    "source_record_index": index,
                    "account_id": account_id,
                    "raw_fields": row,
                    "content_hash": "sha256:"
                    + hashlib.sha256(
                        "\x1f".join(row[c] for c in REQUIRED_COLUMNS).encode()
                    ).hexdigest(),
                },
            )
        )
    events.append(
        _event(
            "ImportBatchCompleted",
            "ImportBatch",
            batch_id,
            len(rows) + 2,
            command_id,
            {"import_batch_id": batch_id, "record_count": len(rows)},
        )
    )
    already_imported = store.has_import_content_hash(raw)
    store.append_events(command, events)
    if not already_imported:
        store.store_import_file(batch_id, path, raw, PARSER_VERSION)
    return batch_id


def normalize_batch(store: LocalFinanceStore, batch_id: str) -> int:
    raw_events = [
        event
        for event in store.events("RawTransactionImported")
        if event["payload"]["import_batch_id"] == batch_id
    ]
    command_id = _id("cmd")
    events = []
    for raw in raw_events:
        fields = raw["payload"]["raw_fields"]
        amount = Decimal(fields["amount"])
        transaction_id = _id("txn")
        payload = {
            "transaction_id": transaction_id,
            "raw_transaction_event_id": raw["event_id"],
            "account_id": raw["payload"]["account_id"],
            "booking_date": fields["booking_date"],
            "value_date": fields["value_date"],
            "amount": str(amount),
            "currency": fields["currency"],
            "direction": "CREDIT" if amount >= 0 else "DEBIT",
            "counterparty": fields["counterparty"],
            "normalized_description": fields["description"].strip(),
            "transaction_type": "INCOME" if amount >= 0 else "EXPENSE",
            "normalization_policy_version": "1.0.0",
        }
        events.append(
            _event("TransactionNormalized", "Transaction", transaction_id, 1, command_id, payload)
        )
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "NormalizeImportBatch",
                "idempotency_key": f"normalize:{batch_id}",
            },
            events,
        )
    )
