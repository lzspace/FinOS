from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from finance_extension.classification import classify_transactions, confirm_classification
from finance_extension.cli import _parser
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.reconciliation import (
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
from finance_extension.store import LocalFinanceStore, StoreInvariantError


HEADER = "booking_date,value_date,amount,currency,counterparty,description\n"


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(self.data, self.key).open()
        self._import(
            "giro-a.csv",
            "acc_giro",
            [
                "2026-07-01,2026-07-01,-120.00,EUR,Shop Beispiel,Einkauf",
                "2026-07-05,2026-07-05,-20.00,EUR,Cafe Beispiel,Kaffee",
                "2026-07-06,2026-07-06,-85.00,EUR,Store Beispiel,Einkauf Zwei",
                "2026-07-10,2026-07-10,-500.00,EUR,Eigenes Konto,Umbuchung",
            ],
        )
        self._import(
            "giro-b.csv",
            "acc_giro",
            [
                "2026-07-01,2026-07-01,-120.00,EUR,Shop Beispiel,Einkauf",
                "2026-07-06,2026-07-06,-20.00,EUR,Cafe Beispiel,Kaffee",
                "2026-07-15,2026-07-15,40.00,EUR,Shop Beispiel,Erstattung",
                "2026-07-20,2026-07-20,85.00,EUR,Store Beispiel,Erstattung",
            ],
        )
        self._import(
            "savings.csv",
            "acc_savings",
            ["2026-07-11,2026-07-11,500.00,EUR,Eigenes Konto,Umbuchung"],
        )
        self._import(
            "august.csv",
            "acc_giro",
            [
                "2026-08-02,2026-08-02,30.00,EUR,Shop Beispiel,Erstattung Teil Zwei",
                "2026-08-03,2026-08-03,10.00,EUR,Store Beispiel,Erstattung Zusatz",
            ],
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _import(self, filename: str, account: str, rows: list[str]) -> None:
        source = self.root / filename
        source.write_text(HEADER + "\n".join(rows) + "\n")
        batch = import_csv(self.store, source, account)
        normalize_batch(self.store, batch)

    def _transaction(
        self, *, amount: str, counterparty: str, booking_date: str, account: str = "acc_giro"
    ) -> str:
        return next(
            event["payload"]["transaction_id"]
            for event in self.store.events("TransactionNormalized")
            if event["payload"]["amount"] == amount
            and event["payload"]["counterparty"] == counterparty
            and event["payload"]["booking_date"] == booking_date
            and event["payload"]["account_id"] == account
        )

    def _confirmed_duplicate(self) -> dict[str, object]:
        return self.store.events("DuplicateTransactionConfirmed")[0]["payload"]

    def test_exact_and_likely_duplicates_are_distinct_and_idempotent(self) -> None:
        self.assertEqual(detect_duplicates(self.store, "2026-07"), 3)
        self.assertEqual(detect_duplicates(self.store, "2026-07"), 0)
        exact = self._confirmed_duplicate()
        self.assertEqual(exact["match_type"], "EXACT_DUPLICATE")
        review = relation_review(self.store, "duplicates")
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["payload"]["match_type"], "LIKELY_DUPLICATE")
        relation_id = review[0]["aggregate_id"]
        self.assertEqual(reject_duplicate(self.store, relation_id), 1)
        self.assertEqual(reject_duplicate(self.store, relation_id), 0)

    def test_user_duplicate_confirmation_and_refund_rejection_are_idempotent(self) -> None:
        detect_duplicates(self.store)
        duplicate_relation = relation_review(self.store, "duplicates")[0]["aggregate_id"]
        self.assertEqual(confirm_duplicate(self.store, duplicate_relation), 1)
        self.assertEqual(confirm_duplicate(self.store, duplicate_relation), 0)
        detect_refunds(self.store)
        refund = relation_review(self.store, "refunds")[0]["payload"]
        self.assertEqual(
            reject_refund(
                self.store,
                refund["refund_transaction_id"],
                refund["original_transaction_id"],
            ),
            1,
        )
        self.assertEqual(
            reject_refund(
                self.store,
                refund["refund_transaction_id"],
                refund["original_transaction_id"],
            ),
            0,
        )

    def test_transfer_with_one_day_difference_can_be_rejected_confirmed_and_broken(self) -> None:
        self.assertEqual(detect_transfers(self.store, "2026-07"), 1)
        outgoing = self._transaction(
            amount="-500.00", counterparty="Eigenes Konto", booking_date="2026-07-10"
        )
        incoming = self._transaction(
            amount="500.00",
            counterparty="Eigenes Konto",
            booking_date="2026-07-11",
            account="acc_savings",
        )
        self.assertEqual(reject_transfer(self.store, outgoing, incoming), 1)
        self.assertEqual(reject_transfer(self.store, outgoing, incoming), 0)

        # A rejected relation stays immutable; a new test store verifies confirmation and break.
        relation = self.store.events("TransferMatchProposed")[0]
        self.assertEqual(relation["payload"]["confidence"], "0.9")

    def test_confirmed_transfer_is_cashflow_neutral_and_break_restores_legs(self) -> None:
        detect_transfers(self.store)
        outgoing = self._transaction(
            amount="-500.00", counterparty="Eigenes Konto", booking_date="2026-07-10"
        )
        incoming = self._transaction(
            amount="500.00",
            counterparty="Eigenes Konto",
            booking_date="2026-07-11",
            account="acc_savings",
        )
        self.assertEqual(confirm_transfer(self.store, outgoing, incoming), 1)
        self.assertEqual(confirm_transfer(self.store, outgoing, incoming), 0)
        projection = reconciled_monthly_cashflow(self.store, "2026-07")
        self.assertEqual(projection["internal_transfers"], 500)
        self.assertEqual(reconciled_transactions(self.store)[outgoing]["cashflow_relevant"], False)
        self.assertEqual(break_transfer(self.store, outgoing, incoming), 1)
        self.assertEqual(break_transfer(self.store, outgoing, incoming), 0)
        self.assertTrue(reconciled_transactions(self.store)[outgoing]["cashflow_relevant"])

    def test_transaction_cannot_join_two_active_transfers(self) -> None:
        self._import(
            "third-account.csv",
            "acc_third",
            ["2026-07-10,2026-07-10,-500.00,EUR,Eigenes Konto,Umbuchung"],
        )
        self.assertGreaterEqual(detect_transfers(self.store), 2)
        outgoing = self._transaction(
            amount="-500.00",
            counterparty="Eigenes Konto",
            booking_date="2026-07-10",
            account="acc_giro",
        )
        other_outgoing = self._transaction(
            amount="-500.00",
            counterparty="Eigenes Konto",
            booking_date="2026-07-10",
            account="acc_third",
        )
        incoming = self._transaction(
            amount="500.00",
            counterparty="Eigenes Konto",
            booking_date="2026-07-11",
            account="acc_savings",
        )
        confirm_transfer(self.store, outgoing, incoming)
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_TRANSFER_MEMBER_ALREADY_ACTIVE"):
            confirm_transfer(self.store, other_outgoing, incoming)

    def test_partial_full_multiple_and_following_month_refunds(self) -> None:
        detect_duplicates(self.store)
        self.assertGreaterEqual(detect_refunds(self.store), 4)
        primary = self._confirmed_duplicate()["primary_transaction_id"]
        refund_40 = self._transaction(
            amount="40.00", counterparty="Shop Beispiel", booking_date="2026-07-15"
        )
        refund_30 = self._transaction(
            amount="30.00", counterparty="Shop Beispiel", booking_date="2026-08-02"
        )
        original_85 = self._transaction(
            amount="-85.00", counterparty="Store Beispiel", booking_date="2026-07-06"
        )
        refund_85 = self._transaction(
            amount="85.00", counterparty="Store Beispiel", booking_date="2026-07-20"
        )
        refund_10 = self._transaction(
            amount="10.00", counterparty="Store Beispiel", booking_date="2026-08-03"
        )

        self.assertEqual(confirm_refund(self.store, refund_40, primary, "40.00"), 1)
        self.assertEqual(confirm_refund(self.store, refund_40, primary, "40.00"), 0)
        self.assertEqual(confirm_refund(self.store, refund_30, primary, "30.00"), 1)
        self.assertEqual(confirm_refund(self.store, refund_85, original_85, "85.00"), 1)
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_REFUND_EXCEEDS_ORIGINAL"):
            confirm_refund(self.store, refund_10, original_85, "10.00")

        view = reconciled_transactions(self.store)
        self.assertEqual(view[primary]["effective_amount"], -50)
        self.assertEqual(view[original_85]["effective_amount"], 0)
        july = reconciled_monthly_cashflow(self.store, "2026-07")
        august = reconciled_monthly_cashflow(self.store, "2026-08")
        self.assertEqual(july["refunds"], 125)
        self.assertEqual(august["refunds"], 30)
        self.assertEqual(august["net_cashflow"], 40)

    def test_reconciled_category_projection_inherits_original_category_and_rebuilds(self) -> None:
        detect_duplicates(self.store)
        detect_refunds(self.store)
        primary = self._confirmed_duplicate()["primary_transaction_id"]
        refund = self._transaction(
            amount="40.00", counterparty="Shop Beispiel", booking_date="2026-07-15"
        )
        confirm_refund(self.store, refund, primary, "40.00")
        classify_transactions(self.store)
        confirm_classification(self.store, primary, "FOOD_GROCERIES")
        before = reconciled_category_breakdown(self.store, "2026-07")
        self.assertEqual(before["categories"]["FOOD_GROCERIES"]["refund_amount"], 40)
        effective_categories = sum(
            item["effective_expense"] for item in before["categories"].values()
        )
        self.assertEqual(
            effective_categories,
            reconciled_monthly_cashflow(self.store, "2026-07")["effective_expenses"],
        )

        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        rebuilt = reconciled_category_breakdown(self.store, "2026-07")
        self.assertEqual(before, rebuilt)

    def test_reconcile_and_cli_surface_are_idempotent(self) -> None:
        first = reconcile(self.store, "2026-07")
        second = reconcile(self.store, "2026-07")
        self.assertTrue(any(first.values()))
        self.assertEqual(second, {"duplicates": 0, "transfers": 0, "refunds": 0})
        parser = _parser()
        parser.parse_args(
            ["--data-dir", "/tmp/finance", "cashflow", "--month", "2026-07", "--reconciled"]
        )
        parser.parse_args(
            [
                "--data-dir",
                "/tmp/finance",
                "transfer",
                "confirm",
                "--outgoing",
                "txn_out",
                "--incoming",
                "txn_in",
            ]
        )
        parser.parse_args(
            [
                "--data-dir",
                "/tmp/finance",
                "refund",
                "confirm",
                "--refund",
                "txn_ref",
                "--original",
                "txn_orig",
                "--amount",
                "40.00",
            ]
        )

    def test_reconciliation_never_attempts_network_access(self) -> None:
        with patch("socket.getaddrinfo", side_effect=AssertionError("network access")):
            reconcile(self.store, "2026-07")
