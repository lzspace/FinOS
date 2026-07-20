"""Stable error metadata exposed without finance data or stack traces."""

from __future__ import annotations

from typing import Any


def _group(code: str) -> str:
    for marker, group in (
        ("WORKSPACE_LOCK", "WORKSPACE"),
        ("ARCHIVE", "RECOVERY"),
        ("BACKUP", "RECOVERY"),
        ("STORE", "STORAGE"),
        ("KEY", "CRYPTOGRAPHY"),
        ("CONTRACT", "CONTRACT"),
        ("SCHEMA", "CONTRACT"),
        ("DIAGNOSTIC", "DIAGNOSTICS"),
        ("MIGRATION", "MIGRATION"),
        ("IMPORT", "IMPORT"),
    ):
        if marker in code:
            return group
    return "DOMAIN"


def error_definition(code: str) -> dict[str, Any]:
    if not code.startswith("FINANCE_"):
        raise ValueError("FINANCE_ERROR_CODE_INVALID")
    blocking = any(
        marker in code
        for marker in (
            "LOCKED",
            "INTEGRITY_FAILED",
            "DECRYPTION_FAILED",
            "DOWNGRADE_BLOCKED",
            "TAMPERED",
            "MIGRATION_FAILED",
        )
    )
    retryable = any(marker in code for marker in ("LOCKED", "UNAVAILABLE", "NOT_FOUND"))
    return {
        "code": code,
        "group": _group(code),
        "severity": "BLOCKING" if blocking else "ERROR",
        "retryable": retryable,
        "user_message_key": f"errors.{code.casefold()}",
        "technical_context_policy": "ALLOWLISTED_METADATA_ONLY",
        "recovery_action": "OPEN_RECOVERY_GUIDANCE" if blocking else "REVIEW_INPUT",
        "audit_requirement": (
            "REQUIRED" if blocking or _group(code) in {"RECOVERY", "CRYPTOGRAPHY"} else "OPTIONAL"
        ),
    }


__all__ = ["error_definition"]
