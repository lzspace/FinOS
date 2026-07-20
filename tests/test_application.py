from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator, FormatChecker

from finance_extension.accounts import (
    create_account,
    record_balance_snapshot,
    reconcile_account_balance,
)
from finance_extension.application import ApplicationContractError, FinanceApplicationService
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
        source = root / "synthetic.csv"
        source.write_text(CSV)
        self.store = LocalFinanceStore(
            root / "data", StaticKeyProvider(Fernet.generate_key())
        ).open()
        batch = import_csv(self.store, source, "acc_01")
        normalize_batch(self.store, batch)
        self.application = FinanceApplicationService(self.store)

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
        for hidden in ("tax", "receipts", "cloud_sync", "external_models"):
            self.assertFalse(capabilities[hidden])
        self._validate(response, "capability_manifest.response.schema.json")

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

    def test_security_states_and_sequence_are_dynamic(self) -> None:
        response = self.application.query("GetRuntimeSecurityStatus")
        self.assertEqual(response["data"]["checks"]["snapshot_integrity"], "PASSED")
        self.assertEqual(response["data"]["checks"]["keychain_available"], "NOT_CHECKED")
        self.assertEqual(response["data"]["last_event_sequence"], len(self.store.events()))
        self._validate(response, "runtime_security_status.response.schema.json")

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


if __name__ == "__main__":
    unittest.main()
