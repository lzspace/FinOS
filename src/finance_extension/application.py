"""Local application API for desktop IPC and projection-only UI access."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from collections import deque
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
from time import monotonic
from typing import Any, Callable

from . import __version__
from .accounts import (
    account_balance_history,
    account_overview,
    account_reviews,
    asset_allocation,
    balance_reconciliations,
    close_account,
    correct_asset_snapshot,
    correct_balance_snapshot,
    correct_liability_snapshot,
    create_account,
    create_asset_snapshot,
    create_liability_snapshot,
    liability_overview,
    liquidity_overview,
    net_worth_history,
    net_worth_overview,
    projected_month_end_balance,
    reconcile_account_balance,
    record_balance_snapshot,
    update_account,
)
from .account_workspace import (
    account_audit_trail,
    account_balance_ledger,
    account_imports,
    account_overview_for_period,
    account_open_reviews,
    account_period_summary,
    account_positions,
    account_position_history,
    account_reconciliations_for_period,
    account_review_counts,
    account_transactions_for_period,
)
from .classification import (
    active_classifications,
    classification_review,
    classify_transactions,
    confirm_classification,
    create_rule,
    reject_classification,
)
from .category_shadow import (
    cancel_category_evaluation_run,
    category_evaluation_summary,
    category_shadow_status,
    category_suggestion_review_summary,
    confirm_category_suggestion,
    correct_category_suggestion,
    create_classification_rule_from_suggestion,
    disable_category_shadow_mode,
    enable_category_shadow_mode,
    evaluate_category_suggestions,
    generate_visible_category_suggestions,
    get_category_evaluation_run,
    get_category_suggestion_detail,
    get_transaction_category_suggestion,
    list_category_suggestions,
    list_classification_rules_with_origin,
    list_category_evaluation_runs,
    pause_category_evaluation_run,
    preview_category_evaluation_run,
    resume_category_evaluation_run,
    reject_category_suggestion,
    start_category_evaluation_run,
)
from .categories import categories, create_category, rename_category, set_category_archived
from .crypto import KeyProvider, KeychainKeyProvider
from .diagnostics import LocalDiagnosticRecorder
from .forecasting import (
    confirm_recurring_pattern,
    create_forecasts,
    detect_recurring_patterns,
    end_recurring_pattern,
    evaluate_forecast,
    expected_transactions,
    forecast_accuracy,
    monthly_forecast,
    pause_recurring_pattern,
    recurring_patterns,
    reject_recurring_pattern,
    update_recurring_pattern,
)
from .financial_time_series import financial_time_series
from .importer import import_csv, normalize_batch
from .ai_import_mapping import (
    analyze_unknown_import_format,
    confirm_import_mapping,
    get_import_format_status,
    get_import_mapping_proposal,
    get_import_profile,
    get_import_profile_history,
    list_import_profiles,
    reject_import_mapping,
    validate_mapping_for_proposal,
)
from .multi_account_import import (
    analyze_import_file,
    break_investment_funding_relation,
    confirm_investment_funding_relation,
    confirm_empty_opening_security_positions,
    correct_security_position_snapshot,
    detect_investment_funding_relations,
    document_balance_difference,
    get_import_analysis,
    get_bank_monthly_export,
    get_import_section_preview,
    import_mapped_sections,
    imported_period_reconciliations,
    imported_security_position_reconciliations,
    initial_balance_requirements,
    list_import_sections,
    map_import_sections,
    reconcile_imported_period_balance,
    reconcile_imported_period_positions,
    reconcile_imported_security_positions,
    record_closing_balance,
    record_opening_balance,
    record_opening_security_position,
    record_closing_security_position,
    reject_investment_funding_relation,
    security_positions,
    security_transactions,
    section_bindings,
)
from .import_ui import (
    balance_reconciliation_context,
    import_execution_result,
    import_history,
    import_history_detail,
    import_wizard_state,
    investment_funding_relation_contexts,
    position_reconciliation_context,
)
from .local_model import LocalModelService
from .reconciliation import (
    break_transfer,
    confirm_duplicate,
    confirm_refund,
    confirm_transfer,
    detect_duplicates,
    detect_refunds,
    detect_transfers,
    reconcile,
    reconciled_category_breakdown,
    reconciled_transactions,
    reject_duplicate,
    reject_refund,
    reject_transfer,
    relation_review,
)
from .read_models import ProjectionDefinition, ProjectionRegistry
from .review_inbox import review_inbox
from .recovery import (
    create_backup,
    delete_backup,
    export_finance_data,
    import_finance_archive,
    key_status,
    list_backups,
    migration_status,
    repair_local_store,
    restore_backup,
    rotate_encryption_key,
    validate_store_integrity,
    verify_archive,
)
from .periods import (
    PeriodSelectionError,
    available_periods,
    default_month_period,
    resolve_period_from_payload,
)
from .release_security import ReleaseIntegrityError, verify_integrity_manifest
from .schema_validation import SCHEMA_ROOT
from .store import LocalFinanceStore, StoreInvariantError
from .storage_policy import validate_runtime_path
from .workspace_lock import inspect_workspace_lock

PRODUCT_VERSION = "1.5.0"
CONTRACT_VERSION = "1.5.0"
UI_CONTRACT_VERSION = "1.5.0"
PROJECTION_VERSIONS: dict[str, str] = {
    "account_overview": "1.0.0",
    "account_detail": "1.0.0",
    "account_period_summary": "1.0.0",
    "account_transactions": "1.0.0",
    "balance_history": "1.0.0",
    "account_reconciliations": "1.0.0",
    "account_imports": "1.0.0",
    "account_positions": "1.0.0",
    "account_position_history": "1.0.0",
    "account_audit_trail": "1.0.0",
    "available_periods": "1.0.0",
    "financial_time_series": "1.0.0",
    "import_format_status": "1.0.0",
    "import_mapping_proposal": "1.0.0",
    "import_profiles": "1.0.0",
    "category_model_evaluation": "1.0.0",
    "category_suggestion_review": "1.0.0",
    "review_inbox": "1.0.0",
}


class ApplicationContractError(ValueError):
    pass


PAGINATION_CONTRACT_VERSION = "1.0.0"


def _query_fingerprint(name: str, payload: dict[str, Any]) -> str:
    normalized = {key: value for key, value in payload.items() if key not in {"cursor", "limit"}}
    encoded = json.dumps(
        {"query": name, "payload": normalized}, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode_cursor(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_cursor(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ApplicationContractError("QUERY_CURSOR_INVALID")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationContractError("QUERY_CURSOR_INVALID") from exc
    if not isinstance(decoded, dict):
        raise ApplicationContractError("QUERY_CURSOR_INVALID")
    return decoded


def _paginate(
    rows: list[dict[str, Any]],
    *,
    query_name: str,
    payload: dict[str, Any],
    sequence: int,
    identity: Callable[[dict[str, Any]], tuple[str, ...]],
    default_limit: int = 50,
) -> tuple[list[dict[str, Any]], str | None, bool, int]:
    raw_limit = payload.get("limit", default_limit)
    if isinstance(raw_limit, bool):
        raise ApplicationContractError("QUERY_LIMIT_INVALID")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ApplicationContractError("QUERY_LIMIT_INVALID") from exc
    if limit < 1 or limit > 200:
        raise ApplicationContractError("QUERY_LIMIT_INVALID")
    fingerprint = _query_fingerprint(query_name, payload)
    start = 0
    if payload.get("cursor") is not None:
        cursor = _decode_cursor(payload["cursor"])
        if cursor.get("v") != PAGINATION_CONTRACT_VERSION or cursor.get("fingerprint") != fingerprint:
            raise ApplicationContractError("QUERY_CURSOR_INVALID")
        if cursor.get("sequence") != sequence:
            raise ApplicationContractError("QUERY_CURSOR_STALE")
        last = cursor.get("last")
        if not isinstance(last, list) or not all(isinstance(item, str) for item in last):
            raise ApplicationContractError("QUERY_CURSOR_INVALID")
        marker = tuple(last)
        try:
            start = next(index for index, row in enumerate(rows) if identity(row) == marker) + 1
        except StopIteration as exc:
            raise ApplicationContractError("QUERY_CURSOR_INVALID") from exc
    page = rows[start : start + limit]
    has_more = start + limit < len(rows)
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(
            {
                "v": PAGINATION_CONTRACT_VERSION,
                "sequence": sequence,
                "fingerprint": fingerprint,
                "last": list(identity(page[-1])),
            }
        )
    return page, next_cursor, has_more, limit


def _pagination_parameters(
    query_name: str, payload: dict[str, Any], sequence: int, default_limit: int = 50
) -> tuple[int, tuple[str, ...] | None, str]:
    raw_limit = payload.get("limit", default_limit)
    if isinstance(raw_limit, bool):
        raise ApplicationContractError("QUERY_LIMIT_INVALID")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ApplicationContractError("QUERY_LIMIT_INVALID") from exc
    if limit < 1 or limit > 200:
        raise ApplicationContractError("QUERY_LIMIT_INVALID")
    fingerprint = _query_fingerprint(query_name, payload)
    if payload.get("cursor") is None:
        return limit, None, fingerprint
    cursor = _decode_cursor(payload["cursor"])
    if cursor.get("v") != PAGINATION_CONTRACT_VERSION or cursor.get("fingerprint") != fingerprint:
        raise ApplicationContractError("QUERY_CURSOR_INVALID")
    if cursor.get("sequence") != sequence:
        raise ApplicationContractError("QUERY_CURSOR_STALE")
    last = cursor.get("last")
    if not isinstance(last, list) or not all(isinstance(item, str) for item in last):
        raise ApplicationContractError("QUERY_CURSOR_INVALID")
    return limit, tuple(last), fingerprint


COMMAND_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ImportTransactions": ("source_file_path", "account_id"),
    "AnalyzeImportFile": ("requested_profile",),
    "AnalyzeUnknownImportFormat": ("bank_identifier",),
    "ValidateImportMapping": ("proposal_id", "mapping"),
    "ConfirmImportMapping": ("proposal_id", "final_mapping", "validation_result_id"),
    "RejectImportMapping": ("proposal_id",),
    "MapImportSections": ("analysis_id", "section_mappings"),
    "ImportMappedSections": (
        "analysis_id",
        "parser_profile",
        "parser_version",
        "import_mode",
    ),
    "RecordOpeningBalance": (
        "account_id",
        "balance_date",
        "booked_balance",
        "currency",
        "source",
        "confirmation",
    ),
    "RecordClosingBalance": (
        "account_id",
        "balance_date",
        "booked_balance",
        "currency",
        "source",
        "confirmation",
    ),
    "RecordOpeningSecurityPosition": (
        "account_id",
        "valuation_date",
        "security_identifier_type",
        "security_identifier",
        "security_name",
        "quantity",
        "valuation_price",
        "price_currency",
        "market_value",
        "valuation_source",
        "confirmation",
    ),
    "ConfirmEmptyOpeningSecurityPositions": ("account_id", "valuation_date"),
    "RecordClosingSecurityPosition": (
        "account_id",
        "valuation_date",
        "security_identifier_type",
        "security_identifier",
        "security_name",
        "quantity",
        "confirmation",
    ),
    "ReconcileImportedPeriodBalance": ("account_id", "period_start", "period_end"),
    "ReconcileImportedSecurityPositions": (
        "account_id",
        "period_start",
        "period_end",
    ),
    "ReconcileImportedPeriodPositions": (
        "account_id",
        "period_start",
        "period_end",
    ),
    "CorrectSecurityPositionSnapshot": ("snapshot_id", "quantity", "reason"),
    "DocumentBalanceDifference": ("reconciliation_id", "explanation"),
    "ConfirmInvestmentFundingRelation": ("relation_id",),
    "RejectInvestmentFundingRelation": ("relation_id",),
    "BreakInvestmentFundingRelation": ("relation_id",),
    "ConfirmClassification": ("transaction_id", "category_code"),
    "RejectClassification": ("transaction_id",),
    "CreateClassificationRule": ("field", "operator", "value", "category_code", "priority"),
    "CreateCategory": ("display_name", "category_type"),
    "RenameCategory": ("category_code", "display_name"),
    "ArchiveCategory": ("category_code",),
    "RestoreCategory": ("category_code",),
    "ConfirmDuplicate": ("relation_id",),
    "RejectDuplicate": ("relation_id",),
    "ConfirmTransfer": ("outgoing_id", "incoming_id"),
    "RejectTransfer": ("outgoing_id", "incoming_id"),
    "BreakTransferMatch": ("outgoing_id", "incoming_id"),
    "ConfirmRefund": ("refund_id", "original_id", "amount"),
    "RejectRefund": ("refund_id", "original_id"),
    "DetectRecurringPatterns": ("from_month", "to_month"),
    "ConfirmRecurringPattern": ("pattern_id",),
    "RejectRecurringPattern": ("pattern_id",),
    "UpdateRecurringPattern": ("pattern_id", "amount", "day_from", "day_to"),
    "PauseRecurringPattern": ("pattern_id",),
    "EndRecurringPattern": ("pattern_id",),
    "CreateForecast": ("month",),
    "EvaluateForecast": ("month",),
    "CreateAccount": ("display_name", "account_type", "institution", "currency"),
    "UpdateAccount": ("account_id",),
    "CloseAccount": ("account_id", "closed_at"),
    "RecordBalanceSnapshot": (
        "account_id",
        "balance_date",
        "booked_balance",
        "currency",
        "source",
        "confidence",
    ),
    "CorrectBalanceSnapshot": ("snapshot_id", "booked_balance", "reason"),
    "ReconcileAccountBalance": ("account_id",),
    "CreateAssetSnapshot": (
        "item_id",
        "display_name",
        "item_type",
        "valuation_date",
        "amount",
        "currency",
    ),
    "CorrectAssetSnapshot": ("snapshot_id", "amount", "reason"),
    "CreateLiabilitySnapshot": (
        "item_id",
        "display_name",
        "item_type",
        "valuation_date",
        "amount",
        "currency",
    ),
    "CorrectLiabilitySnapshot": ("snapshot_id", "amount", "reason"),
    "VerifyBackup": ("archive_path",),
    "RestoreBackup": ("archive_path",),
    "DeleteBackup": ("archive_id",),
    "ImportFinanceArchive": ("archive_path",),
    "ExportDiagnostics": ("destination_path", "confirmed"),
    "RegisterLocalAIRuntime": ("runtime_path",),
    "RegisterLocalModel": ("model_path", "manifest_path"),
    "PauseCategoryEvaluationRun": ("run_id",),
    "ResumeCategoryEvaluationRun": ("run_id",),
    "CancelCategoryEvaluationRun": ("run_id",),
    "ConfirmCategorySuggestion": ("suggestion_id",),
    "CorrectCategorySuggestion": ("suggestion_id", "selected_category"),
    "RejectCategorySuggestion": ("suggestion_id",),
    "CreateClassificationRuleFromSuggestion": ("suggestion_id",),
}


class FinanceApplicationService:
    """Narrow local interface intended for a desktop IPC adapter.

    UI clients can execute named queries and commands. They cannot append events
    or access encryption keys, SQLite, snapshots, or imported source files.
    """

    def __init__(
        self,
        store: LocalFinanceStore,
        *,
        network_egress_disabled: bool | None = None,
        archive_key_provider: KeyProvider | None = None,
        backup_directory: str | Path | None = None,
        export_directory: str | Path | None = None,
        diagnostic_recorder: LocalDiagnosticRecorder | None = None,
        import_file_resolver: Callable[[str], str | Path] | None = None,
        local_model_service: LocalModelService | None = None,
    ) -> None:
        self._store = store
        self._network_egress_disabled = network_egress_disabled
        self._archive_key_provider = archive_key_provider or KeychainKeyProvider(
            service="agent-os.finance.backup", username="archive"
        )
        self._backup_directory = backup_directory
        self._export_directory = export_directory
        self._diagnostic_recorder = diagnostic_recorder
        mode = os.environ.get("FINANCE_PERFORMANCE_MODE", "SUMMARY").upper()
        self._performance_mode = mode if mode in {"OFF", "SUMMARY", "DETAILED"} else "SUMMARY"
        self._performance_samples: deque[dict[str, Any]] = deque(maxlen=500)
        self._read_models = ProjectionRegistry(self._store)
        self._import_file_resolver = import_file_resolver
        self._local_model_service = local_model_service or LocalModelService(
            self._store.data_dir.parent / "models"
        )
        self._local_model_service.start_async()

    def _sequence(self) -> int:
        context = self._store.current_read_context
        return context.sequence if context is not None else self._store.latest_sequence()

    def _projection(
        self,
        name: str,
        parameters: dict[str, Any],
        factory: Callable[[], Any],
    ) -> Any:
        context = self._store.current_read_context
        if context is None:
            return factory()
        normalized = json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
        return context.memoize((name, normalized, context.sequence), factory)

    def _persisted_response(
        self,
        name: str,
        version: str,
        parameters: dict[str, Any],
        factory: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Return an encrypted-store read model at the request's pinned sequence."""

        sequence = self._sequence()
        definition = ProjectionDefinition(name, version)

        def build() -> dict[str, Any]:
            response = factory()
            return {
                "data": response.get("data"),
                "state": response.get("state", "READY"),
                "projection_version": response.get("projection_version", version),
            }

        materialized = self._read_models.materialize(
            definition, parameters, sequence, build
        )
        return self._envelope(
            materialized["data"],
            status=materialized["state"],
            projection_version=materialized["projection_version"],
        )

    def _envelope(
        self, data: Any, *, status: str = "READY", projection_version: str = "1.0.0"
    ) -> dict[str, Any]:
        sequence = self._sequence()
        projection_sequence = sequence
        event_store_sequence = sequence
        if projection_sequence < event_store_sequence:
            freshness_status = "STALE"
        elif projection_sequence > event_store_sequence:
            freshness_status = "INVALID"
        else:
            freshness_status = "CURRENT"
        return {
            "schema_version": "1.0.0",
            "state": status,
            "projection_sequence": projection_sequence,
            "event_store_sequence": event_store_sequence,
            "projection_version": projection_version,
            "freshness_status": freshness_status,
            "generated_at": datetime.now(UTC).isoformat(),
            "data": data,
        }

    def query(
        self,
        query_name: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        queries: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "GetCapabilityManifest": self._capabilities,
            "GetStartupStatus": self._startup_status,
            "GetRuntimeSecurityStatus": self._security_status,
            "GetDashboard": self._dashboard,
            "GetFinancialTimeSeries": self._financial_time_series,
            "ListTransactions": self._transactions,
            "GetTransactionDetails": self._transaction_details,
            "ListAccounts": self._accounts,
            "GetAccount": self._account,
            "GetAccountBalanceHistory": self._account_balance_history,
            "GetBalanceReconciliation": self._balance_reconciliation,
            "GetAvailablePeriods": self._available_periods,
            "ListAccountOverviews": self._account_overviews,
            "GetAccountDetail": self._account_detail,
            "GetAccountPeriodSummary": self._account_period_summary,
            "ListAccountTransactions": self._account_transactions_query,
            "ListAccountReconciliations": self._account_reconciliations_query,
            "ListAccountImports": self._account_imports_query,
            "ListAccountPositions": self._account_positions_query,
            "GetAccountPositionHistory": self._account_position_history_query,
            "GetAccountAuditTrail": self._account_audit_trail_query,
            "GetLiquidityOverview": self._liquidity,
            "GetNetWorthOverview": self._net_worth,
            "GetNetWorthHistory": self._net_worth_history,
            "GetLiabilityOverview": self._liabilities,
            "GetAssetAllocation": self._asset_allocation,
            "GetProjectedMonthEndBalance": self._projected_balance,
            "ListAccountReviews": self._account_reviews,
            "GetReviewInbox": self._review_inbox,
            "GetCategoryBreakdown": self._category_breakdown,
            "ListCategories": self._categories,
            "ListClassificationReviews": self._classification_reviews,
            "ListReconciliationReviews": self._reconciliation_reviews,
            "ListRecurringPatterns": self._recurring_patterns,
            "GetRecurringPattern": self._recurring_pattern,
            "ListExpectedTransactions": self._expected_transactions,
            "GetForecast": self._forecast,
            "ListForecastVersions": self._forecast_versions,
            "GetForecastEvaluation": self._forecast_evaluation,
            "ListImportBatches": self._import_batches,
            "GetImportBatch": self._import_batch,
            "GetImportAnalysis": self._import_analysis,
            "GetImportWizardState": self._import_wizard_state,
            "GetImportExecutionResult": self._import_execution_result,
            "GetImportHistory": self._import_history,
            "GetImportHistoryDetail": self._import_history_detail,
            "GetBankMonthlyExport": self._bank_monthly_export,
            "ListImportSections": self._import_sections,
            "ListImportSectionBindings": self._import_section_bindings,
            "GetImportSectionPreview": self._import_section_preview,
            "GetInitialBalanceRequirements": self._initial_balance_requirements,
            "GetImportedPeriodReconciliation": self._imported_period_reconciliation,
            "GetImportedPeriodReconciliationContext": self._imported_period_reconciliation_context,
            "GetImportedSecurityPositionReconciliation": self._imported_security_position_reconciliation,
            "GetPositionReconciliation": self._position_reconciliation,
            "ListInvestmentFundingRelations": self._investment_funding_relations,
            "GetSecurityTransaction": self._security_transaction,
            "ListSecurityPositions": self._security_positions,
            "ListBackups": self._backups,
            "GetStoreIntegrity": self._store_integrity,
            "GetKeyStatus": self._key_status,
            "GetMigrationStatus": self._migration_status,
            "GetProjectionStatus": self._projection_status,
            "GetLocalModelStatus": self._local_model_status,
            "GetImportFormatStatus": self._import_format_status,
            "GetImportMappingProposal": self._import_mapping_proposal,
            "GetImportMappingValidation": self._import_mapping_validation,
            "ListImportProfiles": self._import_profiles,
            "GetImportProfile": self._import_profile,
            "GetImportProfileHistory": self._import_profile_history,
            "GetFormatChangeDetails": self._format_change_details,
            "GetCategoryShadowStatus": self._category_shadow_status,
            "PreviewCategoryEvaluationRun": self._category_evaluation_preview,
            "GetCategoryEvaluationSummary": self._category_evaluation_summary,
            "GetCategoryEvaluationByCategory": self._category_evaluation_by_category,
            "GetCategoryEvaluationThresholds": self._category_evaluation_thresholds,
            "GetCategoryEvaluationRun": self._category_evaluation_run,
            "ListCategoryEvaluationRuns": self._category_evaluation_runs,
            "GetCategorySuggestionDetail": self._category_suggestion_detail,
            "GetCategorySuggestion": self._category_suggestion_detail,
            "GetTransactionCategorySuggestion": self._transaction_category_suggestion,
            "ListPendingCategorySuggestions": self._pending_category_suggestions,
            "GetCategorySuggestionReviewSummary": self._category_suggestion_review_summary,
            "ListClassificationRules": self._classification_rules,
            "GetQueryPerformanceSummary": self._query_performance_summary,
        }
        if query_name not in queries:
            raise ApplicationContractError("FINANCE_QUERY_UNSUPPORTED")
        started = monotonic()
        with self._store.read_context() as context:
            result = queries[query_name](payload)
            if self._performance_mode != "OFF":
                try:
                    data = result.get("data") if isinstance(result, dict) else None
                    if isinstance(data, list):
                        result_count = len(data)
                    elif isinstance(data, dict):
                        result_count = next(
                            (len(value) for value in data.values() if isinstance(value, list)),
                            len(data),
                        )
                    else:
                        result_count = 0 if data is None else 1
                    sample = {
                        "instrumentation_schema": "1.0.0",
                        "operation": query_name,
                        "duration_ms": round((monotonic() - started) * 1000),
                        "event_store_sequence": context.sequence,
                        "events_loaded": context.events_loaded,
                        "events_decoded": context.events_decoded,
                        "store_queries": context.store_queries,
                        "request_cache_hits": context.request_cache_hits,
                        "request_cache_misses": context.request_cache_misses,
                        "result_count": result_count,
                        "response_bytes": len(
                            json.dumps(result, separators=(",", ":"), default=str).encode()
                        ),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    self._performance_samples.append(sample)
                    if self._diagnostic_recorder:
                        self._diagnostic_recorder.record(
                            operation_kind="QUERY",
                            operation_name=query_name,
                            duration_ms=sample["duration_ms"],
                            event_store_sequence=context.sequence,
                            events_loaded=context.events_loaded,
                            events_decoded=context.events_decoded,
                            store_queries=context.store_queries,
                            request_cache_hits=context.request_cache_hits,
                            request_cache_misses=context.request_cache_misses,
                            result_count=result_count,
                            response_bytes=sample["response_bytes"],
                            component_status="PASSED",
                        )
                except Exception:
                    # Instrumentation is deliberately best effort and must never
                    # affect a Finance query.
                    pass
            return result

    def _query_performance_summary(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(
            {
                "instrumentation_schema": "1.0.0",
                "mode": self._performance_mode,
                "samples": list(self._performance_samples),
            }
        )

    def _startup_status(self, _: dict[str, Any]) -> dict[str, Any]:
        state = "READY"
        error_code: str | None = None
        checks: dict[str, str] = {}
        lock = inspect_workspace_lock(self._store.data_dir)
        checks["workspace_lock"] = lock["status"]
        if lock["status"] not in {"LOCKED"}:
            state, error_code = "WORKSPACE_LOCKED", "FINANCE_WORKSPACE_LOCK_OWNERSHIP_LOST"
        try:
            self._store.key_provider.get_key()
            checks["key"] = "AVAILABLE"
        except Exception:
            state, error_code = "KEYCHAIN_UNAVAILABLE", "FINANCE_KEY_UNAVAILABLE"
            checks["key"] = "UNAVAILABLE"
        integrity = validate_store_integrity(self._store)
        checks["store_integrity"] = integrity["status"]
        if integrity["status"] != "VALID":
            state, error_code = "STORE_CORRUPTED", "FINANCE_STORE_INTEGRITY_FAILED"
            diagnostic = integrity.get("diagnostic")
            if isinstance(diagnostic, dict):
                event_type = diagnostic.get("event_type")
                sequence = diagnostic.get("event_sequence")
                schema_path = diagnostic.get("schema_path")
                if isinstance(event_type, str) and isinstance(sequence, int) and isinstance(schema_path, str):
                    checks["integrity_diagnostic"] = f"{event_type} · Ereignis #{sequence} · {schema_path}"
        schema_version = self._store.schema_version()
        checks["migration"] = "CURRENT" if schema_version == 3 else "MIGRATION_REQUIRED"
        if schema_version < LocalFinanceStore.CURRENT_SCHEMA_VERSION:
            state, error_code = "MIGRATION_REQUIRED", "FINANCE_MIGRATION_REQUIRED"
        free = shutil.disk_usage(self._store.data_dir).free
        checks["free_space"] = "SUFFICIENT" if free >= 100 * 1024 * 1024 else "INSUFFICIENT"
        if free < 100 * 1024 * 1024:
            state, error_code = "INSUFFICIENT_SPACE", "FINANCE_INSUFFICIENT_SPACE"
        source_ui = Path(__file__).resolve().parents[2] / "ui" / "dist"
        installed_ui = Path(__file__).resolve().parent / "ui"
        ui_root = source_ui if source_ui.exists() else installed_ui
        integrity_path = Path(__file__).with_name("release_integrity.json")
        try:
            embedded = json.loads(integrity_path.read_text(encoding="utf-8"))
            if embedded.get("application_version") != __version__:
                state, error_code = "INCOMPATIBLE_VERSION", "FINANCE_RELEASE_VERSION_MISMATCH"
            verify_integrity_manifest(embedded, SCHEMA_ROOT, ui_root)
            checks["bundle_integrity"] = "VALID"
        except (OSError, json.JSONDecodeError, ReleaseIntegrityError):
            state, error_code = "BUNDLE_TAMPERED", "FINANCE_BUNDLE_TAMPERED"
            checks["bundle_integrity"] = "INVALID"
        return self._envelope(
            {
                "status": state,
                "error_code": error_code,
                "checks": checks,
                "read_only_diagnostics_available": True,
                "recovery_document": "RECOVERY.md",
            },
            status="READY" if state == "READY" else "ERROR",
        )

    def _capabilities(self, _: dict[str, Any]) -> dict[str, Any]:
        local_ai = self._local_model_service.status()
        return self._envelope(
            {
                "extension_version": __version__,
                "product_version": PRODUCT_VERSION,
                "contract_version": CONTRACT_VERSION,
                "store_schema_version": self._store.schema_version(),
                "ui_contract_version": UI_CONTRACT_VERSION,
                "schema_version": "1.0.0",
                "projection_versions": PROJECTION_VERSIONS,
                "capabilities": {
                    "imports": True,
                    "multi_account_import": True,
                    "import_capability": True,
                    "multi_account_import_capability": True,
                    "balance_reconciliation_capability": True,
                    "position_reconciliation_capability": True,
                    "investment_funding_capability": True,
                    "security_transactions": True,
                    "classification": True,
                    "reconciliation": True,
                    "review_inbox": True,
                    "recurring_patterns": True,
                    "forecasting": True,
                    "forecast_evaluation": True,
                    "accounts": True,
                    "balances": True,
                    "liquidity": True,
                    "net_worth": True,
                    "wealth": True,
                    "backup": True,
                    "restore": True,
                    "data_export": True,
                    "migrations": True,
                    "period_control": True,
                    "account_workspace": True,
                    "tax": False,
                    "receipts": False,
                    "cloud_sync": False,
                    "external_models": False,
                    "local_ai": local_ai["available"],
                    "local_ai_import_mapping": local_ai["import_mapping"],
                    "ai_import_mapping": True,
                    "import_format_fingerprints": True,
                    "user_import_profiles": True,
                    "local_ai_category_suggestions": local_ai["category_suggestions"],
                    "local_ai_category_suggestion_review": True,
                    "local_ai_category_shadow_mode": True,
                    "local_ai_category_evaluation_replay": True,
                    "local_ai_automatic_classification": False,
                },
                "local_ai": local_ai,
            }
        )

    def _security_status(self, _: dict[str, Any]) -> dict[str, Any]:
        checks = {
            "data_path_local": "NOT_CHECKED",
            "git_path_protection": "NOT_CHECKED",
            "cloud_sync_check": "NOT_CHECKED",
            "keychain_available": "NOT_CHECKED",
            "snapshot_loaded": "NOT_CHECKED",
            "snapshot_integrity": "NOT_CHECKED",
            "network_egress_disabled": (
                "NOT_CHECKED"
                if self._network_egress_disabled is None
                else "PASSED"
                if self._network_egress_disabled
                else "FAILED"
            ),
            "schema_compatibility": "PASSED",
        }
        try:
            validate_runtime_path(
                self._store.data_dir, repository_roots=self._store.repository_roots
            )
            checks["data_path_local"] = "PASSED"
            checks["git_path_protection"] = "PASSED"
            checks["cloud_sync_check"] = "PASSED"
        except Exception:
            checks["data_path_local"] = "FAILED"
            checks["git_path_protection"] = "FAILED"
            checks["cloud_sync_check"] = "FAILED"
        try:
            self._store.connection
            checks["snapshot_loaded"] = "PASSED"
        except StoreInvariantError:
            checks["snapshot_loaded"] = "FAILED"
        try:
            self._store.events()
            checks["snapshot_integrity"] = "PASSED"
        except StoreInvariantError:
            checks["snapshot_integrity"] = "FAILED"
        if self._store.key_provider.__class__.__name__ != "StaticKeyProvider":
            try:
                self._store.key_provider.get_key()
                checks["keychain_available"] = "PASSED"
            except Exception:
                checks["keychain_available"] = "FAILED"
        return self._envelope(
            {
                "extension_version": __version__,
                "schema_version": "1.0.0",
                "checks": checks,
                "last_event_sequence": self._sequence(),
            }
        )

    def _dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "dashboard_read_model",
            "1.0.0",
            payload,
            lambda: self._dashboard_uncached(payload),
        )

    def _dashboard_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("mode") or isinstance(payload.get("period"), dict):
            period = self._resolve_period(payload)
        else:
            month = _require_month(payload)
            year_value, month_value = (int(value) for value in month.split("-"))
            period = resolve_period_from_payload(
                {"mode": "MONTH", "year": year_value, "month": month_value}
            )
        month = period["start_date"][:7]
        series = self._projection(
            "financial_time_series", {"period": period},
            lambda: financial_time_series(self._store, period),
        )
        cashflow = series["summary"]
        base = self._projection(
            "monthly_forecast", {"month": month}, lambda: monthly_forecast(self._store, month)
        ).get("BASE") if period["mode"] == "MONTH" else None
        liquidity = self._projection(
            "liquidity_overview", {"as_of": period["end_date"]},
            lambda: liquidity_overview(self._store, as_of=period["end_date"]),
        )
        worth = self._projection(
            "net_worth_overview", {"as_of": period["end_date"]},
            lambda: net_worth_overview(self._store, as_of=period["end_date"]),
        )
        projected = self._projection(
            "projected_month_end_balance", {"month": month},
            lambda: projected_month_end_balance(self._store, month),
        ) if period["mode"] == "MONTH" else {"projected_month_end_balance": None}
        reviews = self._projection(
            "review_inbox_summary", {},
            lambda: review_inbox(self._store, materialize_limit=0),
        )["total_open"]
        income = cashflow["effective_income"]
        savings_rate = (
            (Decimal(cashflow["net_cashflow"]) / Decimal(income) * 100).quantize(Decimal("0.01"))
            if income is not None and Decimal(income) > 0
            else None
        )
        stale = bool(liquidity["stale_account_ids"]) or any(
            item["freshness"] == "STALE" and item["include_in_net_worth"]
            for item in self._projection(
                "account_overview", {"as_of": period["end_date"]},
                lambda: account_overview(self._store, period["end_date"]),
            )
        )
        return self._envelope(
            {
                "month": month,
                "period": period,
                "effective_income": income,
                "effective_expenses": cashflow["effective_expenses"],
                "net_cashflow": cashflow["net_cashflow"],
                "expected_month_end_surplus": base["predicted_surplus"] if base else None,
                "remaining_expected_income": base["expected_income"] if base else "0",
                "remaining_expected_expenses": base["expected_fixed_expenses"] if base else "0",
                "savings_rate": str(savings_rate) if savings_rate is not None else None,
                "open_reviews": reviews,
                "liquid_balance": liquidity["liquid_funds"] if liquidity["accounts"] else None,
                "liquid_balance_as_of": liquidity["as_of"],
                "projected_month_end_balance": projected["projected_month_end_balance"],
                "net_worth": worth["net_worth"] if worth["source_snapshot_ids"] else None,
                "net_worth_as_of": worth["as_of"],
            },
            status="EMPTY" if self._sequence() == 0 else "STALE" if stale else "READY",
        )

    def _financial_time_series(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "financial_time_series_read_model",
            "1.0.0",
            payload,
            lambda: self._financial_time_series_uncached(payload),
        )

    def _financial_time_series_uncached(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        period = self._resolve_period(payload)
        data = financial_time_series(self._store, period)
        return self._envelope(
            data,
            status="READY" if data["summary"]["has_data"] else "EMPTY",
            projection_version=PROJECTION_VERSIONS["financial_time_series"],
        )

    def _transactions(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "transaction_read_model",
            "1.0.0",
            payload,
            lambda: self._transactions_uncached(payload),
        )

    def _transactions_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = payload.get("month")
        if payload.get("mode") or isinstance(payload.get("period"), dict):
            period = self._resolve_period(payload)
            period_start = period["start_date"]
            period_end = period["end_date"]
        else:
            period_start = f"{month}-01" if isinstance(month, str) and month else None
            period_end = f"{month}-31" if isinstance(month, str) and month else None
        category_ids = payload.get("category_ids")
        if not category_ids:
            sequence = self._sequence()
            limit, marker, fingerprint = _pagination_parameters(
                "ListTransactions", payload, sequence
            )
            if marker is not None and len(marker) != 2:
                raise ApplicationContractError("QUERY_CURSOR_INVALID")
            page_events = self._store.transaction_events_page(
                period_start=period_start,
                period_end=period_end,
                limit=limit + 1,
                after=(marker[0], marker[1]) if marker else None,
                account_ids=payload.get("account_ids")
                if isinstance(payload.get("account_ids"), list)
                else None,
                direction=str(payload["direction"]) if payload.get("direction") else None,
                search=str(payload["search"]).strip() if payload.get("search") else None,
            )
            has_more = len(page_events) > limit
            page_events = page_events[:limit]
            transaction_payloads = {
                event["payload"]["transaction_id"]: event["payload"] for event in page_events
            }
            reconciled_page = reconciled_transactions(self._store, transaction_payloads)
            classifications_page = active_classifications(self._store)
            raw_sources_page = {
                event["event_id"]: event["payload"]
                for event in self._store.events_by_ids(
                    [
                        str(item.get("raw_transaction_event_id"))
                        for item in transaction_payloads.values()
                        if item.get("raw_transaction_event_id")
                    ]
                )
            }
            rows = []
            for event in page_events:
                item = event["payload"]
                classification = classifications_page.get(item["transaction_id"])
                raw_source = raw_sources_page.get(item.get("raw_transaction_event_id"), {})
                rows.append(
                    {
                        **item,
                        **({"export_id": item.get("export_id") or raw_source.get("export_id") or raw_source.get("import_batch_id")} if item.get("export_id") or raw_source.get("export_id") or raw_source.get("import_batch_id") else {}),
                        **({"section_id": item.get("section_id") or raw_source.get("section_id")} if item.get("section_id") or raw_source.get("section_id") else {}),
                        "category_code": (
                            classification["payload"]["category_code"]
                            if classification
                            and classification["event_type"] == "TransactionClassificationConfirmed"
                            else "UNCLASSIFIED"
                        ),
                        **{
                            key: str(value) if isinstance(value, Decimal) else value
                            for key, value in reconciled_page[item["transaction_id"]].items()
                        },
                    }
                )
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = _encode_cursor(
                    {
                        "v": PAGINATION_CONTRACT_VERSION,
                        "sequence": sequence,
                        "fingerprint": fingerprint,
                        "last": [
                            str(last.get("booking_date", "")),
                            str(last.get("transaction_id", "")),
                        ],
                    }
                )
            return self._envelope(
                {
                    "transactions": rows,
                    "items": rows,
                    "next_cursor": next_cursor,
                    "has_more": has_more,
                    "page_size": limit,
                    "pagination_version": PAGINATION_CONTRACT_VERSION,
                },
                status="EMPTY" if not rows else "READY",
            )
        reconciled = reconciled_transactions(self._store)
        classifications = active_classifications(self._store)
        raw_sources = {
            event["event_id"]: event["payload"]
            for event in self._store.events("RawTransactionImported")
        }
        rows = []
        for event in self._store.events("TransactionNormalized"):
            item = event["payload"]
            if period_start and period_end and not period_start <= item["booking_date"] <= period_end:
                continue
            classification = classifications.get(item["transaction_id"])
            raw_source = raw_sources.get(item.get("raw_transaction_event_id"), {})
            export_id = item.get("export_id") or raw_source.get("export_id") or raw_source.get("import_batch_id")
            section_id = item.get("section_id") or raw_source.get("section_id")
            row = {
                    **item,
                    **({"export_id": export_id} if export_id else {}),
                    **({"section_id": section_id} if section_id else {}),
                    "category_code": (
                        classification["payload"]["category_code"]
                        if classification
                        and classification["event_type"] == "TransactionClassificationConfirmed"
                        else "UNCLASSIFIED"
                    ),
                    **{
                        key: str(value) if isinstance(value, Decimal) else value
                        for key, value in reconciled[item["transaction_id"]].items()
                    },
                }
            account_ids = payload.get("account_ids")
            category_ids = payload.get("category_ids")
            if isinstance(account_ids, list) and account_ids and row.get("account_id") not in account_ids:
                continue
            if isinstance(category_ids, list) and category_ids and row["category_code"] not in category_ids:
                continue
            if payload.get("direction") and row.get("direction") != payload["direction"]:
                continue
            search = str(payload.get("search", "")).strip().casefold()
            if search and search not in " ".join(
                str(row.get(key, ""))
                for key in ("counterparty", "normalized_description", "description")
            ).casefold():
                continue
            rows.append(row)
        rows.sort(
            key=lambda row: (
                str(row.get("booking_date", "")), str(row.get("transaction_id", ""))
            ),
            reverse=True,
        )
        page, next_cursor, has_more, page_size = _paginate(
            rows,
            query_name="ListTransactions",
            payload=payload,
            sequence=self._sequence(),
            identity=lambda row: (
                str(row.get("booking_date", "")), str(row.get("transaction_id", ""))
            ),
        )
        return self._envelope(
            {
                "transactions": page,
                "items": page,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "page_size": page_size,
                "pagination_version": PAGINATION_CONTRACT_VERSION,
            },
            status="EMPTY" if not page else "READY",
        )

    def _transaction_details(self, payload: dict[str, Any]) -> dict[str, Any]:
        transaction_id = payload.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ApplicationContractError("FINANCE_TRANSACTION_ID_REQUIRED")
        normalized = next(
            (
                event
                for event in self._store.events("TransactionNormalized")
                if event["payload"]["transaction_id"] == transaction_id
            ),
            None,
        )
        if normalized is None:
            return self._envelope(None, status="EMPTY")

        classification = active_classifications(self._store).get(transaction_id)
        reconciliation = reconciled_transactions(self._store).get(transaction_id, {})
        related_events = [
            {
                "sequence_number": event["sequence_number"],
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "occurred_at": event["occurred_at"],
            }
            for event in self._store.events()
            if event["aggregate_id"] == transaction_id
            or transaction_id
            in {
                str(event["payload"].get("transaction_id", "")),
                str(event["payload"].get("outgoing_transaction_id", "")),
                str(event["payload"].get("incoming_transaction_id", "")),
                str(event["payload"].get("refund_transaction_id", "")),
                str(event["payload"].get("original_transaction_id", "")),
            }
        ]
        data = {
            **normalized["payload"],
            "category_code": (
                classification["payload"]["category_code"]
                if classification
                and classification["event_type"] == "TransactionClassificationConfirmed"
                else "UNCLASSIFIED"
            ),
            "classification": classification,
            "reconciliation": {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in reconciliation.items()
            },
            "event_history": related_events,
        }
        return self._envelope(data)

    def _accounts(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = account_overview(self._store, payload.get("as_of"))
        state = (
            "EMPTY"
            if not rows
            else "STALE"
            if any(row["freshness"] == "STALE" for row in rows)
            else "READY"
        )
        return self._envelope({"accounts": rows}, status=state)

    def _account(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        row = next(
            (
                item
                for item in account_overview(self._store, payload.get("as_of"))
                if item["account_id"] == account_id
            ),
            None,
        )
        if row is None:
            return self._envelope(None, status="EMPTY")
        return self._envelope(
            {
                "account": row,
                "balance_history": account_balance_history(self._store, account_id),
                "reconciliation": balance_reconciliations(self._store).get(account_id),
            },
            status="STALE" if row["freshness"] == "STALE" else "READY",
        )

    def _account_balance_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        rows = account_balance_history(self._store, account_id)
        return self._envelope(
            {"account_id": account_id, "snapshots": rows},
            status="EMPTY" if not rows else "READY",
        )

    def _balance_reconciliation(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        row = balance_reconciliations(self._store).get(account_id)
        return self._envelope(row, status="EMPTY" if row is None else "READY")

    def _resolve_period(self, payload: dict[str, Any]) -> dict[str, Any]:
        has_explicit_period = bool(
            payload.get("period") or payload.get("mode") or payload.get("start_date") or payload.get("year")
        )
        try:
            return resolve_period_from_payload(payload) if has_explicit_period else default_month_period()
        except PeriodSelectionError as exc:
            raise ApplicationContractError(str(exc)) from exc

    def _available_periods(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(
            available_periods(self._store),
            projection_version=PROJECTION_VERSIONS["available_periods"],
        )

    def _account_overviews(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "account_read_model",
            "1.0.0",
            payload,
            lambda: self._account_overviews_uncached(payload),
        )

    def _account_overviews_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        period = self._resolve_period(payload)
        rows = account_overview_for_period(
            self._store,
            period,
            account_types=payload.get("account_types"),
            status=payload.get("status"),
            currency=payload.get("currency"),
            include_closed=bool(payload.get("include_closed", False)),
        )
        state = (
            "EMPTY"
            if not rows
            else "STALE"
            if any(row["data_freshness"] == "STALE" for row in rows)
            else "READY"
        )
        return self._envelope(
            {"period": period, "accounts": rows},
            status=state,
            projection_version=PROJECTION_VERSIONS["account_overview"],
        )

    def _account_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "account_detail_read_model",
            "1.0.0",
            payload,
            lambda: self._account_detail_uncached(payload),
        )

    def _account_detail_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload)
        row = next(
            (
                item
                for item in account_overview_for_period(
                    self._store, period, include_closed=True
                )
                if item["account_id"] == account_id
            ),
            None,
        )
        if row is None:
            raise ApplicationContractError("FINANCE_ACCOUNT_NOT_FOUND")
        summary = account_period_summary(self._store, account_id, period)
        data = {
            "account": row,
            "overview_status": row["overview_status"],
            "period": period,
            "period_summary": summary,
            "balance_history": account_balance_ledger(self._store, account_id, period),
            "reconciliation": balance_reconciliations(self._store).get(account_id),
            "open_review_count": account_review_counts(self._store).get(account_id, 0),
            "open_reviews": account_open_reviews(self._store, account_id),
        }
        return self._envelope(
            data,
            status="STALE" if row["data_freshness"] == "STALE" else "READY",
            projection_version=PROJECTION_VERSIONS["account_detail"],
        )

    def _account_period_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload)
        data = account_period_summary(self._store, account_id, period)
        return self._envelope(data, projection_version=PROJECTION_VERSIONS["account_period_summary"])

    def _account_transactions_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload)
        rows = account_transactions_for_period(
            self._store,
            account_id,
            period,
            category_code=payload.get("category_code"),
            direction=payload.get("direction"),
            import_month=payload.get("import_month"),
        )
        return self._envelope(
            {"account_id": account_id, "period": period, "transactions": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_transactions"],
        )

    def _account_reconciliations_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload) if payload.get("period") or payload.get("mode") else None
        rows = account_reconciliations_for_period(self._store, account_id, period)
        status_filter = payload.get("status")
        if status_filter:
            rows = [row for row in rows if row["status"] == status_filter]
        return self._envelope(
            {"account_id": account_id, "reconciliations": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_reconciliations"],
        )

    def _account_imports_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        rows = account_imports(self._store, account_id)
        return self._envelope(
            {"account_id": account_id, "imports": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_imports"],
        )

    def _account_positions_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        rows = account_positions(self._store, account_id)
        return self._envelope(
            {"account_id": account_id, "positions": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_positions"],
        )

    def _account_position_history_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload) if payload.get("period") or payload.get("mode") else None
        rows = account_position_history(self._store, account_id, period)
        return self._envelope(
            {"account_id": account_id, "period": period, "transactions": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_position_history"],
        )

    def _account_audit_trail_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        period = self._resolve_period(payload) if payload.get("period") or payload.get("mode") else None
        rows = account_audit_trail(self._store, account_id, period)
        return self._envelope(
            {"account_id": account_id, "period": period, "audit_history": rows},
            status="EMPTY" if not rows else "READY",
            projection_version=PROJECTION_VERSIONS["account_audit_trail"],
        )

    def _liquidity(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = liquidity_overview(
            self._store, payload.get("valuation_currency", "EUR"), payload.get("as_of")
        )
        state = (
            "PARTIAL"
            if data["currency_conflicts"]
            else "STALE"
            if data["stale_account_ids"]
            else "READY"
        )
        return self._envelope(data, status=state)

    def _net_worth(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = net_worth_overview(
            self._store, payload.get("valuation_currency", "EUR"), payload.get("as_of")
        )
        state = "PARTIAL" if data["currency_conflicts"] else "READY"
        return self._envelope(data, status=state)

    def _net_worth_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = net_worth_history(self._store, payload.get("valuation_currency", "EUR"))
        return self._envelope({"history": rows}, status="EMPTY" if not rows else "READY")

    def _liabilities(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = liability_overview(self._store, payload.get("valuation_currency", "EUR"))
        return self._envelope(data, status="PARTIAL" if data["currency_conflicts"] else "READY")

    def _asset_allocation(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = asset_allocation(self._store, payload.get("valuation_currency", "EUR"))
        return self._envelope(data, status="PARTIAL" if data["currency_conflicts"] else "READY")

    def _projected_balance(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = projected_month_end_balance(self._store, _require_month(payload))
        state = data["status"] if data["status"] in {"READY", "STALE"} else "EMPTY"
        return self._envelope(data, status=state)

    def _account_reviews(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = account_reviews(self._store, payload.get("as_of"))
        return self._envelope({"reviews": rows}, status="EMPTY" if not rows else "READY")

    def _review_inbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "review_read_model",
            "1.0.0",
            payload,
            lambda: self._review_inbox_uncached(payload),
        )

    def _review_inbox_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = review_inbox(self._store)
        page, next_cursor, has_more, page_size = _paginate(
            data["items"],
            query_name="GetReviewInbox",
            payload=payload,
            sequence=self._sequence(),
            identity=lambda row: (str(row["review_id"]),),
        )
        data.update(
            {
                "items": page,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "page_size": page_size,
                "pagination_version": PAGINATION_CONTRACT_VERSION,
            }
        )
        return self._envelope(
            data,
            status="EMPTY" if data["total_open"] == 0 else "READY",
            projection_version=PROJECTION_VERSIONS["review_inbox"],
        )

    def _category_breakdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "category_period_summary",
            "1.0.0",
            payload,
            lambda: self._category_breakdown_uncached(payload),
        )

    def _category_breakdown_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = _require_month(payload)
        result = reconciled_category_breakdown(self._store, month)
        for item in result["categories"].values():
            for key in ("gross_expense", "refund_amount", "effective_expense"):
                item[key] = str(item[key])
        return self._envelope(result, status="EMPTY" if not result["categories"] else "READY")

    def _categories(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope({"categories": categories(self._store)})

    def _classification_reviews(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = classification_review(self._store)
        return self._envelope({"reviews": rows}, status="EMPTY" if not rows else "READY")

    def _reconciliation_reviews(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get("type", "duplicates")
        rows = relation_review(self._store, kind)
        return self._envelope(
            {"type": kind, "reviews": rows}, status="EMPTY" if not rows else "READY"
        )

    def _recurring_patterns(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = list(recurring_patterns(self._store).values())
        return self._envelope({"patterns": rows}, status="EMPTY" if not rows else "READY")

    def _recurring_pattern(self, payload: dict[str, Any]) -> dict[str, Any]:
        pattern = recurring_patterns(self._store).get(payload.get("pattern_id", ""))
        if not pattern:
            return self._envelope(None, status="EMPTY")
        expected = [
            item
            for item in expected_transactions(self._store).values()
            if item["recurring_pattern_id"] == pattern["pattern_id"]
        ]
        return self._envelope({"pattern": pattern, "expected_transactions": expected})

    def _expected_transactions(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = payload.get("status")
        rows = [
            item
            for item in expected_transactions(self._store).values()
            if not status or item["status"] == status
        ]
        return self._envelope(
            {"expected_transactions": rows}, status="EMPTY" if not rows else "READY"
        )

    def _forecast(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = _require_month(payload)
        result = monthly_forecast(self._store, month)
        return self._envelope(
            {"month": month, "scenarios": result}, status="EMPTY" if not result else "READY"
        )

    def _forecast_versions(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = _require_month(payload)
        rows = [
            event
            for event in self._store.events()
            if event["event_type"] in {"ForecastCreated", "ForecastSuperseded", "ForecastEvaluated"}
            and event["payload"]["period_start"].startswith(month)
        ]
        return self._envelope(
            {"month": month, "versions": rows}, status="EMPTY" if not rows else "READY"
        )

    def _forecast_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = _require_month(payload)
        rows = [
            item
            for item in forecast_accuracy(self._store)
            if item["period_start"].startswith(month)
        ]
        return self._envelope(
            {"month": month, "evaluations": rows}, status="EMPTY" if not rows else "READY"
        )

    def _import_batches(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in self._store.connection.execute(
                "SELECT import_id, content_hash, parser_version, status, created_at FROM import_files ORDER BY created_at DESC"
            ).fetchall()
        ]
        return self._envelope({"imports": rows}, status="EMPTY" if not rows else "READY")

    def _import_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._store.connection.execute(
            "SELECT import_id, content_hash, parser_version, status, created_at FROM import_files WHERE import_id=?",
            (payload.get("import_id"),),
        ).fetchone()
        return self._envelope(dict(row) if row else None, status="READY" if row else "EMPTY")

    def _import_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = get_import_analysis(
            self._store, _require_identifier(payload, "analysis_id")
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_wizard_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = import_wizard_state(self._store, payload.get("export_id"))
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_execution_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = import_execution_result(
            self._store, _require_identifier(payload, "export_id")
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "import_read_model",
            "1.0.0",
            payload,
            lambda: self._import_history_uncached(payload),
        )

    def _import_history_uncached(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = import_history(self._store)
        return self._envelope(
            {"imports": rows}, status="READY" if rows else "EMPTY"
        )

    def _import_history_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = import_history_detail(
            self._store, _require_identifier(payload, "export_id")
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _bank_monthly_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = get_bank_monthly_export(
            self._store, _require_identifier(payload, "export_id")
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_sections(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = list_import_sections(
            self._store, _require_identifier(payload, "analysis_id")
        )
        return self._envelope(
            {"sections": rows}, status="READY" if rows else "EMPTY"
        )

    def _import_section_bindings(self, payload: dict[str, Any]) -> dict[str, Any]:
        bank_identifier = payload.get("bank_identifier")
        rows = [
            item
            for item in section_bindings(self._store).values()
            if not bank_identifier or item["bank_identifier"] == bank_identifier
        ]
        return self._envelope(
            {"bindings": rows}, status="READY" if rows else "EMPTY"
        )

    def _import_section_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(
            get_import_section_preview(
                self._store,
                _require_identifier(payload, "analysis_id"),
                _require_identifier(payload, "section_id"),
            )
        )

    def _initial_balance_requirements(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = initial_balance_requirements(
            self._store, _require_identifier(payload, "analysis_id")
        )
        return self._envelope(
            {"requirements": rows}, status="READY" if rows else "EMPTY"
        )

    def _imported_period_reconciliation(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        rows = [
            item
            for item in imported_period_reconciliations(self._store).values()
            if item["account_id"] == account_id
            and (not payload.get("period_start") or item["period_start"] == payload["period_start"])
            and (not payload.get("period_end") or item["period_end"] == payload["period_end"])
        ]
        row = rows[-1] if rows else None
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _imported_period_reconciliation_context(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._envelope(
            balance_reconciliation_context(
                self._store,
                account_id=_require_identifier(payload, "account_id"),
                period_start=_require_identifier(payload, "period_start"),
                period_end=_require_identifier(payload, "period_end"),
                section_id=payload.get("section_id"),
            )
        )

    def _imported_security_position_reconciliation(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        account_id = _require_identifier(payload, "account_id")
        rows = [
            item
            for item in imported_security_position_reconciliations(
                self._store
            ).values()
            if item["account_id"] == account_id
            and (
                not payload.get("period_start")
                or item["period_start"] == payload["period_start"]
            )
            and (
                not payload.get("period_end")
                or item["period_end"] == payload["period_end"]
            )
        ]
        row = rows[-1] if rows else None
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _position_reconciliation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(
            position_reconciliation_context(
                self._store,
                account_id=_require_identifier(payload, "account_id"),
                period_start=_require_identifier(payload, "period_start"),
                period_end=_require_identifier(payload, "period_end"),
                section_id=payload.get("section_id"),
            )
        )

    def _investment_funding_relations(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = payload.get("status")
        rows = investment_funding_relation_contexts(
            self._store,
            export_id=payload.get("export_id"),
            status=status,
        )
        return self._envelope(
            {"relations": rows}, status="READY" if rows else "EMPTY"
        )

    def _security_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = security_transactions(self._store).get(
            _require_identifier(payload, "transaction_id")
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _security_positions(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = payload.get("account_id")
        rows = [
            item
            for item in security_positions(self._store).values()
            if not account_id or item["account_id"] == account_id
        ]
        return self._envelope(
            {"positions": rows}, status="READY" if rows else "EMPTY"
        )

    def _backups(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = list_backups(
            self._store, self._archive_key_provider, self._backup_directory
        )
        state = (
            "EMPTY"
            if not rows
            else "PARTIAL"
            if any(row["verification_status"] != "VALID" for row in rows)
            else "READY"
        )
        return self._envelope({"backups": rows}, status=state)

    def _store_integrity(self, _: dict[str, Any]) -> dict[str, Any]:
        result = validate_store_integrity(self._store)
        return self._envelope(result, status="READY" if result["status"] == "VALID" else "ERROR")

    def _key_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(key_status(self._store, self._archive_key_provider))

    def _migration_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(migration_status(self._store))

    def _projection_status(self, _: dict[str, Any]) -> dict[str, Any]:
        event_sequence = self._sequence()
        rows = [
            {
                **dict(row),
                "freshness_status": (
                    "CURRENT"
                    if int(row["last_applied_sequence"]) == event_sequence
                    and row["status"] == "CURRENT"
                    else "STALE"
                ),
            }
            for row in self._store.connection.execute(
                "SELECT projection_name,projection_version,last_applied_sequence,status,updated_at "
                "FROM projection_registry ORDER BY projection_name"
            ).fetchall()
        ]
        return self._envelope(
            {"event_store_sequence": event_sequence, "projections": rows}
        )

    def _local_model_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(self._local_model_service.status())

    def _import_format_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_id = _require_identifier(payload, "analysis_id")
        row = get_import_format_status(self._store, analysis_id)
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_mapping_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        proposal_id = _require_identifier(payload, "proposal_id")
        row = get_import_mapping_proposal(self._store, proposal_id)
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_mapping_validation(self, payload: dict[str, Any]) -> dict[str, Any]:
        proposal_id = _require_identifier(payload, "proposal_id")
        row = next(
            (
                event["payload"]
                for event in reversed(self._store.events("ImportMappingValidationCompleted"))
                if event["aggregate_id"] == proposal_id
            ),
            None,
        )
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_profiles(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = list_import_profiles(self._store)
        return self._envelope(
            {"profiles": rows}, status="READY" if rows else "EMPTY"
        )

    def _import_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = _require_identifier(payload, "profile_id")
        row = get_import_profile(self._store, profile_id)
        return self._envelope(row, status="READY" if row else "EMPTY")

    def _import_profile_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = _require_identifier(payload, "profile_id")
        rows = get_import_profile_history(self._store, profile_id)
        return self._envelope(
            {"profile_id": profile_id, "events": rows},
            status="READY" if rows else "EMPTY",
        )

    def _format_change_details(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._import_format_status(payload)

    def _category_shadow_status(self, _: dict[str, Any]) -> dict[str, Any]:
        data = category_shadow_status(self._store)
        data["local_model"] = self._local_model_service.status()
        return self._envelope(data, projection_version="1.0.0")

    def _category_evaluation_preview(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(preview_category_evaluation_run(self._store))

    def _category_evaluation_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        filters = {
            key: str(payload[key])
            for key in ("model_id", "model_version", "policy_version", "taxonomy_version")
            if payload.get(key)
        }
        return self._envelope(
            category_evaluation_summary(self._store, filters),
            projection_version="1.0.0",
        )

    def _category_evaluation_by_category(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary = self._category_evaluation_summary(payload)
        summary["data"] = {
            "categories": summary["data"]["categories"],
            "minimum_sample": summary["data"]["minimum_sample"],
            "filters": summary["data"]["filters"],
        }
        return summary

    def _category_evaluation_thresholds(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary = self._category_evaluation_summary(payload)
        summary["data"] = {
            "thresholds": summary["data"]["thresholds"],
            "confidence_kind": summary["data"]["confidence_kind"],
            "minimum_sample": summary["data"]["minimum_sample"],
            "filters": summary["data"]["filters"],
        }
        return summary

    def _category_evaluation_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = _require_identifier(payload, "run_id")
        return self._envelope(get_category_evaluation_run(self._store, run_id))

    def _category_evaluation_runs(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope({"runs": list_category_evaluation_runs(self._store)})

    def _category_suggestion_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        suggestion_id = _require_identifier(payload, "suggestion_id")
        data = get_category_suggestion_detail(self._store, suggestion_id)
        return self._envelope(
            data,
            status="READY" if data else "EMPTY",
            projection_version=PROJECTION_VERSIONS["category_suggestion_review"],
        )

    def _transaction_category_suggestion(self, payload: dict[str, Any]) -> dict[str, Any]:
        transaction_id = _require_identifier(payload, "transaction_id")
        data = get_transaction_category_suggestion(self._store, transaction_id)
        return self._envelope(
            data,
            status="READY" if data else "EMPTY",
            projection_version=PROJECTION_VERSIONS["category_suggestion_review"],
        )

    def _pending_category_suggestions(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "category_suggestion_read_model",
            "1.0.0",
            payload,
            lambda: self._pending_category_suggestions_uncached(payload),
        )

    def _pending_category_suggestions_uncached(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        filters = {
            key: payload[key]
            for key in (
                "account_id",
                "period",
                "category_code",
                "confidence_min",
                "confidence_max",
                "status",
            )
            if payload.get(key) is not None
        }
        filters.setdefault("status", "PENDING")
        rows = list_category_suggestions(self._store, filters)
        page, next_cursor, has_more, page_size = _paginate(
            rows,
            query_name="ListPendingCategorySuggestions",
            payload=payload,
            sequence=self._sequence(),
            identity=lambda row: (str(row.get("suggestion_id", "")),),
        )
        return self._envelope(
            {
                "suggestions": page,
                "items": page,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "page_size": page_size,
                "pagination_version": PAGINATION_CONTRACT_VERSION,
            },
            status="READY" if page else "EMPTY",
            projection_version=PROJECTION_VERSIONS["category_suggestion_review"],
        )

    def _category_suggestion_review_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persisted_response(
            "category_suggestion_summary_read_model",
            "1.0.0",
            payload,
            lambda: self._category_suggestion_review_summary_uncached(payload),
        )

    def _category_suggestion_review_summary_uncached(
        self, _: dict[str, Any]
    ) -> dict[str, Any]:
        return self._envelope(
            category_suggestion_review_summary(self._store),
            projection_version=PROJECTION_VERSIONS["category_suggestion_review"],
        )

    def _classification_rules(self, _: dict[str, Any]) -> dict[str, Any]:
        rows = list_classification_rules_with_origin(self._store)
        return self._envelope(
            {"rules": rows}, status="READY" if rows else "EMPTY"
        )

    def _execute_command(self, command_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        required = COMMAND_REQUIRED_FIELDS.get(command_name, ())
        if any(field not in payload for field in required):
            raise ApplicationContractError("FINANCE_COMMAND_PAYLOAD_INVALID")
        result: Any
        if command_name == "ImportTransactions":
            batch = import_csv(self._store, payload["source_file_path"], payload["account_id"])
            result = {
                "import_batch_id": batch,
                "normalized_count": normalize_batch(self._store, batch),
            }
        elif command_name == "AnalyzeImportFile":
            source_path = payload.get("source_file_path")
            source_reference = payload.get("source_file_reference")
            if source_path is None and source_reference and self._import_file_resolver:
                source_path = self._import_file_resolver(source_reference)
            if source_path is None:
                raise ApplicationContractError("FINANCE_IMPORT_FILE_REFERENCE_INVALID")
            result = analyze_import_file(
                self._store,
                source_path,
                payload["requested_profile"],
                payload.get("confirmed_bank_identifier"),
            )
        elif command_name == "AnalyzeUnknownImportFormat":
            source_path = payload.get("source_file_path")
            source_reference = payload.get("source_file_reference")
            if source_path is None and source_reference and self._import_file_resolver:
                source_path = self._import_file_resolver(source_reference)
            if source_path is None:
                raise ApplicationContractError("FINANCE_IMPORT_FILE_REFERENCE_INVALID")
            result = analyze_unknown_import_format(
                self._store,
                self._local_model_service,
                source_path,
                bank_identifier=payload["bank_identifier"],
                regenerate=bool(payload.get("regenerate", False)),
            )
        elif command_name == "ValidateImportMapping":
            result = validate_mapping_for_proposal(
                self._store,
                payload["proposal_id"],
                payload["mapping"],
                persist=True,
            )
        elif command_name == "ConfirmImportMapping":
            result = confirm_import_mapping(
                self._store,
                payload["proposal_id"],
                payload["final_mapping"],
                validation_result_id=payload["validation_result_id"],
                confirmation_comment=payload.get("confirmation_comment"),
                compatibility_rules=payload.get("compatibility_rules"),
            )
        elif command_name == "RejectImportMapping":
            result = reject_import_mapping(
                self._store, payload["proposal_id"], payload.get("reason")
            )
        elif command_name == "MapImportSections":
            result = map_import_sections(
                self._store, payload["analysis_id"], payload["section_mappings"]
            )
        elif command_name == "ImportMappedSections":
            result = import_mapped_sections(
                self._store,
                payload["analysis_id"],
                parser_profile=payload["parser_profile"],
                parser_version=payload["parser_version"],
                import_mode=payload["import_mode"],
            )
        elif command_name == "RecordOpeningBalance":
            result = record_opening_balance(self._store, **payload)
        elif command_name == "RecordClosingBalance":
            result = record_closing_balance(self._store, **payload)
        elif command_name == "RecordOpeningSecurityPosition":
            result = record_opening_security_position(self._store, **payload)
        elif command_name == "ConfirmEmptyOpeningSecurityPositions":
            result = confirm_empty_opening_security_positions(self._store, **payload)
        elif command_name == "RecordClosingSecurityPosition":
            result = record_closing_security_position(self._store, **payload)
        elif command_name == "ReconcileImportedPeriodBalance":
            result = reconcile_imported_period_balance(self._store, **payload)
        elif command_name == "ReconcileImportedSecurityPositions":
            result = reconcile_imported_security_positions(self._store, **payload)
        elif command_name == "ReconcileImportedPeriodPositions":
            result = reconcile_imported_period_positions(self._store, **payload)
        elif command_name == "CorrectSecurityPositionSnapshot":
            result = correct_security_position_snapshot(self._store, **payload)
        elif command_name == "DocumentBalanceDifference":
            result = document_balance_difference(self._store, **payload)
        elif command_name == "DetectInvestmentFundingRelations":
            result = detect_investment_funding_relations(self._store)
        elif command_name == "ConfirmInvestmentFundingRelation":
            result = confirm_investment_funding_relation(
                self._store, payload["relation_id"]
            )
        elif command_name == "RejectInvestmentFundingRelation":
            result = reject_investment_funding_relation(
                self._store, payload["relation_id"]
            )
        elif command_name == "BreakInvestmentFundingRelation":
            result = break_investment_funding_relation(
                self._store, payload["relation_id"]
            )
        elif command_name == "ClassifyTransactions":
            result = classify_transactions(self._store, payload.get("month"))
            try:
                generate_visible_category_suggestions(
                    self._store, self._local_model_service
                )
            except Exception:
                # Shadow inference is never a Finance-Core availability boundary.
                pass
        elif command_name == "ConfirmClassification":
            result = confirm_classification(
                self._store, payload["transaction_id"], payload["category_code"]
            )
            try:
                evaluate_category_suggestions(self._store, payload["transaction_id"])
            except Exception:
                # A failed evaluation must not roll back the user classification.
                pass
        elif command_name == "RejectClassification":
            result = reject_classification(self._store, payload["transaction_id"])
        elif command_name == "CreateClassificationRule":
            result = create_rule(self._store, **payload)
        elif command_name == "CreateCategory":
            result = create_category(self._store, **payload)
        elif command_name == "RenameCategory":
            result = rename_category(self._store, payload["category_code"], payload["display_name"])
        elif command_name == "ArchiveCategory":
            result = set_category_archived(self._store, payload["category_code"], True)
        elif command_name == "RestoreCategory":
            result = set_category_archived(self._store, payload["category_code"], False)
        elif command_name == "Reconcile":
            result = reconcile(self._store, payload.get("month"))
        elif command_name == "DetectDuplicates":
            result = detect_duplicates(self._store, payload.get("month"))
        elif command_name == "DetectTransfers":
            result = detect_transfers(self._store, payload.get("month"))
        elif command_name == "DetectRefunds":
            result = detect_refunds(self._store, payload.get("month"))
        elif command_name == "ConfirmDuplicate":
            result = confirm_duplicate(self._store, payload["relation_id"])
        elif command_name == "RejectDuplicate":
            result = reject_duplicate(self._store, payload["relation_id"])
        elif command_name == "ConfirmTransfer":
            result = confirm_transfer(self._store, payload["outgoing_id"], payload["incoming_id"])
        elif command_name == "RejectTransfer":
            result = reject_transfer(self._store, payload["outgoing_id"], payload["incoming_id"])
        elif command_name == "BreakTransferMatch":
            result = break_transfer(self._store, payload["outgoing_id"], payload["incoming_id"])
        elif command_name == "ConfirmRefund":
            result = confirm_refund(
                self._store, payload["refund_id"], payload["original_id"], payload["amount"]
            )
        elif command_name == "RejectRefund":
            result = reject_refund(self._store, payload["refund_id"], payload["original_id"])
        elif command_name == "DetectRecurringPatterns":
            result = detect_recurring_patterns(
                self._store, payload["from_month"], payload["to_month"]
            )
        elif command_name == "ConfirmRecurringPattern":
            result = confirm_recurring_pattern(self._store, payload["pattern_id"])
        elif command_name == "RejectRecurringPattern":
            result = reject_recurring_pattern(self._store, payload["pattern_id"])
        elif command_name == "UpdateRecurringPattern":
            result = update_recurring_pattern(
                self._store,
                payload["pattern_id"],
                amount=payload["amount"],
                day_from=payload["day_from"],
                day_to=payload["day_to"],
            )
        elif command_name == "PauseRecurringPattern":
            result = pause_recurring_pattern(self._store, payload["pattern_id"])
        elif command_name == "EndRecurringPattern":
            result = end_recurring_pattern(self._store, payload["pattern_id"])
        elif command_name == "CreateForecast":
            result = create_forecasts(self._store, payload["month"])
        elif command_name == "EvaluateForecast":
            result = evaluate_forecast(self._store, payload["month"])
        elif command_name == "CreateAccount":
            result = create_account(self._store, **payload)
        elif command_name == "UpdateAccount":
            account_id = payload["account_id"]
            result = update_account(
                self._store,
                account_id,
                **{key: value for key, value in payload.items() if key != "account_id"},
            )
        elif command_name == "CloseAccount":
            result = close_account(self._store, payload["account_id"], payload["closed_at"])
        elif command_name == "RecordBalanceSnapshot":
            result = record_balance_snapshot(self._store, **payload)
        elif command_name == "CorrectBalanceSnapshot":
            result = correct_balance_snapshot(
                self._store,
                payload["snapshot_id"],
                booked_balance=payload["booked_balance"],
                available_balance=payload.get("available_balance"),
                reason=payload["reason"],
            )
        elif command_name == "ReconcileAccountBalance":
            result = reconcile_account_balance(self._store, payload["account_id"])
        elif command_name == "CreateAssetSnapshot":
            result = create_asset_snapshot(self._store, **payload)
        elif command_name == "CorrectAssetSnapshot":
            result = correct_asset_snapshot(
                self._store, payload["snapshot_id"], payload["amount"], payload["reason"]
            )
        elif command_name == "CreateLiabilitySnapshot":
            result = create_liability_snapshot(self._store, **payload)
        elif command_name == "CorrectLiabilitySnapshot":
            result = correct_liability_snapshot(
                self._store, payload["snapshot_id"], payload["amount"], payload["reason"]
            )
        elif command_name == "CreateBackup":
            result = create_backup(
                self._store, self._archive_key_provider, self._backup_directory
            )
        elif command_name == "VerifyBackup":
            verified = verify_archive(
                payload["archive_path"],
                self._archive_key_provider,
                expected_kind="BACKUP",
                repository_roots=self._store.repository_roots,
                known_network_roots=self._store.known_network_roots,
            )
            result = {**verified.manifest, "verification_status": "VALID"}
        elif command_name == "RestoreBackup":
            result = restore_backup(
                self._store, payload["archive_path"], self._archive_key_provider
            )
        elif command_name == "DeleteBackup":
            result = delete_backup(
                self._store,
                self._archive_key_provider,
                payload["archive_id"],
                self._backup_directory,
            )
        elif command_name == "ExportFinanceData":
            result = export_finance_data(
                self._store,
                self._archive_key_provider,
                payload.get("destination_directory", self._export_directory),
            )
        elif command_name == "ImportFinanceArchive":
            result = import_finance_archive(
                self._store, payload["archive_path"], self._archive_key_provider
            )
        elif command_name == "RotateEncryptionKey":
            result = rotate_encryption_key(
                self._store, self._archive_key_provider, self._backup_directory
            )
        elif command_name == "RepairLocalStore":
            result = repair_local_store(self._store)
        elif command_name == "RebuildReadModels":
            with self._store.transaction() as connection:
                connection.execute("DELETE FROM projection_document")
                connection.execute("DELETE FROM projection_registry")
            result = {
                "status": "REBUILDING",
                "strategy": "LAZY_DETERMINISTIC_REPLAY",
                "event_store_sequence": self._store.latest_sequence(),
            }
        elif command_name == "ValidateStoreIntegrity":
            result = validate_store_integrity(self._store)
        elif command_name == "RegisterLocalAIRuntime":
            result = self._local_model_service.register_runtime(payload["runtime_path"])
        elif command_name == "RegisterLocalModel":
            result = self._local_model_service.register(
                payload["model_path"], payload["manifest_path"]
            )
        elif command_name == "EnableLocalModel":
            result = self._local_model_service.enable()
        elif command_name == "DisableLocalModel":
            result = self._local_model_service.disable()
        elif command_name == "EnableCategoryShadowMode":
            result = enable_category_shadow_mode(self._store)
        elif command_name == "DisableCategoryShadowMode":
            result = disable_category_shadow_mode(self._store)
        elif command_name == "StartCategoryEvaluationRun":
            result = start_category_evaluation_run(
                self._store,
                self._local_model_service,
                max_batch_size=int(payload.get("max_batch_size", 500)),
            )
        elif command_name == "PauseCategoryEvaluationRun":
            result = pause_category_evaluation_run(self._store, payload["run_id"])
        elif command_name == "ResumeCategoryEvaluationRun":
            result = resume_category_evaluation_run(
                self._store, self._local_model_service, payload["run_id"]
            )
        elif command_name == "CancelCategoryEvaluationRun":
            result = cancel_category_evaluation_run(self._store, payload["run_id"])
        elif command_name == "ConfirmCategorySuggestion":
            result = confirm_category_suggestion(self._store, payload["suggestion_id"])
        elif command_name == "CorrectCategorySuggestion":
            result = correct_category_suggestion(
                self._store, payload["suggestion_id"], payload["selected_category"]
            )
        elif command_name == "RejectCategorySuggestion":
            result = reject_category_suggestion(self._store, payload["suggestion_id"])
        elif command_name == "CreateClassificationRuleFromSuggestion":
            result = create_classification_rule_from_suggestion(
                self._store,
                payload["suggestion_id"],
                field=str(payload.get("field", "counterparty")),
            )
        elif command_name == "ExportDiagnostics":
            if self._diagnostic_recorder is None:
                raise ApplicationContractError("FINANCE_DIAGNOSTICS_DISABLED")
            result = self._diagnostic_recorder.export(
                payload["destination_path"], confirmed=payload["confirmed"] is True
            )
        else:
            raise ApplicationContractError("FINANCE_COMMAND_UNSUPPORTED")
        return {
            "schema_version": "1.0.0",
            "status": "COMPLETED",
            "result": result,
            "event_store_sequence": self._sequence(),
        }

    def command(
        self,
        command_name: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        correlation_id = request_id or "req_unavailable"
        started = monotonic()
        safe_name = command_name if command_name in COMMAND_REQUIRED_FIELDS or command_name in {
            "ClassifyTransactions",
            "Reconcile",
            "DetectDuplicates",
            "DetectTransfers",
            "DetectRefunds",
            "CreateBackup",
            "ExportFinanceData",
            "RotateEncryptionKey",
            "RepairLocalStore",
            "ValidateStoreIntegrity",
            "DetectInvestmentFundingRelations",
            "EnableLocalModel",
            "DisableLocalModel",
            "EnableCategoryShadowMode",
            "DisableCategoryShadowMode",
            "StartCategoryEvaluationRun",
        } else "UnsupportedCommand"
        try:
            result = self._execute_command(command_name, payload)
        except Exception as exc:
            if self._diagnostic_recorder:
                raw_code = str(exc).split(":", 1)[0]
                if command_name in {"RegisterLocalAIRuntime", "RegisterLocalModel"}:
                    code = (
                        raw_code
                        if raw_code.startswith(("LOCAL_MODEL_", "LOCAL_AI_"))
                        else "DESKTOP_OPERATION_FAILED"
                    )
                    self._diagnostic_recorder.record_local_model_operation(
                        request_id=correlation_id,
                        operation=command_name,
                        error_code=code,
                    )
                else:
                    code = raw_code if raw_code.startswith("FINANCE_") else "FINANCE_COMMAND_FAILED"
                    self._diagnostic_recorder.record(
                        operation_kind="COMMAND",
                        operation_name=safe_name,
                        duration_ms=round((monotonic() - started) * 1000),
                        error_code=code,
                        component_status="FAILED",
                    )
            raise
        if self._diagnostic_recorder:
            if command_name in {"RegisterLocalAIRuntime", "RegisterLocalModel"}:
                registration = result.get("result", {})
                model_id = registration.get("model_id") if isinstance(registration, dict) else None
                runtime_id = registration.get("runtime_id") if isinstance(registration, dict) else None
                self._diagnostic_recorder.record_local_model_operation(
                    request_id=correlation_id,
                    operation=command_name,
                    model_id=model_id if isinstance(model_id, str) else None,
                    runtime_id=runtime_id if isinstance(runtime_id, str) else "llama.cpp",
                )
            else:
                self._diagnostic_recorder.record(
                    operation_kind="COMMAND",
                    operation_name=safe_name,
                    duration_ms=round((monotonic() - started) * 1000),
                    event_count=result["event_store_sequence"],
                    component_status="PASSED",
                )
        return result


def _require_month(payload: dict[str, Any]) -> str:
    month = payload.get("month")
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise ApplicationContractError("FINANCE_MONTH_REQUIRED")
    return month


def _require_identifier(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ApplicationContractError(f"FINANCE_{name.upper()}_REQUIRED")
    return value
