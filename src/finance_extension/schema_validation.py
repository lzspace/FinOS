"""Draft 2020-12 validation for Vertical Slice event payloads."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from datetime import date
import re

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SOURCE_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "extensions/finance/schemas"
INSTALLED_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "extensions/finance/schemas"
SCHEMA_ROOT = SOURCE_SCHEMA_ROOT if SOURCE_SCHEMA_ROOT.exists() else INSTALLED_SCHEMA_ROOT
SCHEMA_BY_EVENT = {
    "CategoryCreated": "category_events.schema.json",
    "CategoryRenamed": "category_events.schema.json",
    "CategoryArchived": "category_events.schema.json",
    "CategoryRestored": "category_events.schema.json",
    "CategoryShadowModeEnabled": "category_shadow_events.schema.json",
    "CategoryShadowModeDisabled": "category_shadow_events.schema.json",
    "CategoryEvaluationRunStarted": "category_shadow_events.schema.json",
    "CategoryEvaluationRunProgressed": "category_shadow_events.schema.json",
    "CategoryEvaluationRunPaused": "category_shadow_events.schema.json",
    "CategoryEvaluationRunResumed": "category_shadow_events.schema.json",
    "CategoryEvaluationRunCompleted": "category_shadow_events.schema.json",
    "CategoryEvaluationRunCancelled": "category_shadow_events.schema.json",
    "CategorySuggestionGenerated": "category_shadow_events.schema.json",
    "CategorySuggestionConfirmed": "category_shadow_events.schema.json",
    "CategorySuggestionCorrected": "category_shadow_events.schema.json",
    "CategorySuggestionRejected": "category_shadow_events.schema.json",
    "CategorySuggestionSuperseded": "category_shadow_events.schema.json",
    "ClassificationRuleCreatedFromSuggestion": "category_shadow_events.schema.json",
    "CategorySuggestionValidationFailed": "category_shadow_events.schema.json",
    "CategorySuggestionEvaluated": "category_shadow_events.schema.json",
    "CategorySuggestionEvaluationSuperseded": "category_shadow_events.schema.json",
    "ImportFormatFingerprintCreated": "ai_import_mapping_events.schema.json",
    "UnknownImportFormatDetected": "ai_import_mapping_events.schema.json",
    "ImportFormatChangeDetected": "ai_import_mapping_events.schema.json",
    "ImportMappingProposed": "ai_import_mapping_events.schema.json",
    "ImportMappingValidationCompleted": "ai_import_mapping_events.schema.json",
    "ImportMappingConfirmed": "ai_import_mapping_events.schema.json",
    "ImportMappingRejected": "ai_import_mapping_events.schema.json",
    "ImportProfileCreated": "ai_import_mapping_events.schema.json",
    "ImportProfileReused": "ai_import_mapping_events.schema.json",
    "ImportProfileSuperseded": "ai_import_mapping_events.schema.json",
    "TransactionClassificationProposed": "classification_events.schema.json",
    "TransactionClassificationConfirmed": "classification_events.schema.json",
    "TransactionClassificationRejected": "classification_events.schema.json",
    "ClassificationRuleCreated": "classification_events.schema.json",
    "ClassificationRuleApplied": "classification_events.schema.json",
    "DuplicateTransactionDetected": "reconciliation_events.schema.json",
    "DuplicateTransactionConfirmed": "reconciliation_events.schema.json",
    "DuplicateTransactionRejected": "reconciliation_events.schema.json",
    "TransferMatchProposed": "reconciliation_events.schema.json",
    "TransferMatchConfirmed": "reconciliation_events.schema.json",
    "TransferMatchRejected": "reconciliation_events.schema.json",
    "TransferMatchBroken": "reconciliation_events.schema.json",
    "RefundRelationProposed": "reconciliation_events.schema.json",
    "RefundRelationConfirmed": "reconciliation_events.schema.json",
    "RefundRelationRejected": "reconciliation_events.schema.json",
    "RecurringPatternProposed": "forecast_events.schema.json",
    "RecurringPatternConfirmed": "forecast_events.schema.json",
    "RecurringPatternRejected": "forecast_events.schema.json",
    "RecurringPatternUpdated": "forecast_events.schema.json",
    "RecurringPatternPaused": "forecast_events.schema.json",
    "RecurringPatternEnded": "forecast_events.schema.json",
    "ExpectedTransactionCreated": "forecast_events.schema.json",
    "ExpectedTransactionMatched": "forecast_events.schema.json",
    "ExpectedTransactionMissed": "forecast_events.schema.json",
    "ExpectedTransactionCancelled": "forecast_events.schema.json",
    "ForecastCreated": "forecast_events.schema.json",
    "ForecastEvaluated": "forecast_events.schema.json",
    "ForecastSuperseded": "forecast_events.schema.json",
    "AccountCreated": "account_events.schema.json",
    "AccountUpdated": "account_events.schema.json",
    "AccountClosed": "account_events.schema.json",
    "BalanceSnapshotRecorded": "account_events.schema.json",
    "BalanceSnapshotCorrected": "account_events.schema.json",
    "AccountBalanceReconciled": "account_events.schema.json",
    "AssetSnapshotRecorded": "account_events.schema.json",
    "AssetSnapshotCorrected": "account_events.schema.json",
    "LiabilitySnapshotRecorded": "account_events.schema.json",
    "LiabilitySnapshotCorrected": "account_events.schema.json",
    "ImportFileAnalyzed": "multi_account_import_events.schema.json",
    "ImportSectionMapped": "multi_account_import_events.schema.json",
    "ImportSectionSkipped": "multi_account_import_events.schema.json",
    "ImportSectionBindingConfirmed": "multi_account_import_events.schema.json",
    "ImportSectionCompleted": "multi_account_import_events.schema.json",
    "EmptyImportSectionProcessed": "multi_account_import_events.schema.json",
    "OpeningBalanceRecorded": "multi_account_import_events.schema.json",
    "ClosingBalanceRecorded": "multi_account_import_events.schema.json",
    "OpeningBalanceCarryForwardAdjusted": "multi_account_import_events.schema.json",
    "SecurityTransactionNormalized": "multi_account_import_events.schema.json",
    "OpeningSecurityPositionRecorded": "multi_account_import_events.schema.json",
    "EmptyOpeningSecurityPositionsConfirmed": "multi_account_import_events.schema.json",
    "ClosingSecurityPositionRecorded": "multi_account_import_events.schema.json",
    "InvestmentFundingRelationProposed": "multi_account_import_events.schema.json",
    "InvestmentFundingRelationConfirmed": "multi_account_import_events.schema.json",
    "InvestmentFundingRelationRejected": "multi_account_import_events.schema.json",
    "InvestmentFundingRelationBroken": "multi_account_import_events.schema.json",
    "ImportedPeriodBalanceReconciled": "multi_account_import_events.schema.json",
    "ImportedSecurityPositionsReconciled": "multi_account_import_events.schema.json",
    "SecurityPositionSnapshotCorrected": "multi_account_import_events.schema.json",
    "BalanceDifferenceDocumented": "multi_account_import_events.schema.json",
}


class EventValidationError(ValueError):
    pass


def _validate_legacy_import_analysis(event: dict[str, Any]) -> None:
    """Accept the pre-1.1.0 analysis event without weakening new writes.

    Those events were valid when appended, but lack the account-binding and
    source-identity fields introduced in 1.1.0. They remain immutable and
    must be accepted for recovery validation of existing local workspaces.
    """
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload")
    required = {
        "analysis_id": str,
        "detected_profile": str,
        "encoding": str,
        "delimiter": str,
        "file_hash": str,
        "file_size": int,
        "period_start": str,
        "period_end": str,
        "profile_version": str,
        "sections": list,
        "status": str,
        "warnings": list,
    }
    if any(not isinstance(payload.get(name), value_type) for name, value_type in required.items()):
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload")
    if (
        payload["detected_profile"] != "GermanMultiAccountCsvV1"
        or payload["encoding"] not in {"cp1252", "utf-8"}
        or payload["delimiter"] != ";"
        or payload["profile_version"] != "1.0.0"
        or payload["status"] != "ANALYZED"
        or not re.fullmatch(r"[a-f0-9]{64}", payload["file_hash"])
        or payload["file_size"] < 1
        or not payload["sections"]
        or any(not isinstance(item, str) for item in payload["warnings"])
    ):
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload")
    try:
        date.fromisoformat(payload["period_start"])
        date.fromisoformat(payload["period_end"])
    except ValueError as exc:
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload") from exc


def _validate_legacy_balance(event: dict[str, Any]) -> None:
    """Accept balances written before carry-forward fields existed.

    Version 1.3 added three nullable carry-forward fields to balance events.
    Existing events are immutable, so recovery validation
    supplies only those documented defaults before applying the current,
    strict schema. A partial set is never accepted as legacy data.
    """
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload")
    legacy_fields = {
        "balance_id",
        "account_id",
        "balance_date",
        "booked_balance",
        "available_balance",
        "currency",
        "source",
        "confirmation",
        "comment",
        "recorded_at",
    }
    if set(payload) != legacy_fields:
        raise EventValidationError("FINANCE_EVENT_SCHEMA_INVALID: $.payload")
    normalized = {
        **event,
        "payload": {
            **payload,
            "carry_forward_source_reconciliation_id": None,
            "suggested_value": None,
            "adjustment_reason": None,
        },
    }
    validate_event(normalized)


@lru_cache(maxsize=1)
def _schemas() -> tuple[dict[str, dict[str, Any]], Registry]:
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_ROOT.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents.values()
    )
    return documents, registry


def validate_event(event: dict[str, Any]) -> None:
    if event.get("event_type") == "ImportFileAnalyzed" and "export_id" not in event.get(
        "payload", {}
    ):
        _validate_legacy_import_analysis(event)
        return
    if event.get("event_type") in {"OpeningBalanceRecorded", "ClosingBalanceRecorded"} and all(
        name not in event.get("payload", {})
        for name in (
            "carry_forward_source_reconciliation_id",
            "suggested_value",
            "adjustment_reason",
        )
    ):
        _validate_legacy_balance(event)
        return
    schema_name = SCHEMA_BY_EVENT.get(
        event.get("event_type", ""), "vertical_slice_events.schema.json"
    )
    documents, registry = _schemas()
    schema = documents[schema_name]
    errors = sorted(
        Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(
            event
        ),
        key=str,
    )
    if errors:
        # Never include an event/payload in this error: it may contain finance data.
        raise EventValidationError(f"FINANCE_EVENT_SCHEMA_INVALID: {errors[0].json_path}")
