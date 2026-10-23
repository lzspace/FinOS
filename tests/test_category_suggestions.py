from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cryptography.fernet import Fernet

from finance_extension.application import FinanceApplicationService
from finance_extension.category_shadow import (
    category_suggestion_states,
    confirm_category_suggestion,
    correct_category_suggestion,
    create_classification_rule_from_suggestion,
    generate_visible_category_suggestions,
    list_category_suggestions,
    reject_category_suggestion,
)
from finance_extension.classification import (
    active_classifications,
    category_breakdown,
    classify_transactions,
    confirm_classification,
    create_rule,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.reconciliation import reconciled_monthly_cashflow
from finance_extension.store import LocalFinanceStore, StoreInvariantError


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-64.32,EUR,REWE Markt,Kartenzahlung Filiale
2026-07-02,2026-07-02,3200.00,EUR,Arbeitgeber,Gehalt Juli
"""


class MockSuggestionProvider:
    def __init__(self, *, model_version: str = "1.0.0", available: bool = True) -> None:
        self.model_version = model_version
        self.available = available
        self.requests: list[Any] = []

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "category_suggestions": self.available,
            "model": {
                "model_id": "finance-assistant-small",
                "model_version": self.model_version,
                "runtime_version": "llama.cpp test",
                "structured_output_backend": "GBNF",
                "inference_policy_version": "1.1.0",
            }
            if self.available
            else None,
        }

    def infer_structured(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "category_code": "FOOD_GROCERIES",
            "raw_confidence": 0.982,
            "alternatives": [
                {"category_code": "HOUSING_UTILITIES", "confidence": 0.012},
                {"category_code": "OTHER_EXPENSE", "confidence": 0.006},
            ],
            "reason_codes": ["MERCHANT_SEMANTICS", "DESCRIPTION_SEMANTICS"],
        }

    def start_async(self) -> None:
        return None


class CategorySuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(self.data, self.key).open()
        source = self.root / "transactions.csv"
        source.write_text(CSV, encoding="utf-8")
        batch = import_csv(self.store, source, "acc_giro")
        normalize_batch(self.store, batch)
        transactions = self.store.events("TransactionNormalized")
        self.debit = transactions[0]["payload"]["transaction_id"]
        self.credit = transactions[1]["payload"]["transaction_id"]
        classify_transactions(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _generate(self, provider: MockSuggestionProvider | None = None) -> dict[str, Any]:
        provider = provider or MockSuggestionProvider()
        result = generate_visible_category_suggestions(self.store, provider)
        self.assertEqual(result["generated"], 1)
        return list_category_suggestions(self.store, {"status": "PENDING"})[0]

    def test_pending_suggestion_has_no_finance_projection_side_effect(self) -> None:
        before_breakdown = category_breakdown(self.store, "2026-07")
        before_cashflow = reconciled_monthly_cashflow(self.store, "2026-07")
        before_forecasts = len(self.store.events("ForecastCreated"))

        suggestion = self._generate()

        self.assertEqual(suggestion["status"], "PENDING")
        self.assertEqual(category_breakdown(self.store, "2026-07"), before_breakdown)
        self.assertEqual(reconciled_monthly_cashflow(self.store, "2026-07"), before_cashflow)
        self.assertEqual(len(self.store.events("ForecastCreated")), before_forecasts)
        self.assertEqual(len(self.store.events("ClassificationRuleCreated")), 0)

    def test_confirm_creates_exactly_one_user_confirmed_classification(self) -> None:
        suggestion = self._generate()

        first = confirm_category_suggestion(self.store, suggestion["suggestion_id"])
        second = confirm_category_suggestion(self.store, suggestion["suggestion_id"])

        confirmed = [
            event
            for event in self.store.events("TransactionClassificationConfirmed")
            if event["payload"]["transaction_id"] == self.debit
        ]
        self.assertEqual(first["status"], "CONFIRMED")
        self.assertEqual(second["status"], "CONFIRMED")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["payload"]["category_code"], "FOOD_GROCERIES")
        self.assertEqual(
            confirmed[0]["payload"]["confirmed_by"],
            "LOCAL_LLM_CONFIRMED_BY_USER",
        )
        self.assertEqual(
            confirmed[0]["payload"]["suggestion_id"], suggestion["suggestion_id"]
        )
        self.assertEqual(len(self.store.events("CategorySuggestionEvaluated")), 1)

    def test_correction_wins_and_rejection_keeps_transaction_unclassified(self) -> None:
        corrected = self._generate()
        result = correct_category_suggestion(
            self.store, corrected["suggestion_id"], "HOUSING_UTILITIES"
        )
        self.assertEqual(result["status"], "CORRECTED")
        self.assertEqual(
            active_classifications(self.store)[self.debit]["payload"]["category_code"],
            "HOUSING_UTILITIES",
        )

        self.store.close()
        second_data = self.root / "reject-data"
        self.store = LocalFinanceStore(second_data, self.key).open()
        source = self.root / "reject.csv"
        source.write_text(CSV, encoding="utf-8")
        batch = import_csv(self.store, source, "acc_giro")
        normalize_batch(self.store, batch)
        self.debit = self.store.events("TransactionNormalized")[0]["payload"]["transaction_id"]
        classify_transactions(self.store)
        rejected = self._generate()
        reject_category_suggestion(self.store, rejected["suggestion_id"])
        current = active_classifications(self.store)[self.debit]
        self.assertNotEqual(current["event_type"], "TransactionClassificationConfirmed")

    def test_user_decision_and_deterministic_rule_prevent_model_call(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        provider = MockSuggestionProvider()
        generate_visible_category_suggestions(self.store, provider)
        self.assertEqual(len(provider.requests), 0)

        self.store.close()
        rule_data = self.root / "rule-data"
        self.store = LocalFinanceStore(rule_data, self.key).open()
        source = self.root / "rule.csv"
        source.write_text(CSV, encoding="utf-8")
        batch = import_csv(self.store, source, "acc_giro")
        normalize_batch(self.store, batch)
        self.debit = self.store.events("TransactionNormalized")[0]["payload"]["transaction_id"]
        create_rule(
            self.store,
            field="counterparty",
            operator="EQUALS",
            value="REWE Markt",
            category_code="FOOD_GROCERIES",
            priority=250,
        )
        classify_transactions(self.store)
        ruled_provider = MockSuggestionProvider()
        generate_visible_category_suggestions(self.store, ruled_provider)
        self.assertEqual(len(ruled_provider.requests), 0)

    def test_rule_creation_is_explicit_audited_and_idempotent(self) -> None:
        suggestion = self._generate()
        confirm_category_suggestion(self.store, suggestion["suggestion_id"])

        first = create_classification_rule_from_suggestion(
            self.store, suggestion["suggestion_id"]
        )
        second = create_classification_rule_from_suggestion(
            self.store, suggestion["suggestion_id"]
        )

        self.assertEqual(first, second)
        self.assertEqual(len(self.store.events("ClassificationRuleCreated")), 1)
        self.assertEqual(
            len(self.store.events("ClassificationRuleCreatedFromSuggestion")), 1
        )

    def test_new_model_supersedes_pending_without_overwriting_it(self) -> None:
        first = self._generate(MockSuggestionProvider(model_version="1.0.0"))
        second = self._generate(MockSuggestionProvider(model_version="1.1.0"))
        states = category_suggestion_states(self.store)

        self.assertEqual(states[first["suggestion_id"]]["status"], "SUPERSEDED")
        self.assertEqual(states[second["suggestion_id"]]["status"], "PENDING")
        self.assertEqual(len(self.store.events("CategorySuggestionGenerated")), 2)

    def test_stale_suggestion_is_blocked(self) -> None:
        suggestion = self._generate()
        with patch("finance_extension.category_shadow.TAXONOMY_VERSION", "2.0.0"):
            with self.assertRaisesRegex(StoreInvariantError, "CATEGORY_SUGGESTION_STALE"):
                confirm_category_suggestion(self.store, suggestion["suggestion_id"])

    def test_model_unavailable_does_not_block_manual_classification(self) -> None:
        result = generate_visible_category_suggestions(
            self.store, MockSuggestionProvider(available=False)
        )
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")

        self.assertGreater(result["skipped"], 0)
        self.assertEqual(
            active_classifications(self.store)[self.debit]["payload"]["category_code"],
            "FOOD_GROCERIES",
        )

    def test_restart_backup_restore_and_public_contract_keep_open_suggestion(self) -> None:
        suggestion = self._generate()
        backup_key = StaticKeyProvider(Fernet.generate_key())
        backup = create_backup(self.store, backup_key, self.root / "backups")
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        self.assertEqual(
            category_suggestion_states(self.store)[suggestion["suggestion_id"]]["status"],
            "PENDING",
        )
        reject_category_suggestion(self.store, suggestion["suggestion_id"])
        restore_backup(self.store, backup["path"], backup_key)

        application = FinanceApplicationService(
            self.store, local_model_service=MockSuggestionProvider()  # type: ignore[arg-type]
        )
        pending = application.query("ListPendingCategorySuggestions")["data"]
        summary = application.query("GetCategorySuggestionReviewSummary")["data"]
        application.command(
            "ConfirmCategorySuggestion", {"suggestion_id": suggestion["suggestion_id"]}
        )

        self.assertEqual(len(pending["suggestions"]), 1)
        self.assertEqual(summary["pending_count"], 1)
        self.assertFalse(summary["automatic_acceptance"])


if __name__ == "__main__":
    unittest.main()
