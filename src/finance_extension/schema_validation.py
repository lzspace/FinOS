"""Draft 2020-12 validation for Vertical Slice event payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SOURCE_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "extensions/finance/schemas"
INSTALLED_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "extensions/finance/schemas"
SCHEMA_ROOT = SOURCE_SCHEMA_ROOT if SOURCE_SCHEMA_ROOT.exists() else INSTALLED_SCHEMA_ROOT
SCHEMA_BY_EVENT = {
    "TransactionClassificationProposed": "classification_events.schema.json",
    "TransactionClassificationConfirmed": "classification_events.schema.json",
    "TransactionClassificationRejected": "classification_events.schema.json",
    "ClassificationRuleCreated": "classification_events.schema.json",
    "ClassificationRuleApplied": "classification_events.schema.json",
}


class EventValidationError(ValueError):
    pass


def validate_event(event: dict[str, Any]) -> None:
    schema_path = SCHEMA_ROOT / SCHEMA_BY_EVENT.get(
        event.get("event_type", ""), "vertical_slice_events.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event), key=str
    )
    if errors:
        # Never include an event/payload in this error: it may contain finance data.
        raise EventValidationError(f"FINANCE_EVENT_SCHEMA_INVALID: {errors[0].json_path}")
