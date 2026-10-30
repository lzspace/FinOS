from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator, FormatChecker

from finance_extension.accounts import (
    close_account,
    create_account,
    record_balance_snapshot,
    reconcile_account_balance,
)
from finance_extension.application import ApplicationContractError, FinanceApplicationService
from finance_extension.classification import confirm_classification
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.schema_validation import SCHEMA_ROOT
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,3200.00,EUR,Arbeitgeber Beispiel,Gehalt
2026-07-03,2026-07-03,-42.80,EUR,Markt Beispiel,Lebensmittel
"""


class ApplicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        source = root / "synthetic.csv"
        source.write_text(CSV)
        self.store = LocalFinanceStore(
            root / "data", StaticKeyProvider(Fernet.generate_key())
        ).open()
        batch = import_csv(self.store, source, "acc_01")
        normalize_batch(self.store, batch)
        self.archive_key = StaticKeyProvider(Fernet.generate_key())
        self.application = FinanceApplicationService(
            self.store,
            archive_key_provider=self.archive_key,
            backup_directory=root / "backups",
            export_directory=root / "exports",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _validate(self, response: dict[str, object], schema_name: str) -> None:
        schema = json.loads((SCHEMA_ROOT / schema_name).read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(response)

    def test_capabilities_hide_out_of_scope_features_and_validate(self) -> None:
        response = self.application.query("GetCapabilityManifest")
        capabilities = response["data"]["capabilities"]
        self.assertTrue(capabilities["forecasting"])
        self.assertTrue(capabilities["wealth"])
        self.assertTrue(capabilities["accounts"])
        self.assertFalse(capabilities["local_ai"])
        self.assertFalse(response["data"]["local_ai"]["automatic_classification"])
        self.assertEqual(response["data"]["local_ai"]["network_egress"], "DENY")
        for hidden in ("tax", "receipts", "cloud_sync", "external_models"):
            self.assertFalse(capabilities[hidden])
        self._validate(response, "capability_manifest.response.schema.json")

    def test_local_model_status_is_non_blocking_and_offline(self) -> None:
        response = self.application.query("GetLocalModelStatus")

        self.assertEqual(response["data"]["status"], "DISABLED")
        self.assertFalse(response["data"]["available"])
        self.assertEqual(response["data"]["runtime"], "llama.cpp")
        self.assertEqual(response["data"]["processing_location"], "LOCAL_DEVICE")

    def test_runtime_registration_returns_only_opaque_metadata(self) -> None:
        runtime = self.root / "llama-cli"
        runtime.write_text(
            "#!/bin/sh\necho 'llama.cpp b11794-f84909536'\n",
            encoding="utf-8",
        )
        runtime.chmod(0o700)

        response = self.application.command(
            "RegisterLocalAIRuntime",
            {"runtime_path": str(runtime)},
            request_id="req_runtime_test",
        )
        encoded = json.dumps(response)

        self.assertEqual(response["result"]["provider"], "LLAMA_CPP")
        self.assertEqual(response["result"]["integrity_status"], "VALID")
        self.assertNotIn(str(runtime), encoded)
        self.assertNotIn("runtime_path", encoded)

    def test_projection_queries_do_not_append_events(self) -> None:
        before = len(self.store.events())
        dashboard = self.application.query("GetDashboard", {"month": "2026-07"})
        transactions = self.application.query("ListTransactions", {"month": "2026-07"})
        transaction_id = transactions["data"]["transactions"][0]["transaction_id"]
        details = self.application.query(
            "GetTransactionDetails", {"transaction_id": transaction_id}
        )
        self.assertEqual(len(self.store.events()), before)
        self.assertEqual(details["data"]["transaction_id"], transaction_id)
        self.assertEqual(details["data"]["category_code"], "UNCLASSIFIED")
        self._validate(dashboard, "dashboard.response.schema.json")
        self._validate(transactions, "transaction_list.response.schema.json")
        self._validate(details, "transaction_detail.response.schema.json")

    def test_monthly_workspace_is_derived_and_marks_following_months_missing(self) -> None:
        create_account(
            self.store,
            account_id="acc_01",
            display_name="Historisches Girokonto",
            account_type="CHECKING",
            institution="Testbank",
            currency="EUR",
            opened_at="2026-08-01",
        )
        before = len(self.store.events())
        response = self.application.query(
            "GetMonthlyWorkspaceStatus", {"as_of": "2026-09-30"}
        )
        self.assertEqual(len(self.store.events()), before)
        self._validate(response, "monthly_workspace_status.response.schema.json")

        data = response["data"]
        rows = {item["month"]: item for item in data["months"]}
        self.assertEqual(data["earliest_month"], "2026-07")
        self.assertEqual(rows["2026-07"]["import_status"], "LEGACY_DATA")
        self.assertEqual(rows["2026-07"]["status"], "REVIEW")
        self.assertEqual(rows["2026-07"]["expected_account_count"], 1)
        self.assertGreater(rows["2026-07"]["review_counts"]["CATEGORY"], 0)
        self.assertEqual(rows["2026-08"]["status"], "MISSING")
        self.assertEqual(
            rows["2026-08"]["primary_action"],
            {"page": "imports", "view": "month", "entity_id": "2026-08"},
        )
        self.assertEqual(rows["2026-09"]["expected_account_count"], 1)

        rebuilt = FinanceApplicationService(
            self.store,
            archive_key_provider=self.archive_key,
            backup_directory=self.root / "backups",
            export_directory=self.root / "exports",
        ).query("GetMonthlyWorkspaceStatus", {"as_of": "2026-09-30"})
        self.assertEqual(response["data"], rebuilt["data"])

    def test_latest_sequence_and_request_read_context_do_not_repeat_decoding(self) -> None:
        expected = self.store.events()[-1]["sequence_number"]
        self.assertEqual(self.store.latest_sequence(), expected)

        with self.store.read_context() as context:
            first = self.store.events_by_type("TransactionNormalized")
            decoded_after_first = context.events_decoded
            second = self.store.events_by_type("TransactionNormalized")
            self.assertEqual(first, second)
            self.assertEqual(context.events_decoded, decoded_after_first)
            self.assertGreater(context.request_cache_hits, 0)

    def test_transaction_cursor_pages_are_stable_and_reject_stale_state(self) -> None:
        first = self.application.query(
            "ListTransactions", {"month": "2026-07", "limit": 1}
        )
        self.assertEqual(len(first["data"]["items"]), 1)
        self.assertTrue(first["data"]["has_more"])
        cursor = first["data"]["next_cursor"]
        second = self.application.query(
            "ListTransactions",
            {"month": "2026-07", "limit": 1, "cursor": cursor},
        )
        self.assertNotEqual(
            first["data"]["items"][0]["transaction_id"],
            second["data"]["items"][0]["transaction_id"],
        )

        transaction_id = first["data"]["items"][0]["transaction_id"]
        confirm_classification(self.store, transaction_id, "OTHER_EXPENSE")
        with self.assertRaisesRegex(ApplicationContractError, "QUERY_CURSOR_STALE"):
            self.application.query(
                "ListTransactions",
                {"month": "2026-07", "limit": 1, "cursor": cursor},
            )

    def test_query_limit_is_bounded(self) -> None:
        with self.assertRaisesRegex(ApplicationContractError, "QUERY_LIMIT_INVALID"):
            self.application.query("ListTransactions", {"month": "2026-07", "limit": 0})

    def test_transaction_query_uses_shared_period_selection(self) -> None:
        response = self.application.query(
            "ListTransactions",
            {"mode": "CUSTOM_RANGE", "start_date": "2026-07-02", "end_date": "2026-07-31"},
        )
        rows = response["data"]["transactions"]
        self.assertTrue(rows)
        self.assertTrue(all("2026-07-02" <= row["booking_date"] <= "2026-07-31" for row in rows))
        self.assertTrue(all("account_id" in row and "export_id" in row for row in rows))
        self._validate(response, "transaction_list.response.schema.json")

    def test_dashboard_and_time_series_share_period_and_cashflow_totals(self) -> None:
        selection = {
            "mode": "CUSTOM_RANGE",
            "start_date": "2026-07-01",
            "end_date": "2026-07-05",
        }
        dashboard = self.application.query("GetDashboard", selection)
        series = self.application.query("GetFinancialTimeSeries", selection)

        self.assertEqual(len(series["data"]["points"]), 5)
        self.assertIsNone(series["data"]["points"][1]["income"])
        self.assertFalse(series["data"]["points"][1]["has_data"])
        self.assertEqual(
            dashboard["data"]["effective_income"],
            series["data"]["summary"]["effective_income"],
        )
        self.assertEqual(
            dashboard["data"]["effective_expenses"],
            series["data"]["summary"]["effective_expenses"],
        )
        self.assertEqual(
            dashboard["data"]["net_cashflow"],
            series["data"]["summary"]["net_cashflow"],
        )
        self.assertIsNone(dashboard["data"]["liquid_balance"])
        self.assertIsNone(dashboard["data"]["net_worth"])
        self._validate(dashboard, "dashboard.response.schema.json")
        self._validate(series, "financial_time_series.response.schema.json")

    def test_dashboard_marks_stale_balance_sources(self) -> None:
        create_account(
            self.store,
            account_id="acc_stale",
            display_name="Altes Girokonto",
            account_type="CHECKING",
            institution="Lokale Bank",
            currency="EUR",
            opened_at="2025-01-01",
        )
        record_balance_snapshot(
            self.store,
            account_id="acc_stale",
            balance_date="2025-01-31",
            booked_balance="100.00",
            available_balance="100.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        response = self.application.query(
            "GetDashboard", {"mode": "MONTH", "year": 2026, "month": 7}
        )
        self.assertEqual(response["state"], "STALE")
        self.assertEqual(response["data"]["liquid_balance"], "100.00")

    def test_review_inbox_is_derived_and_rebuilds_deterministically(self) -> None:
        before = len(self.store.events())
        first = self.application.query("GetReviewInbox")
        self.assertEqual(len(self.store.events()), before)
        self.assertEqual(first["data"]["total_open"], len(first["data"]["items"]))
        self.assertEqual(
            first["data"]["total_open"],
            sum(first["data"]["items_by_type"].values()),
        )
        self.assertGreater(first["data"]["items_by_type"]["CATEGORY"], 0)
        self._validate(first, "review_inbox.response.schema.json")

        transaction_id = next(
            item["entity_id"]
            for item in first["data"]["items"]
            if item["review_type"] == "CATEGORY"
        )
        category_before = first["data"]["items_by_type"]["CATEGORY"]
        confirm_classification(self.store, transaction_id, "OTHER_EXPENSE")
        resolved = self.application.query("GetReviewInbox")["data"]
        self.assertEqual(resolved["items_by_type"]["CATEGORY"], category_before - 1)

        expected = resolved
        key_provider = self.store.key_provider
        self.store.close()
        self.store = LocalFinanceStore(
            self.root / "data", key_provider
        ).open()
        self.application = FinanceApplicationService(
            self.store,
            archive_key_provider=self.archive_key,
            backup_directory=self.root / "backups",
            export_directory=self.root / "exports",
        )
        self.assertEqual(self.application.query("GetReviewInbox")["data"], expected)

    def test_security_states_and_sequence_are_dynamic(self) -> None:
        response = self.application.query("GetRuntimeSecurityStatus")
        self.assertEqual(response["data"]["checks"]["snapshot_integrity"], "PASSED")
        self.assertEqual(response["data"]["checks"]["keychain_available"], "NOT_CHECKED")
        self.assertEqual(response["data"]["last_event_sequence"], len(self.store.events()))
        self._validate(response, "runtime_security_status.response.schema.json")

    def test_startup_integrity_is_ready_and_versioned(self) -> None:
        response = self.application.query("GetStartupStatus")
        self.assertEqual(response["data"]["status"], "READY")
        self.assertEqual(response["data"]["checks"]["bundle_integrity"], "VALID")
        self._validate(response, "startup_status.response.schema.json")

    def test_unsupported_contracts_and_missing_identifiers_fail_closed(self) -> None:
        with self.assertRaisesRegex(ApplicationContractError, "FINANCE_QUERY_UNSUPPORTED"):
            self.application.query("ReadEventStore")
        with self.assertRaisesRegex(ApplicationContractError, "FINANCE_TRANSACTION_ID_REQUIRED"):
            self.application.query("GetTransactionDetails")
        with self.assertRaisesRegex(ApplicationContractError, "FINANCE_COMMAND_UNSUPPORTED"):
            self.application.command("AppendEvent", {})

    def test_existing_command_is_dispatched_through_the_service(self) -> None:
        response = self.application.command("ClassifyTransactions", {"month": "2026-07"})
        self.assertEqual(response["status"], "COMPLETED")
        self.assertGreater(response["event_store_sequence"], 0)

    def test_classification_memory_is_available_as_a_projection(self) -> None:
        transactions = self.application.query("ListTransactions", {"month": "2026-07"})
        transaction_id = transactions["data"]["transactions"][0]["transaction_id"]
        self.application.command(
            "ConfirmClassification",
            {"transaction_id": transaction_id, "category_code": "FOOD_GROCERIES"},
        )

        response = self.application.query("GetClassificationMemory")

        self.assertEqual(response["state"], "READY")
        self.assertEqual(response["data"]["active_entry_count"], 1)
        self.assertEqual(response["data"]["policy_version"], "classification-memory@1.0.0")

    def test_custom_categories_are_persisted_and_listed(self) -> None:
        transactions = self.application.query("ListTransactions", {"month": "2026-07"})
        transaction_id = transactions["data"]["transactions"][0]["transaction_id"]

        response = self.application.command(
            "ConfirmClassification",
            {
                "transaction_id": transaction_id,
                "category_code": "CUSTOM_HAUSTIERE",
            },
        )
        categories = self.application.query("ListCategories")
        details = self.application.query(
            "GetTransactionDetails", {"transaction_id": transaction_id}
        )

        self.assertEqual(response["status"], "COMPLETED")
        custom = next(
            item for item in categories["data"]["categories"]
            if item["category_code"] == "CUSTOM_HAUSTIERE"
        )
        self.assertEqual(custom["display_name"], "Haustiere")
        self.assertTrue(custom["legacy"])
        self.assertEqual(details["data"]["category_code"], "CUSTOM_HAUSTIERE")
        self.application.command(
            "RenameCategory",
            {"category_code": "CUSTOM_HAUSTIERE", "display_name": "Tierbedarf"},
        )
        renamed = next(
            item for item in self.application.query("ListCategories")["data"]["categories"]
            if item["category_code"] == "CUSTOM_HAUSTIERE"
        )
        self.assertEqual(renamed["display_name"], "Tierbedarf")

    def test_category_commands_create_rename_archive_and_restore(self) -> None:
        created = self.application.command(
            "CreateCategory", {"display_name": "Haustiere", "category_type": "EXPENSE"}
        )["result"]
        self.application.command(
            "RenameCategory", {"category_code": created["category_code"], "display_name": "Tierbedarf"}
        )
        self.application.command("ArchiveCategory", {"category_code": created["category_code"]})
        archived = next(
            item for item in self.application.query("ListCategories")["data"]["categories"]
            if item["category_code"] == created["category_code"]
        )
        self.assertEqual(archived["display_name"], "Tierbedarf")
        self.assertEqual(archived["status"], "ARCHIVED")
        self.application.command("RestoreCategory", {"category_code": created["category_code"]})
        self._validate(self.application.query("ListCategories"), "category_list.response.schema.json")

    def test_account_and_net_worth_queries_use_versioned_projection_contracts(self) -> None:
        create_account(
            self.store,
            account_id="acc_main",
            display_name="Girokonto",
            account_type="CHECKING",
            institution="Lokale Bank",
            currency="EUR",
            opened_at="2026-01-01",
        )
        record_balance_snapshot(
            self.store,
            account_id="acc_main",
            balance_date="2026-07-20",
            booked_balance="1200.00",
            available_balance="1150.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        reconcile_account_balance(self.store, "acc_main")
        accounts = self.application.query("ListAccounts", {"as_of": "2026-07-20"})
        account = self.application.query(
            "GetAccount", {"account_id": "acc_main", "as_of": "2026-07-20"}
        )
        history = self.application.query(
            "GetAccountBalanceHistory", {"account_id": "acc_main"}
        )
        reconciliation = self.application.query(
            "GetBalanceReconciliation", {"account_id": "acc_main"}
        )
        liquidity = self.application.query("GetLiquidityOverview", {"as_of": "2026-07-20"})
        worth = self.application.query("GetNetWorthOverview", {"as_of": "2026-07-20"})
        worth_history = self.application.query("GetNetWorthHistory")
        liabilities = self.application.query("GetLiabilityOverview")
        allocation = self.application.query("GetAssetAllocation")
        projected = self.application.query("GetProjectedMonthEndBalance", {"month": "2026-07"})
        self.assertNotIn("account_reference", accounts["data"]["accounts"][0])
        self.assertEqual(liquidity["data"]["liquid_funds"], "1150.00")
        self.assertEqual(worth["data"]["net_worth"], "1200.00")
        self._validate(accounts, "account_list.response.schema.json")
        self._validate(account, "account_detail.response.schema.json")
        self._validate(history, "account_balance_history.response.schema.json")
        self._validate(reconciliation, "balance_reconciliation.response.schema.json")
        self._validate(liquidity, "liquidity_overview.response.schema.json")
        self._validate(worth, "net_worth_overview.response.schema.json")
        self._validate(worth_history, "net_worth_history.response.schema.json")
        self._validate(liabilities, "liability_overview.response.schema.json")
        self._validate(allocation, "asset_allocation.response.schema.json")
        self._validate(projected, "projected_month_end_balance.response.schema.json")

    def test_account_workspace_queries_use_period_control_and_versioned_contracts(self) -> None:
        create_account(
            self.store,
            account_id="acc_main",
            display_name="Girokonto",
            account_type="CHECKING",
            institution="Lokale Bank",
            currency="EUR",
            opened_at="2026-01-01",
        )
        record_balance_snapshot(
            self.store,
            account_id="acc_main",
            balance_date="2026-06-30",
            booked_balance="1200.00",
            available_balance="1150.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        record_balance_snapshot(
            self.store,
            account_id="acc_main",
            balance_date="2026-07-20",
            booked_balance="1200.00",
            available_balance="1150.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        reconcile_account_balance(self.store, "acc_main")
        period = {"mode": "MONTH", "year": 2026, "month": 7}

        available = self.application.query("GetAvailablePeriods")
        overviews = self.application.query("ListAccountOverviews", {"period": period})
        detail = self.application.query(
            "GetAccountDetail", {"account_id": "acc_main", "period": period}
        )
        summary = self.application.query(
            "GetAccountPeriodSummary", {"account_id": "acc_main", "period": period}
        )
        transactions = self.application.query(
            "ListAccountTransactions", {"account_id": "acc_main", "period": period}
        )
        reconciliations = self.application.query(
            "ListAccountReconciliations", {"account_id": "acc_main"}
        )
        imports = self.application.query("ListAccountImports", {"account_id": "acc_main"})
        positions = self.application.query("ListAccountPositions", {"account_id": "acc_main"})
        audit_trail = self.application.query(
            "GetAccountAuditTrail", {"account_id": "acc_main"}
        )

        self.assertEqual(overviews["data"]["accounts"][0]["overview_status"], "MATCHED")
        self.assertEqual(detail["data"]["account"]["account_id"], "acc_main")
        self.assertEqual(
            detail["data"]["open_review_count"],
            len(detail["data"]["open_reviews"]),
        )
        inbox_ids = {item["review_id"] for item in self.application.query("GetReviewInbox")["data"]["items"]}
        self.assertTrue(
            {item["review_id"] for item in detail["data"]["open_reviews"]}.issubset(inbox_ids)
        )
        self.assertEqual(positions["data"]["positions"], [])
        for response in (
            overviews,
            detail,
            summary,
            transactions,
            reconciliations,
            imports,
            positions,
            audit_trail,
            available,
        ):
            self.assertIn("projection_version", response)
            self.assertEqual(response["freshness_status"], "CURRENT")
            self.assertIn("generated_at", response)

        self._validate(available, "available_periods.response.schema.json")
        self._validate(overviews, "account_overview_list.response.schema.json")
        self._validate(detail, "account_detail.response.schema.json")
        self._validate(summary, "account_period_summary.response.schema.json")
        self._validate(transactions, "account_transaction_list.response.schema.json")
        self._validate(reconciliations, "account_reconciliation_list.response.schema.json")
        self._validate(imports, "account_import_list.response.schema.json")
        self._validate(positions, "account_position_list.response.schema.json")
        self._validate(audit_trail, "account_audit_trail.response.schema.json")

        close_account(self.store, "acc_main", "2026-07-31")
        active_only = self.application.query("ListAccountOverviews", {"period": period})
        including_closed = self.application.query(
            "ListAccountOverviews", {"period": period, "include_closed": True}
        )
        closed_detail = self.application.query(
            "GetAccountDetail", {"account_id": "acc_main", "period": period}
        )
        self.assertFalse(any(item["account_id"] == "acc_main" for item in active_only["data"]["accounts"]))
        self.assertEqual(including_closed["data"]["accounts"][0]["overview_status"], "CLOSED")
        self.assertEqual(closed_detail["data"]["account"]["status"], "CLOSED")

        with self.assertRaisesRegex(ApplicationContractError, "FINANCE_ACCOUNT_NOT_FOUND"):
            self.application.query("GetAccountDetail", {"account_id": "does_not_exist"})
        with self.assertRaisesRegex(Exception, "FINANCE_PERIOD_SELECTION_INVALID"):
            self.application.query(
                "ListAccountOverviews", {"period": {"mode": "MONTH", "year": 2026, "month": 13}}
            )
        with self.assertRaisesRegex(Exception, "FINANCE_PERIOD_RANGE_TOO_LARGE"):
            self.application.query(
                "ListAccountOverviews",
                {
                    "period": {
                        "mode": "CUSTOM_RANGE",
                        "start_date": "2000-01-01",
                        "end_date": "2026-01-01",
                    }
                },
            )

        manifest = self.application.query("GetCapabilityManifest")["data"]
        self.assertEqual(manifest["product_version"], "1.5.0")
        self.assertEqual(manifest["contract_version"], "1.5.0")
        self.assertEqual(manifest["ui_contract_version"], "1.5.0")
        self.assertEqual(manifest["store_schema_version"], 3)
        self.assertIn("account_overview", manifest["projection_versions"])
        self.assertTrue(manifest["capabilities"]["period_control"])
        self.assertTrue(manifest["capabilities"]["account_workspace"])

    def test_recovery_commands_and_status_queries_use_versioned_contracts(self) -> None:
        empty = self.application.query("ListBackups")
        created = self.application.command("CreateBackup", {})
        archive_path = created["result"]["path"]
        verified = self.application.command("VerifyBackup", {"archive_path": archive_path})
        backups = self.application.query("ListBackups")
        integrity = self.application.query("GetStoreIntegrity")
        keys = self.application.query("GetKeyStatus")
        migrations = self.application.query("GetMigrationStatus")

        self.assertEqual(empty["state"], "EMPTY")
        self.assertEqual(verified["result"]["verification_status"], "VALID")
        self.assertTrue(keys["data"]["archive_key"]["independent_from_store"])
        self._validate(backups, "backup_list.response.schema.json")
        self._validate(integrity, "store_integrity.response.schema.json")
        self._validate(keys, "key_status.response.schema.json")
        self._validate(migrations, "migration_status.response.schema.json")


if __name__ == "__main__":
    unittest.main()
