"""Local application API for desktop IPC and projection-only UI access."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from . import __version__
from .classification import (
    active_classifications,
    classification_review,
    classify_transactions,
    confirm_classification,
    create_rule,
    reject_classification,
)
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
from .store import LocalFinanceStore, StoreInvariantError
from .storage_policy import validate_runtime_path


class ApplicationContractError(ValueError):
    pass


COMMAND_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ImportTransactions": ("source_file_path", "account_id"),
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
}


class FinanceApplicationService:
    """Narrow local interface intended for a desktop IPC adapter.

    UI clients can execute named queries and commands. They cannot append events
    or access encryption keys, SQLite, snapshots, or imported source files.
    """

    def __init__(
        self, store: LocalFinanceStore, *, network_egress_disabled: bool | None = None
    ) -> None:
        self._store = store
        self._network_egress_disabled = network_egress_disabled

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
            "GetRuntimeSecurityStatus": self._security_status,
            "GetDashboard": self._dashboard,
            "ListTransactions": self._transactions,
            "GetTransactionDetails": self._transaction_details,
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
        }
        if query_name not in queries:
            raise ApplicationContractError("FINANCE_QUERY_UNSUPPORTED")
        return queries[query_name](payload)

    def _capabilities(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._envelope(
            {
                "extension_version": __version__,
                "schema_version": "1.0.0",
                "capabilities": {
                    "imports": True,
                    "classification": True,
                    "reconciliation": True,
                    "recurring_patterns": True,
                    "forecasting": True,
                    "forecast_evaluation": True,
                    "wealth": False,
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
            or transaction_id in {
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

    def command(self, command_name: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        else:
            raise ApplicationContractError("FINANCE_COMMAND_UNSUPPORTED")
        return {
            "schema_version": "1.0.0",
            "status": "COMPLETED",
            "result": result,
            "event_store_sequence": self._sequence(),
        }


def _require_month(payload: dict[str, Any]) -> str:
    month = payload.get("month")
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise ApplicationContractError("FINANCE_MONTH_REQUIRED")
    return month
