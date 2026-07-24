"""Local application API for desktop IPC and projection-only UI access."""

from __future__ import annotations

from decimal import Decimal
import json
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
from .classification import (
    active_classifications,
    classification_review,
    classify_transactions,
    confirm_classification,
    create_rule,
    reject_classification,
)
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
from .importer import import_csv, normalize_batch
from .multi_account_import import (
    analyze_import_file,
    break_investment_funding_relation,
    confirm_investment_funding_relation,
    confirm_empty_opening_security_positions,
    detect_investment_funding_relations,
    get_import_analysis,
    get_bank_monthly_export,
    get_import_section_preview,
    import_mapped_sections,
    imported_period_reconciliations,
    imported_security_position_reconciliations,
    initial_balance_requirements,
    investment_funding_relations,
    list_import_sections,
    map_import_sections,
    reconcile_imported_period_balance,
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
    reconciled_monthly_cashflow,
    reconciled_transactions,
    reject_duplicate,
    reject_refund,
    reject_transfer,
    relation_review,
)
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
from .release_security import ReleaseIntegrityError, verify_integrity_manifest
from .schema_validation import SCHEMA_ROOT
from .store import LocalFinanceStore, StoreInvariantError
from .storage_policy import validate_runtime_path
from .workspace_lock import inspect_workspace_lock


class ApplicationContractError(ValueError):
    pass


COMMAND_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ImportTransactions": ("source_file_path", "account_id"),
    "AnalyzeImportFile": ("source_file_path", "requested_profile"),
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
    "ConfirmInvestmentFundingRelation": ("relation_id",),
    "RejectInvestmentFundingRelation": ("relation_id",),
    "BreakInvestmentFundingRelation": ("relation_id",),
    "ConfirmClassification": ("transaction_id", "category_code"),
    "RejectClassification": ("transaction_id",),
    "CreateClassificationRule": ("field", "operator", "value", "category_code", "priority"),
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
    ) -> None:
        self._store = store
        self._network_egress_disabled = network_egress_disabled
        self._archive_key_provider = archive_key_provider or KeychainKeyProvider(
            service="agent-os.finance.backup", username="archive"
        )
        self._backup_directory = backup_directory
        self._export_directory = export_directory
        self._diagnostic_recorder = diagnostic_recorder

    def _sequence(self) -> int:
        events = self._store.events()
        return events[-1]["sequence_number"] if events else 0

    def _envelope(self, data: Any, *, status: str = "READY") -> dict[str, Any]:
        sequence = self._sequence()
        return {
            "schema_version": "1.0.0",
            "state": status,
            "projection_sequence": sequence,
            "event_store_sequence": sequence,
            "data": data,
        }

    def query(self, query_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        queries: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "GetCapabilityManifest": self._capabilities,
            "GetStartupStatus": self._startup_status,
            "GetRuntimeSecurityStatus": self._security_status,
            "GetDashboard": self._dashboard,
            "ListTransactions": self._transactions,
            "GetTransactionDetails": self._transaction_details,
            "ListAccounts": self._accounts,
            "GetAccount": self._account,
            "GetAccountBalanceHistory": self._account_balance_history,
            "GetBalanceReconciliation": self._balance_reconciliation,
            "GetLiquidityOverview": self._liquidity,
            "GetNetWorthOverview": self._net_worth,
            "GetNetWorthHistory": self._net_worth_history,
            "GetLiabilityOverview": self._liabilities,
            "GetAssetAllocation": self._asset_allocation,
            "GetProjectedMonthEndBalance": self._projected_balance,
            "ListAccountReviews": self._account_reviews,
            "GetCategoryBreakdown": self._category_breakdown,
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
            "GetBankMonthlyExport": self._bank_monthly_export,
            "ListImportSections": self._import_sections,
            "ListImportSectionBindings": self._import_section_bindings,
            "GetImportSectionPreview": self._import_section_preview,
            "GetInitialBalanceRequirements": self._initial_balance_requirements,
            "GetImportedPeriodReconciliation": self._imported_period_reconciliation,
            "GetImportedSecurityPositionReconciliation": self._imported_security_position_reconciliation,
            "ListInvestmentFundingRelations": self._investment_funding_relations,
            "GetSecurityTransaction": self._security_transaction,
            "ListSecurityPositions": self._security_positions,
            "ListBackups": self._backups,
            "GetStoreIntegrity": self._store_integrity,
            "GetKeyStatus": self._key_status,
            "GetMigrationStatus": self._migration_status,
        }
        if query_name not in queries:
            raise ApplicationContractError("FINANCE_QUERY_UNSUPPORTED")
        return queries[query_name](payload)

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
        return self._envelope(
            {
                "extension_version": __version__,
                "schema_version": "1.0.0",
                "capabilities": {
                    "imports": True,
                    "multi_account_import": True,
                    "security_transactions": True,
                    "classification": True,
                    "reconciliation": True,
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
                    "tax": False,
                    "receipts": False,
                    "cloud_sync": False,
                    "external_models": False,
                },
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
        month = _require_month(payload)
        cashflow = reconciled_monthly_cashflow(self._store, month)
        base = monthly_forecast(self._store, month).get("BASE")
        liquidity = liquidity_overview(self._store)
        worth = net_worth_overview(self._store)
        projected = projected_month_end_balance(self._store, month)
        reviews = (
            len(classification_review(self._store))
            + sum(
                len(relation_review(self._store, kind))
                for kind in ("duplicates", "transfers", "refunds")
            )
            + sum(
                1
                for item in recurring_patterns(self._store).values()
                if item["status"] == "PROPOSED"
            )
            + sum(
                1
                for item in expected_transactions(self._store).values()
                if item["status"] == "MISSED"
            )
            + len(account_reviews(self._store))
        )
        income = cashflow["effective_income"]
        savings_rate = (
            (cashflow["net_cashflow"] / income * 100).quantize(Decimal("0.01"))
            if income > 0
            else None
        )
        return self._envelope(
            {
                "month": month,
                "effective_income": str(income),
                "effective_expenses": str(cashflow["effective_expenses"]),
                "net_cashflow": str(cashflow["net_cashflow"]),
                "expected_month_end_surplus": base["predicted_surplus"] if base else None,
                "remaining_expected_income": base["expected_income"] if base else "0",
                "remaining_expected_expenses": base["expected_fixed_expenses"] if base else "0",
                "savings_rate": str(savings_rate) if savings_rate is not None else None,
                "open_reviews": reviews,
                "liquid_balance": liquidity["liquid_funds"],
                "liquid_balance_as_of": liquidity["as_of"],
                "projected_month_end_balance": projected["projected_month_end_balance"],
                "net_worth": worth["net_worth"],
                "net_worth_as_of": worth["as_of"],
            },
            status="EMPTY" if self._sequence() == 0 else "READY",
        )

    def _transactions(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = payload.get("month")
        reconciled = reconciled_transactions(self._store)
        classifications = active_classifications(self._store)
        rows = []
        for event in self._store.events("TransactionNormalized"):
            item = event["payload"]
            if month and not item["booking_date"].startswith(month):
                continue
            classification = classifications.get(item["transaction_id"])
            rows.append(
                {
                    **item,
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
            )
        return self._envelope({"transactions": rows}, status="EMPTY" if not rows else "READY")

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

    def _category_breakdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = _require_month(payload)
        result = reconciled_category_breakdown(self._store, month)
        for item in result["categories"].values():
            for key in ("gross_expense", "refund_amount", "effective_expense"):
                item[key] = str(item[key])
        return self._envelope(result, status="EMPTY" if not result["categories"] else "READY")

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

    def _investment_funding_relations(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = payload.get("status")
        rows = [
            item
            for item in investment_funding_relations(self._store).values()
            if not status or item["status"] == status
        ]
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
            result = analyze_import_file(
                self._store,
                payload["source_file_path"],
                payload["requested_profile"],
                payload.get("confirmed_bank_identifier"),
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
        elif command_name == "ConfirmClassification":
            result = confirm_classification(
                self._store, payload["transaction_id"], payload["category_code"]
            )
        elif command_name == "RejectClassification":
            result = reject_classification(self._store, payload["transaction_id"])
        elif command_name == "CreateClassificationRule":
            result = create_rule(self._store, **payload)
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
        elif command_name == "ValidateStoreIntegrity":
            result = validate_store_integrity(self._store)
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

    def command(self, command_name: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        } else "UnsupportedCommand"
        try:
            result = self._execute_command(command_name, payload)
        except Exception as exc:
            if self._diagnostic_recorder:
                raw_code = str(exc).split(":", 1)[0]
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
