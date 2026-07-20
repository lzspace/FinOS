"""Explicitly local, encrypted and data-minimizing diagnostics."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .crypto import KeyProvider
from .storage_policy import validate_runtime_path


class DiagnosticInvariantError(ValueError):
    pass


ALLOWED_FIELDS = frozenset(
    {
        "recorded_at",
        "operation_kind",
        "operation_name",
        "duration_ms",
        "projection_duration_ms",
        "event_count",
        "store_size_bytes",
        "migration_result",
        "error_code",
        "component",
        "component_status",
    }
)
FORBIDDEN_FIELD_FRAGMENTS = (
    "amount",
    "counterparty",
    "description",
    "account",
    "payload",
    "transaction",
    "currency",
    "category",
    "path",
)


class LocalDiagnosticRecorder:
    FILE_NAME = "diagnostics.jsonl.fernet"

    def __init__(self, data_dir: str | Path, key_provider: KeyProvider) -> None:
        self.data_dir = Path(data_dir)
        self.key_provider = key_provider

    @property
    def path(self) -> Path:
        return self.data_dir / self.FILE_NAME

    def record(self, **fields: Any) -> None:
        unknown = set(fields) - ALLOWED_FIELDS
        if unknown or any(
            fragment in key.casefold() for key in fields for fragment in FORBIDDEN_FIELD_FRAGMENTS
        ):
            raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_FIELD_FORBIDDEN")
        operation = fields.get("operation_name")
        if operation is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", str(operation)):
            raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_VALUE_INVALID")
        error_code = fields.get("error_code")
        if error_code is not None and not re.fullmatch(r"FINANCE_[A-Z0-9_]+", str(error_code)):
            raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_VALUE_INVALID")
        record = {"recorded_at": datetime.now(UTC).isoformat(), **fields}
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        root = validate_runtime_path(self.data_dir)
        root.mkdir(parents=True, exist_ok=True)
        token = Fernet(self.key_provider.get_key()).encrypt(encoded)
        with self.path.open("ab") as stream:
            stream.write(token + b"\n")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in self.path.read_bytes().splitlines():
            try:
                decoded = Fernet(self.key_provider.get_key()).decrypt(line)
                item = json.loads(decoded)
            except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
                raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_INTEGRITY_FAILED") from exc
            if set(item) - ALLOWED_FIELDS:
                raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_FIELD_FORBIDDEN")
            result.append(item)
        return result

    def export(self, destination: str | Path, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise DiagnosticInvariantError("FINANCE_DIAGNOSTIC_EXPORT_CONFIRMATION_REQUIRED")
        target = validate_runtime_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        records = self.records()
        temporary = target.parent / f".{target.name}.tmp"
        temporary.write_text(
            json.dumps({"format": 1, "records": records}, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)
        return {"exported": True, "record_count": len(records), "path": str(target)}


__all__ = [
    "ALLOWED_FIELDS",
    "DiagnosticInvariantError",
    "LocalDiagnosticRecorder",
]
