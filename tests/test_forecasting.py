from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from finance_extension.cli import _parser
from finance_extension.crypto import StaticKeyProvider
from finance_extension.forecasting import (
    confirm_recurring_pattern,
    create_forecasts,
    detect_recurring_patterns,
    end_recurring_pattern,
    evaluate_forecast,
    expected_transactions,
    forecast_accuracy,
    generate_expected_transactions,
    match_expected_transactions,
    monthly_forecast,
    pause_recurring_pattern,
    recurring_patterns,
    reject_recurring_pattern,
    update_recurring_pattern,
)
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.reconciliation import (
    confirm_refund,
    confirm_transfer,
    detect_duplicates,
    detect_refunds,
    detect_transfers,
    relation_review,
)
from finance_extension.store import LocalFinanceStore


HEADER = "booking_date,value_date,amount,currency,counterparty,description\n"


class ForecastingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(self.data, self.key).open()
        self._import(
            "history.csv",
            "acc_main",
            [
                "2024-03-10,2024-03-10,-600.00,EUR,Versicherung Beispiel,Jahresbeitrag",
                "2025-03-10,2025-03-10,-600.00,EUR,Versicherung Beispiel,Jahresbeitrag",
                "2026-01-01,2026-01-01,3200.00,EUR,Arbeitgeber Beispiel,Gehalt",
                "2026-02-01,2026-02-01,3200.00,EUR,Arbeitgeber Beispiel,Gehalt",
                "2026-03-01,2026-03-01,3200.00,EUR,Arbeitgeber Beispiel,Gehalt",
                "2026-04-02,2026-04-02,3200.00,EUR,Arbeitgeber Beispiel,Gehalt",
                "2026-01-03,2026-01-03,-1000.00,EUR,Vermieter Beispiel,Miete",
                "2026-02-03,2026-02-03,-1000.00,EUR,Vermieter Beispiel,Miete",
                "2026-03-03,2026-03-03,-1000.00,EUR,Vermieter Beispiel,Miete",
                "2026-01-05,2026-01-05,-80.00,EUR,Strom Beispiel,Abschlag",
                "2026-02-05,2026-02-05,-82.00,EUR,Strom Beispiel,Abschlag",
                "2026-03-05,2026-03-05,-81.00,EUR,Strom Beispiel,Abschlag",
                "2026-01-15,2026-01-15,-17.99,EUR,Netflix Beispiel,Abo",
                "2026-02-15,2026-02-15,-17.99,EUR,Netflix Beispiel,Abo",
                "2026-03-15,2026-03-15,-17.99,EUR,Netflix Beispiel,Abo",
                "2026-01-20,2026-01-20,-200.00,EUR,Einmalig Januar,Variabel",
                "2026-02-20,2026-02-20,-300.00,EUR,Einmalig Februar,Variabel",
                "2026-03-20,2026-03-20,-250.00,EUR,Einmalig März,Variabel",
                "2026-03-10,2026-03-10,-600.00,EUR,Versicherung Beispiel,Jahresbeitrag",
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

    def _pattern(self, merchant: str) -> dict[str, object]:
        return next(
            pattern
            for pattern in recurring_patterns(self.store).values()
            if pattern["merchant_key"] == merchant.casefold()
        )

    def _transaction(self, account: str, booking_date: str, amount: str) -> str:
        return next(
            event["payload"]["transaction_id"]
            for event in self.store.events("TransactionNormalized")
            if event["payload"]["account_id"] == account
            and event["payload"]["booking_date"] == booking_date
            and event["payload"]["amount"] == amount
        )

    def _detect(self) -> None:
        detect_recurring_patterns(self.store, "2024-01", "2026-03")

    def _confirm_monthly_patterns(self) -> None:
        self._detect()
        for merchant in (
            "Arbeitgeber Beispiel",
            "Vermieter Beispiel",
            "Strom Beispiel",
            "Netflix Beispiel",
            "Versicherung Beispiel",
        ):
            confirm_recurring_pattern(self.store, self._pattern(merchant)["pattern_id"])

    def test_detects_monthly_variable_and_annual_patterns_idempotently(self) -> None:
        self.assertEqual(detect_recurring_patterns(self.store, "2024-01", "2026-03"), 5)
        self.assertEqual(detect_recurring_patterns(self.store, "2024-01", "2026-03"), 0)
        salary = self._pattern("Arbeitgeber Beispiel")
        electricity = self._pattern("Strom Beispiel")
        insurance = self._pattern("Versicherung Beispiel")
        self.assertEqual(salary["frequency"], "MONTHLY")
        self.assertEqual(electricity["expected_amount"], "81.00")
        self.assertEqual(electricity["amount_tolerance"], "1.00")
        self.assertEqual(insurance["frequency"], "ANNUALLY")

    def test_rejection_update_pause_and_end_are_versioned_and_protected(self) -> None:
        self._detect()
        netflix = self._pattern("Netflix Beispiel")["pattern_id"]
        salary = self._pattern("Arbeitgeber Beispiel")["pattern_id"]
        reject_recurring_pattern(self.store, netflix)
        self.assertEqual(detect_recurring_patterns(self.store, "2024-01", "2026-03"), 0)
        confirm_recurring_pattern(self.store, salary)
        update_recurring_pattern(self.store, salary, amount="3300.00", day_from=1, day_to=5)
        self.assertEqual(
            update_recurring_pattern(self.store, salary, amount="3300.00", day_from=1, day_to=5),
            0,
        )
        self.assertEqual(recurring_patterns(self.store)[salary]["expected_amount"], "3300.00")
        pause_recurring_pattern(self.store, salary)
        self.assertEqual(generate_expected_transactions(self.store, "2026-04"), 0)
        end_recurring_pattern(self.store, salary)
        self.assertEqual(recurring_patterns(self.store)[salary]["status"], "ENDED")

    def test_expected_transactions_match_real_booking_and_leave_missing_visible(self) -> None:
        self._confirm_monthly_patterns()
        self.assertEqual(generate_expected_transactions(self.store, "2026-04"), 5)
        expectations = expected_transactions(self.store)
        salary = next(
            item for item in expectations.values() if item["merchant_key"] == "arbeitgeber beispiel"
        )
        rent = next(
            item for item in expectations.values() if item["merchant_key"] == "vermieter beispiel"
        )
        self.assertEqual(salary["status"], "MATCHED")
        self.assertIsNotNone(salary["matched_transaction_id"])
        self.assertEqual(rent["status"], "EXPECTED")

    def test_later_import_matches_an_existing_expectation_idempotently(self) -> None:
        self._confirm_monthly_patterns()
        generate_expected_transactions(self.store, "2026-05")
        salary_before = next(
            item
            for item in expected_transactions(self.store).values()
            if item["merchant_key"] == "arbeitgeber beispiel"
            and item["expected_date"].startswith("2026-05")
        )
        self.assertEqual(salary_before["status"], "EXPECTED")
        self._import(
            "may-salary.csv",
            "acc_main",
            ["2026-05-02,2026-05-02,3200.00,EUR,Arbeitgeber Beispiel,Gehalt"],
        )
        self.assertEqual(match_expected_transactions(self.store, "2026-05"), 1)
        self.assertEqual(match_expected_transactions(self.store, "2026-05"), 0)
        self.assertEqual(
            expected_transactions(self.store)[salary_before["expected_transaction_id"]]["status"],
            "MATCHED",
        )

    def test_forecast_scenarios_are_deterministic_idempotent_and_immutable(self) -> None:
        self._confirm_monthly_patterns()
        self.assertEqual(create_forecasts(self.store, "2026-04"), 3)
        self.assertEqual(create_forecasts(self.store, "2026-04"), 0)
        scenarios = monthly_forecast(self.store, "2026-04")
        self.assertEqual(set(scenarios), {"CONSERVATIVE", "BASE", "OPTIMISTIC"})
        conservative = scenarios["CONSERVATIVE"]["predicted_surplus"]
        base = scenarios["BASE"]["predicted_surplus"]
        optimistic = scenarios["OPTIMISTIC"]["predicted_surplus"]
        self.assertLess(Decimal(conservative), Decimal(base))
        self.assertLess(Decimal(base), Decimal(optimistic))
        self.assertEqual(scenarios["BASE"]["predicted_variable_expenses"], "250.00")

        original_forecast_id = scenarios["BASE"]["forecast_id"]
        original_payload = dict(scenarios["BASE"])
        self._import(
            "new-april.csv",
            "acc_main",
            ["2026-04-25,2026-04-25,-50.00,EUR,Einmalig April,Variabel"],
        )
        self.assertEqual(create_forecasts(self.store, "2026-04"), 6)
        created = next(
            event["payload"]
            for event in self.store.events("ForecastCreated")
            if event["payload"]["forecast_id"] == original_forecast_id
        )
        self.assertEqual(created, original_payload)

    def test_evaluation_marks_missed_expectations_and_rebuilds_after_restart(self) -> None:
        self._confirm_monthly_patterns()
        create_forecasts(self.store, "2026-04")
        self.assertGreater(evaluate_forecast(self.store, "2026-04"), 1)
        self.assertEqual(evaluate_forecast(self.store, "2026-04"), 0)
        accuracy = forecast_accuracy(self.store)
        self.assertEqual(len(accuracy), 1)
        self.assertGreater(accuracy[0]["expected_transactions_missed"], 0)
        self.assertIn("actual_variable_expenses", accuracy[0]["component_accuracy"])

        before = monthly_forecast(self.store, "2026-04")
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        self.assertEqual(before, monthly_forecast(self.store, "2026-04"))

    def test_duplicates_transfers_and_full_refunds_do_not_create_patterns(self) -> None:
        self._import(
            "duplicate-and-relations.csv",
            "acc_main",
            [
                "2026-01-15,2026-01-15,-17.99,EUR,Netflix Beispiel,Abo",
                "2026-01-21,2026-01-21,-100.00,EUR,Shop Beispiel,Einkauf",
                "2026-01-22,2026-01-22,100.00,EUR,Shop Beispiel,Erstattung",
                "2026-02-21,2026-02-21,-100.00,EUR,Shop Beispiel,Einkauf",
                "2026-02-22,2026-02-22,100.00,EUR,Shop Beispiel,Erstattung",
                "2026-03-21,2026-03-21,-100.00,EUR,Shop Beispiel,Einkauf",
                "2026-03-22,2026-03-22,100.00,EUR,Shop Beispiel,Erstattung",
                "2026-01-10,2026-01-10,-500.00,EUR,Eigenes Konto,Umbuchung",
                "2026-02-10,2026-02-10,-500.00,EUR,Eigenes Konto,Umbuchung",
                "2026-03-10,2026-03-10,-500.00,EUR,Eigenes Konto,Umbuchung",
            ],
        )
        self._import(
            "transfer-income.csv",
            "acc_savings",
            [
                "2026-01-11,2026-01-11,500.00,EUR,Eigenes Konto,Umbuchung",
                "2026-02-11,2026-02-11,500.00,EUR,Eigenes Konto,Umbuchung",
                "2026-03-11,2026-03-11,500.00,EUR,Eigenes Konto,Umbuchung",
            ],
        )
        detect_duplicates(self.store)
        detect_transfers(self.store)
        for relation in list(relation_review(self.store, "transfers")):
            confirm_transfer(
                self.store,
                relation["payload"]["outgoing_transaction_id"],
                relation["payload"]["incoming_transaction_id"],
            )
        detect_refunds(self.store)
        for month in ("01", "02", "03"):
            original = self._transaction("acc_main", f"2026-{month}-21", "-100.00")
            refund = self._transaction("acc_main", f"2026-{month}-22", "100.00")
            confirm_refund(self.store, refund, original, "100.00")

        detect_recurring_patterns(self.store, "2024-01", "2026-03")
        patterns = recurring_patterns(self.store).values()
        merchants = {pattern["merchant_key"] for pattern in patterns}
        self.assertNotIn("eigenes konto", merchants)
        self.assertNotIn("shop beispiel", merchants)
        netflix = self._pattern("Netflix Beispiel")
        self.assertEqual(len(netflix["source_transaction_ids"]), 3)

    def test_cli_and_schema_resolution_remain_offline(self) -> None:
        parser = _parser()
        parser.parse_args(
            [
                "--data-dir",
                "/tmp/finance",
                "recurring",
                "detect",
                "--from",
                "2025-01",
                "--to",
                "2026-07",
            ]
        )
        parser.parse_args(
            ["--data-dir", "/tmp/finance", "forecast", "create", "--month", "2026-08"]
        )
        with patch("socket.getaddrinfo", side_effect=AssertionError("network access")):
            self._detect()
