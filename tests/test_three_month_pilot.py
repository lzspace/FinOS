from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from typing import Any

from cryptography.fernet import Fernet

from finance_extension.accounts import create_account
from finance_extension.application import FinanceApplicationService
from finance_extension.category_shadow import (
    confirm_category_suggestion,
    generate_category_shadow_suggestion,
)
from finance_extension.classification import (
    active_classifications,
    classify_transactions,
    confirm_classification,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.multi_account_import import (
    analyze_import_file,
    import_mapped_sections,
    initial_balance_requirements,
    map_import_sections,
    reconcile_imported_period_balance,
    record_closing_balance,
    record_opening_balance,
    section_bindings,
    suggested_opening_balance,
)
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.store import LocalFinanceStore


def _monthly_export(month: int, rows: list[str]) -> str:
    last_day = {1: 31, 2: 28, 3: 31}[month]
    return (
        "Bank;PILOT_BANK\n"
        f"Zeitraum;01.{month:02d}.2026 - {last_day:02d}.{month:02d}.2026\n"
        "Umsätze Girokonto\n"
        "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
        + "\n".join(rows)
        + "\n"
    )


MONTHS = {
    "2026-01": _monthly_export(
        1,
        [
            "05.01.2026;05.01.2026;Gutschrift;Gehalt Pilot;2.000,00",
            "10.01.2026;10.01.2026;Lastschrift/Belastung;Pilot Fitness;-50,00",
        ],
    ),
    "2026-02": _monthly_export(
        2,
        [
            "05.02.2026;05.02.2026;Gutschrift;Gehalt Pilot;2.100,00",
            "10.02.2026;10.02.2026;Lastschrift/Belastung;Pilot Fitness;-50,00",
        ],
    ),
    "2026-03": _monthly_export(
        3,
        [
            "05.03.2026;05.03.2026;Gutschrift;Gehalt Pilot;2.200,00",
            "12.03.2026;12.03.2026;Kartenzahlung;Pilot Café;-30,00",
        ],
    ),
}


class PilotCategoryModel:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "category_suggestions": True,
            "model": {
                "model_id": "pilot-local-model",
                "model_version": "1.0.0",
                "runtime_version": "llama.cpp pilot",
                "structured_output_backend": "GBNF",
                "inference_policy_version": "1.1.0",
            },
        }

    def infer_structured(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "category_code": "FOOD_RESTAURANTS",
            "raw_confidence": 0.97,
            "alternatives": [
                {"category_code": "OTHER_EXPENSE", "confidence": 0.03}
            ],
            "reason_codes": ["MERCHANT_SEMANTICS"],
        }

    def start_async(self) -> None:
        return None


class ThreeMonthPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="finance-three-month-pilot-")
        self.root = Path(self.temporary.name)
        self.store_key = StaticKeyProvider(Fernet.generate_key())
        self.archive_key = StaticKeyProvider(Fernet.generate_key())
        self.data_dir = self.root / "workspace"
        self.store = LocalFinanceStore(self.data_dir, self.store_key).open()
        create_account(
            self.store,
            account_id="acc_pilot",
            display_name="Pilot-Girokonto",
            account_type="CHECKING",
            institution="Pilot Bank",
            currency="EUR",
            opened_at="2025-01-01",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _restart(self) -> None:
        self.store.close()
        self.store = LocalFinanceStore(self.data_dir, self.store_key).open()

    def _analyze_and_map(self, month: str) -> dict[str, Any]:
        source = self.root / f"pilot-{month}.csv"
        source.write_bytes(MONTHS[month].encode("cp1252"))
        analysis = analyze_import_file(self.store, source)
        self.assertEqual(analysis["report_month"], month)
        self.assertEqual(analysis["import_profile"], "GermanMultiAccountCsvV1")
        section = analysis["sections"][0]
        if month != "2026-01":
            self.assertEqual(section["mapped_account_id"], "acc_pilot")
        map_import_sections(
            self.store,
            analysis["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": "acc_pilot",
                    "action": "USE_EXISTING_ACCOUNT",
                }
            ],
        )
        return analysis

    def _record_suggestion(self, analysis: dict[str, Any], suggestion: dict[str, Any]) -> None:
        record_opening_balance(
            self.store,
            account_id="acc_pilot",
            balance_date=suggestion["period_end"],
            booked_balance=suggestion["suggested_value"],
            available_balance=None,
            currency="EUR",
            source="PREVIOUS_SYSTEM",
            confirmation=True,
            comment=f"Pilot Carry-forward für {analysis['report_month']}",
            carry_forward_source_reconciliation_id=suggestion[
                "source_reconciliation_id"
            ],
            suggested_value=suggestion["suggested_value"],
            opening_balance_source=suggestion["source"],
            source_period=suggestion["source_period"],
            source_account_id=suggestion["source_account_id"],
            source_projection_sequence=suggestion["source_projection_sequence"],
        )

    def _transactions(self, month: str) -> list[dict[str, Any]]:
        return [
            event["payload"]
            for event in self.store.events("TransactionNormalized")
            if event["payload"]["booking_date"].startswith(month)
        ]

    def _confirm_month_categories(self, month: str) -> None:
        classify_transactions(self.store, month)
        for transaction in self._transactions(month):
            current = active_classifications(self.store)[transaction["transaction_id"]]
            if current["payload"]["category_code"] == "UNCLASSIFIED":
                confirm_classification(
                    self.store,
                    transaction["transaction_id"],
                    "HEALTH",
                    create_rule_from="normalized_description",
                )
            else:
                confirm_classification(
                    self.store,
                    transaction["transaction_id"],
                    current["payload"]["category_code"],
                )

    def test_three_consecutive_months_remain_consistent(self) -> None:
        january = self._analyze_and_map("2026-01")
        record_opening_balance(
            self.store,
            account_id="acc_pilot",
            balance_date="2025-12-31",
            booked_balance="1000.00",
            available_balance=None,
            currency="EUR",
            source="MANUAL_ENTRY",
            confirmation=True,
            comment="Manueller Start des Piloten",
            opening_balance_source="MANUAL",
        )
        self.assertEqual(
            import_mapped_sections(self.store, january["analysis_id"])["status"],
            "COMPLETED",
        )
        record_closing_balance(
            self.store,
            account_id="acc_pilot",
            balance_date="2026-01-31",
            booked_balance="2950.00",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
        )
        self.assertEqual(
            reconcile_imported_period_balance(
                self.store,
                account_id="acc_pilot",
                period_start="2026-01-01",
                period_end="2026-01-31",
            )["status"],
            "MATCHED",
        )
        self._confirm_month_categories("2026-01")
        self._restart()

        february = self._analyze_and_map("2026-02")
        february_suggestion = suggested_opening_balance(
            self.store, account_id="acc_pilot", period_start="2026-02-01"
        )
        self.assertEqual(
            february_suggestion["source"], "REPORTED_PRIOR_CLOSING_BALANCE"
        )
        self.assertEqual(february_suggestion["suggested_value"], "2950.00")
        self._record_suggestion(february, february_suggestion)
        self.assertTrue(
            initial_balance_requirements(self.store, february["analysis_id"])[0][
                "satisfied"
            ]
        )
        self.assertEqual(
            import_mapped_sections(self.store, february["analysis_id"])["status"],
            "COMPLETED",
        )
        self.assertEqual(
            reconcile_imported_period_balance(
                self.store,
                account_id="acc_pilot",
                period_start="2026-02-01",
                period_end="2026-02-28",
            )["status"],
            "NO_REPORTED_CLOSING_BALANCE",
        )
        self._confirm_month_categories("2026-02")
        february_fitness = next(
            item
            for item in self._transactions("2026-02")
            if "pilot fitness" in item["normalized_description"].casefold()
        )
        self.assertEqual(
            active_classifications(self.store)[february_fitness["transaction_id"]][
                "payload"
            ]["category_code"],
            "HEALTH",
        )
        self._restart()

        march = self._analyze_and_map("2026-03")
        march_suggestion = suggested_opening_balance(
            self.store, account_id="acc_pilot", period_start="2026-03-01"
        )
        self.assertEqual(
            march_suggestion["source"], "CALCULATED_PRIOR_CLOSING_BALANCE"
        )
        self.assertEqual(march_suggestion["suggested_value"], "5000.00")
        self._record_suggestion(march, march_suggestion)

        # The calculated carry-forward remains historical truth even when the
        # matching bank-reported value is recorded later.
        record_closing_balance(
            self.store,
            account_id="acc_pilot",
            balance_date="2026-02-28",
            booked_balance="5000.00",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
        )
        self.assertEqual(
            reconcile_imported_period_balance(
                self.store,
                account_id="acc_pilot",
                period_start="2026-02-01",
                period_end="2026-02-28",
            )["status"],
            "MATCHED",
        )
        self.assertEqual(
            import_mapped_sections(self.store, march["analysis_id"])["status"],
            "COMPLETED",
        )
        record_closing_balance(
            self.store,
            account_id="acc_pilot",
            balance_date="2026-03-31",
            booked_balance="7170.00",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
        )
        self.assertEqual(
            reconcile_imported_period_balance(
                self.store,
                account_id="acc_pilot",
                period_start="2026-03-01",
                period_end="2026-03-31",
            )["status"],
            "MATCHED",
        )

        classify_transactions(self.store, "2026-03")
        march_transactions = self._transactions("2026-03")
        salary = next(item for item in march_transactions if Decimal(item["amount"]) > 0)
        confirm_classification(
            self.store,
            salary["transaction_id"],
            active_classifications(self.store)[salary["transaction_id"]]["payload"][
                "category_code"
            ],
        )
        cafe = next(item for item in march_transactions if Decimal(item["amount"]) < 0)
        model = PilotCategoryModel()
        generated = generate_category_shadow_suggestion(
            self.store, model, cafe["transaction_id"], mode="SUGGESTION"
        )
        confirm_category_suggestion(
            self.store, generated["suggestion"]["suggestion_id"]
        )
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(self.store.events("ImportMappingProposed")), 0)
        self.assertEqual(len(section_bindings(self.store)), 1)

        application = FinanceApplicationService(self.store)
        period = {"mode": "MONTH", "year": 2026, "month": 3}
        expected = {
            "dashboard": application.query("GetDashboard", period)["data"],
            "accounts": application.query(
                "ListAccountOverviews", {"period": period}
            )["data"],
            "reviews": application.query("GetReviewInbox")["data"],
        }
        self.assertEqual(expected["dashboard"]["effective_income"], "2200.00")
        self.assertEqual(expected["dashboard"]["effective_expenses"], "30.00")
        self.assertEqual(expected["dashboard"]["net_cashflow"], "2170.00")
        self.assertEqual(expected["reviews"]["items_by_type"]["CATEGORY"], 0)
        self.assertEqual(expected["reviews"]["items_by_type"]["AI_SUGGESTION"], 0)
        self.assertEqual(expected["reviews"]["items_by_type"]["IMPORT"], 0)
        self.assertEqual(expected["reviews"]["items_by_type"]["RELATION"], 0)
        self.assertEqual(expected["reviews"]["items_by_type"]["FORMAT_CHANGE"], 0)
        self.assertEqual(
            [item["entity_id"] for item in expected["reviews"]["items"]],
            ["acc_pilot:stale_balance"],
        )
        self.assertEqual(
            expected["accounts"]["accounts"][0]["period_net_cashflow"], "2170.00"
        )

        application.command("RebuildReadModels", {})
        self.assertEqual(
            application.query("GetDashboard", period)["data"], expected["dashboard"]
        )
        self.assertEqual(
            application.query("ListAccountOverviews", {"period": period})["data"],
            expected["accounts"],
        )
        self.assertEqual(
            application.query("GetReviewInbox")["data"], expected["reviews"]
        )

        backup = create_backup(self.store, self.archive_key, self.root / "backups")
        restored = LocalFinanceStore(
            self.root / "restored-workspace", self.store_key
        ).open()
        try:
            restore_backup(restored, backup["path"], self.archive_key)
            restored_application = FinanceApplicationService(restored)
            self.assertEqual(
                restored_application.query("GetDashboard", period)["data"],
                expected["dashboard"],
            )
            self.assertEqual(
                restored_application.query("GetReviewInbox")["data"],
                expected["reviews"],
            )
        finally:
            restored.close()


if __name__ == "__main__":
    unittest.main()
