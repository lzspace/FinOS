from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.accounts import (
    account_overview,
    account_reviews,
    asset_allocation,
    balance_snapshots,
    close_account,
    correct_asset_snapshot,
    correct_balance_snapshot,
    create_account,
    create_asset_snapshot,
    create_liability_snapshot,
    liquidity_overview,
    net_worth_overview,
    projected_month_end_balance,
    reconcile_account_balance,
    record_balance_snapshot,
    update_account,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import ImportErrorSafe, import_csv, normalize_batch
from finance_extension.reconciliation import confirm_transfer, detect_transfers
from finance_extension.store import LocalFinanceStore, StoreInvariantError


HEADER = "booking_date,value_date,amount,currency,counterparty,description\n"


class AccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(self.data, self.key).open()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _account(self, account_id: str, account_type: str = "CHECKING", **changes: object) -> str:
        return create_account(
            self.store,
            account_id=account_id,
            display_name=str(changes.pop("display_name", account_id)),
            account_type=account_type,
            institution="Lokale Bank",
            currency=str(changes.pop("currency", "EUR")),
            opened_at="2026-01-01",
            **changes,
        )

    def _snapshot(
        self, account_id: str, point: str, amount: str, source: str = "MANUAL_ENTRY"
    ) -> str:
        return record_balance_snapshot(
            self.store,
            account_id=account_id,
            balance_date=point,
            booked_balance=amount,
            available_balance=amount,
            currency="EUR",
            source=source,
            confidence="HIGH",
        )

    def _import(self, filename: str, account_id: str, rows: list[str]) -> None:
        source = self.root / filename
        source.write_text(HEADER + "\n".join(rows) + "\n")
        batch = import_csv(self.store, source, account_id)
        normalize_batch(self.store, batch)

    def test_account_lifecycle_is_event_sourced_and_closed_account_rejects_import(self) -> None:
        self._account("acc_main", account_reference="DE001234567890")
        update_account(self.store, "acc_main", display_name="Hauptkonto")
        close_account(self.store, "acc_main", "2026-06-30")
        account = account_overview(self.store, "2026-07-20")[0]
        self.assertEqual(account["status"], "CLOSED")
        self.assertEqual(account["masked_reference"], "•••• 7890")
        self.assertNotIn("account_reference", account)
        source = self.root / "closed.csv"
        source.write_text(HEADER + "2026-07-01,2026-07-01,-10.00,EUR,Beispiel,Nach Schließung\n")
        with self.assertRaisesRegex(ImportErrorSafe, "FINANCE_IMPORT_ACCOUNT_CLOSED"):
            import_csv(self.store, source, "acc_main")

    def test_snapshot_correction_never_mutates_history_and_rebuilds(self) -> None:
        self._account("acc_main")
        original = self._snapshot("acc_main", "2026-07-01", "1000.00")
        replacement = correct_balance_snapshot(
            self.store,
            original,
            booked_balance="1010.00",
            available_balance="1005.00",
            reason="Bankbestätigung korrigiert",
        )
        self.assertNotIn(original, balance_snapshots(self.store))
        self.assertEqual(balance_snapshots(self.store)[replacement]["booked_balance"], "1010.00")
        self.assertEqual(len(self.store.events("BalanceSnapshotRecorded")), 1)
        self.assertEqual(len(self.store.events("BalanceSnapshotCorrected")), 1)
        before = balance_snapshots(self.store)
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        self.assertEqual(balance_snapshots(self.store), before)

    def test_reconciliation_detects_difference_without_correcting_balance(self) -> None:
        self._account("acc_main")
        self._snapshot("acc_main", "2026-07-01", "1000.00")
        self._import(
            "movement.csv",
            "acc_main",
            ["2026-07-05,2026-07-05,-100.00,EUR,Markt,Einkauf"],
        )
        target = self._snapshot("acc_main", "2026-07-10", "895.00", "IMPORT_SOURCE")
        self.assertEqual(reconcile_account_balance(self.store, "acc_main"), 1)
        event = self.store.events("AccountBalanceReconciled")[-1]
        self.assertEqual(event["payload"]["calculated_balance"], "900.00")
        self.assertEqual(event["payload"]["balance_difference"], "-5.00")
        self.assertEqual(event["payload"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(balance_snapshots(self.store)[target]["booked_balance"], "895.00")
        self.assertTrue(
            any(row["review_type"] == "BALANCE_DIFFERENCE" for row in account_reviews(self.store))
        )

    def test_missing_opening_balance_is_an_explicit_review(self) -> None:
        self._account("acc_main")
        self._snapshot("acc_main", "2026-07-10", "895.00")
        reconcile_account_balance(self.store, "acc_main")
        event = self.store.events("AccountBalanceReconciled")[-1]
        self.assertEqual(event["payload"]["status"], "OPENING_BALANCE_MISSING")
        self.assertIsNone(event["payload"]["calculated_balance"])

    def test_multi_account_net_worth_separates_assets_and_liabilities(self) -> None:
        self._account("acc_checking")
        self._account("acc_savings", "SAVINGS")
        self._account("acc_card", "CREDIT_CARD", include_in_liquidity=False)
        self._snapshot("acc_checking", "2026-07-20", "895.00")
        self._snapshot("acc_savings", "2026-07-20", "5000.00")
        self._snapshot("acc_card", "2026-07-20", "-500.00")
        asset = create_asset_snapshot(
            self.store,
            item_id="asset_fund",
            display_name="Indexfonds",
            item_type="INVESTMENT",
            valuation_date="2026-07-20",
            amount="10000.00",
            currency="EUR",
        )
        correct_asset_snapshot(self.store, asset, "10100.00", "Aktualisierter Kurs")
        create_liability_snapshot(
            self.store,
            item_id="liability_loan",
            display_name="Privatkredit",
            item_type="LOAN",
            valuation_date="2026-07-20",
            amount="2000.00",
            currency="EUR",
        )
        worth = net_worth_overview(self.store, as_of="2026-07-20")
        self.assertEqual(Decimal(worth["liquid_funds"]), Decimal("5895.00"))
        self.assertEqual(Decimal(worth["investments"]), Decimal("10100.00"))
        self.assertEqual(Decimal(worth["liabilities"]), Decimal("2500.00"))
        self.assertEqual(Decimal(worth["net_worth"]), Decimal("13495.00"))
        self.assertEqual(asset_allocation(self.store)["allocation"]["INVESTMENTS"], "10100.00")

    def test_internal_transfer_does_not_change_consolidated_projected_balance(self) -> None:
        self._account("acc_a")
        self._account("acc_b")
        self._snapshot("acc_a", "2026-07-01", "1000.00")
        self._snapshot("acc_b", "2026-07-01", "500.00")
        self._import(
            "transfer-out.csv",
            "acc_a",
            ["2026-07-02,2026-07-02,-200.00,EUR,Eigenes Konto,Umbuchung"],
        )
        self._import(
            "transfer-in.csv",
            "acc_b",
            ["2026-07-02,2026-07-02,200.00,EUR,Eigenes Konto,Umbuchung"],
        )
        detect_transfers(self.store, "2026-07")
        proposal = self.store.events("TransferMatchProposed")[-1]["payload"]
        confirm_transfer(
            self.store,
            proposal["outgoing_transaction_id"],
            proposal["incoming_transaction_id"],
        )
        projected = projected_month_end_balance(self.store, "2026-07")
        self.assertEqual(projected["latest_confirmed_liquid_balance"], "1500.00")
        self.assertEqual(projected["realized_cashflow_since_snapshots"], "0.00")
        self.assertEqual(projected["projected_month_end_balance"], "1500.00")

    def test_foreign_currency_is_visible_but_not_silently_converted(self) -> None:
        self._account("acc_eur")
        self._account("acc_usd", "SAVINGS", currency="USD")
        self._snapshot("acc_eur", "2026-07-20", "1000.00")
        record_balance_snapshot(
            self.store,
            account_id="acc_usd",
            balance_date="2026-07-20",
            booked_balance="500.00",
            available_balance="500.00",
            currency="USD",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        liquidity = liquidity_overview(self.store, "EUR", "2026-07-20")
        self.assertEqual(liquidity["liquid_funds"], "1000.00")
        self.assertEqual(liquidity["currency_conflicts"], ["USD"])

    def test_explicit_foreign_valuation_keeps_original_amount_and_rate(self) -> None:
        snapshot_id = create_asset_snapshot(
            self.store,
            item_id="asset_us_fund",
            display_name="US-Fonds",
            item_type="INVESTMENT",
            valuation_date="2026-07-20",
            amount="1000.00",
            currency="USD",
            valuation_currency="EUR",
            exchange_rate="0.90",
        )
        snapshot = next(
            event["payload"]
            for event in self.store.events("AssetSnapshotRecorded")
            if event["payload"]["snapshot_id"] == snapshot_id
        )
        self.assertEqual(snapshot["original_amount"], "1000.00")
        self.assertEqual(snapshot["original_currency"], "USD")
        self.assertEqual(snapshot["valuation_currency"], "EUR")
        self.assertEqual(snapshot["exchange_rate"], "0.90")
        self.assertEqual(snapshot["valued_amount"], "900.0000")
        worth = net_worth_overview(self.store, "EUR", "2026-07-20")
        self.assertEqual(worth["investments"], "900.0000")
        self.assertEqual(worth["currency_conflicts"], [])

    def test_conflicting_snapshot_priority_and_invalid_update_are_rejected(self) -> None:
        self._account("acc_main")
        self._snapshot("acc_main", "2026-07-01", "1000.00")
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_BALANCE_SNAPSHOT_CONFLICT"):
            self._snapshot("acc_main", "2026-07-01", "1001.00")
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_ACCOUNT_UPDATE_INVALID"):
            update_account(self.store, "acc_main", currency="USD")


if __name__ == "__main__":
    unittest.main()
