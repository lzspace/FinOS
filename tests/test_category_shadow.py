from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cryptography.fernet import Fernet

from finance_extension.category_shadow import (
    category_evaluation_summary,
    category_shadow_status,
    enable_category_shadow_mode,
    evaluate_category_suggestions,
    generate_category_shadow_suggestion,
    get_category_evaluation_run,
    resume_category_evaluation_run,
    start_category_evaluation_run,
    transaction_eligibility,
    validate_category_suggestion,
)
from finance_extension.classification import (
    active_classifications,
    category_breakdown,
    confirm_classification,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.local_model import LocalModelError
from finance_extension.application import FinanceApplicationService
from finance_extension.reconciliation import reconciled_monthly_cashflow
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-64.32,EUR,REWE Markt,Kartenzahlung REWE 123456789
2026-07-02,2026-07-02,3200.00,EUR,Arbeitgeber,Gehalt Personalnummer 99887766
"""


def valid_suggestion(category: str = "FOOD_GROCERIES") -> dict[str, Any]:
    return {
        "category_code": category,
        "raw_confidence": 0.982,
        "alternatives": [
            {"category_code": "HOUSING_UTILITIES", "confidence": 0.012},
            {"category_code": "OTHER_EXPENSE", "confidence": 0.006},
        ],
        "reason_codes": ["MERCHANT_SEMANTICS", "DESCRIPTION_SEMANTICS"],
    }


class MockLocalModelProvider:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        model_version: str = "1.0.0",
        policy_version: str = "1.1.0",
        error_code: str | None = None,
    ) -> None:
        self.response = response or valid_suggestion()
        self.model_version = model_version
        self.policy_version = policy_version
        self.error_code = error_code
        self.requests: list[Any] = []

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "category_suggestions": True,
            "model": {
                "model_id": "finance-assistant-small",
                "model_version": self.model_version,
                "runtime_version": "llama.cpp b11794-f84909536",
                "structured_output_backend": "GBNF",
                "inference_policy_version": self.policy_version,
            },
        }

    def infer_structured(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.error_code:
            raise LocalModelError(self.error_code)
        return self.response

    def start_async(self) -> None:
        return None


class CategoryShadowTests(unittest.TestCase):
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
        transactions = [
            event["payload"]["transaction_id"]
            for event in self.store.events("TransactionNormalized")
        ]
        self.debit, self.credit = transactions

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_debit_and_credit_are_eligible_and_input_is_minimized(self) -> None:
        self.assertTrue(transaction_eligibility(self.store, self.debit)["eligible"])
        self.assertTrue(transaction_eligibility(self.store, self.credit)["eligible"])
        enable_category_shadow_mode(self.store)
        model = MockLocalModelProvider()

        result = generate_category_shadow_suggestion(
            self.store, model, self.debit, mode="LIVE_SHADOW"
        )

        self.assertEqual(result["status"], "GENERATED")
        prompt = json.loads(model.requests[0].prompt)
        self.assertEqual(set(prompt), {"purpose", "transaction", "allowed_categories"})
        self.assertNotIn("account_id", model.requests[0].prompt)
        self.assertNotIn(self.debit, model.requests[0].prompt)
        self.assertNotIn("123456789", model.requests[0].prompt)
        self.assertEqual(prompt["transaction"]["amount_bucket"], "50_100")
        self.assertNotIn("final_category", model.requests[0].prompt)

    def test_validator_rejects_unknown_main_duplicate_alternative_and_confidence(self) -> None:
        allowed = ["FOOD_GROCERIES", "HOUSING_UTILITIES", "OTHER_EXPENSE"]
        unknown = valid_suggestion("MADE_UP")
        duplicate = valid_suggestion()
        duplicate["alternatives"].append(
            {"category_code": "HOUSING_UTILITIES", "confidence": 0.1}
        )
        main_alternative = valid_suggestion()
        main_alternative["alternatives"].append(
            {"category_code": "FOOD_GROCERIES", "confidence": 0.1}
        )
        bad_reason = valid_suggestion()
        bad_reason["reason_codes"] = ["CHAIN_OF_THOUGHT"]
        too_high = valid_suggestion()
        too_high["raw_confidence"] = 1.1

        for value in (unknown, duplicate, main_alternative, bad_reason, too_high):
            with self.subTest(value=value):
                self.assertFalse(
                    validate_category_suggestion(value, allowed_categories=allowed)
                )

    def test_relations_investment_and_cancelled_transactions_are_excluded(self) -> None:
        relation_states = (
            {"category_relevant": False, "refund_status": "NONE"},
            {"category_relevant": True, "refund_status": "REFUND"},
            {"category_relevant": True, "refund_status": "REFUNDED"},
        )
        for relation in relation_states:
            with self.subTest(relation=relation), patch(
                "finance_extension.category_shadow.reconciled_transactions",
                return_value={self.debit: relation},
            ):
                self.assertFalse(
                    transaction_eligibility(self.store, self.debit)["eligible"]
                )
        base = next(
            event["payload"]
            for event in self.store.events("TransactionNormalized")
            if event["payload"]["transaction_id"] == self.debit
        )
        for changes in (
            {"transaction_type": "INVESTMENT_PURCHASE_FUNDING"},
            {"status": "CANCELLED"},
            {"status": "SUPERSEDED"},
        ):
            with self.subTest(changes=changes), patch(
                "finance_extension.category_shadow._transactions",
                return_value={self.debit: {**base, **changes}},
            ):
                self.assertFalse(
                    transaction_eligibility(self.store, self.debit)["eligible"]
                )

    def test_invalid_output_and_timeout_are_persisted_without_suggestion(self) -> None:
        enable_category_shadow_mode(self.store)
        invalid = MockLocalModelProvider(valid_suggestion("UNKNOWN_CATEGORY"))
        invalid_result = generate_category_shadow_suggestion(
            self.store, invalid, self.debit, mode="LIVE_SHADOW"
        )
        timeout_result = generate_category_shadow_suggestion(
            self.store,
            MockLocalModelProvider(error_code="LOCAL_AI_TIMEOUT"),
            self.credit,
            mode="LIVE_SHADOW",
        )

        self.assertEqual(invalid_result["error_code"], "LOCAL_AI_SEMANTIC_VALIDATION_FAILED")
        self.assertEqual(timeout_result["error_code"], "LOCAL_AI_TIMEOUT")
        self.assertEqual(len(self.store.events("CategorySuggestionGenerated")), 0)
        self.assertEqual(len(self.store.events("CategorySuggestionValidationFailed")), 2)

    def test_shadow_prediction_has_no_finance_or_rule_side_effect(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        before_classification = active_classifications(self.store)[self.debit]
        before_breakdown = category_breakdown(self.store, "2026-07")
        before_cashflow = reconciled_monthly_cashflow(self.store, "2026-07")
        before_rules = len(self.store.events("ClassificationRuleCreated"))
        before_forecasts = len(self.store.events("ForecastCreated"))
        enable_category_shadow_mode(self.store)

        generate_category_shadow_suggestion(
            self.store, MockLocalModelProvider(), self.debit, mode="EVALUATION_REPLAY"
        )

        self.assertEqual(active_classifications(self.store)[self.debit], before_classification)
        self.assertEqual(category_breakdown(self.store, "2026-07"), before_breakdown)
        self.assertEqual(reconciled_monthly_cashflow(self.store, "2026-07"), before_cashflow)
        self.assertEqual(len(self.store.events("ClassificationRuleCreated")), before_rules)
        self.assertEqual(len(self.store.events("ForecastCreated")), before_forecasts)

    def test_ground_truth_evaluation_and_later_correction_are_versioned(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        enable_category_shadow_mode(self.store)
        generated = generate_category_shadow_suggestion(
            self.store, MockLocalModelProvider(), self.debit, mode="EVALUATION_REPLAY"
        )
        suggestion_id = generated["suggestion"]["suggestion_id"]
        first = category_evaluation_summary(self.store)
        self.assertEqual(first["correct_count"], 1)

        confirm_classification(self.store, self.debit, "HOUSING_UTILITIES")
        evaluate_category_suggestions(self.store, self.debit)

        second = category_evaluation_summary(self.store)
        self.assertEqual(second["correct_count"], 0)
        self.assertEqual(second["incorrect_count"], 1)
        self.assertEqual(len(self.store.events("CategorySuggestionEvaluationSuperseded")), 1)
        detail = next(
            event["payload"]
            for event in self.store.events("CategorySuggestionEvaluated")
            if event["payload"]["suggestion_id"] == suggestion_id
            and event["payload"]["status"] == "ACTIVE"
        )
        self.assertIn(detail["ground_truth_source"], {"USER_CONFIRMED", "USER_CORRECTED"})

    def test_small_samples_do_not_publish_misleading_accuracy(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        enable_category_shadow_mode(self.store)
        generate_category_shadow_suggestion(
            self.store, MockLocalModelProvider(), self.debit, mode="EVALUATION_REPLAY"
        )

        summary = category_evaluation_summary(self.store)

        self.assertEqual(summary["overall_status"], "INSUFFICIENT_DATA")
        self.assertIsNone(summary["overall_accuracy"])
        self.assertIsNone(summary["thresholds"]["0.97"]["accuracy"])
        groceries = next(
            row for row in summary["categories"] if row["category_code"] == "FOOD_GROCERIES"
        )
        self.assertIsNone(groceries["precision"])
        self.assertIsNone(groceries["recall"])

    def test_model_policy_and_taxonomy_versions_are_separate_groups(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        enable_category_shadow_mode(self.store)
        generate_category_shadow_suggestion(
            self.store,
            MockLocalModelProvider(model_version="1.0.0"),
            self.debit,
            mode="EVALUATION_REPLAY",
        )
        with (
            patch("finance_extension.category_shadow.INFERENCE_POLICY_VERSION", "1.2.0"),
            patch("finance_extension.category_shadow.TAXONOMY_VERSION", "1.1.0"),
        ):
            generate_category_shadow_suggestion(
                self.store,
                MockLocalModelProvider(
                    model_version="1.1.0", policy_version="1.2.0"
                ),
                self.debit,
                mode="EVALUATION_REPLAY",
            )

        first = category_evaluation_summary(
            self.store,
            {
                "model_version": "1.0.0",
                "policy_version": "1.1.0",
                "taxonomy_version": "1.0.0",
            },
        )
        second = category_evaluation_summary(
            self.store,
            {
                "model_version": "1.1.0",
                "policy_version": "1.2.0",
                "taxonomy_version": "1.1.0",
            },
        )
        self.assertEqual(first["prediction_count"], 1)
        self.assertEqual(first["evaluated_count"], 1)
        self.assertEqual(second["prediction_count"], 1)
        self.assertEqual(second["evaluated_count"], 1)

    def test_replay_is_restart_safe_and_does_not_auto_resume(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        enable_category_shadow_mode(self.store)
        model = MockLocalModelProvider()
        started = start_category_evaluation_run(
            self.store, model, process=False
        )
        run_id = started["run_id"]
        self.assertEqual(started["status"], "RUNNING")
        self.assertEqual(len(self.store.events("CategorySuggestionGenerated")), 0)

        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        interrupted = get_category_evaluation_run(self.store, run_id)
        self.assertEqual(interrupted["status"], "INTERRUPTED")
        self.assertEqual(len(self.store.events("CategorySuggestionGenerated")), 0)

        completed = resume_category_evaluation_run(self.store, model, run_id)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["succeeded"], 1)

    def test_shadow_toggle_retains_existing_evaluations(self) -> None:
        first = enable_category_shadow_mode(self.store)
        self.assertTrue(first["enabled"])
        self.assertEqual(category_shadow_status(self.store)["predictions"], 0)

    def test_backup_restore_preserves_shadow_history_and_aggregations(self) -> None:
        confirm_classification(self.store, self.debit, "FOOD_GROCERIES")
        enable_category_shadow_mode(self.store)
        run = start_category_evaluation_run(self.store, MockLocalModelProvider())
        before_status = category_shadow_status(self.store)
        before_summary = category_evaluation_summary(self.store)
        backup_key = StaticKeyProvider(Fernet.generate_key())
        backup = create_backup(self.store, backup_key, self.root / "backups")

        confirm_classification(self.store, self.debit, "HOUSING_UTILITIES")
        evaluate_category_suggestions(self.store, self.debit)
        restore_backup(self.store, backup["path"], backup_key)

        self.assertEqual(category_shadow_status(self.store), before_status)
        self.assertEqual(category_evaluation_summary(self.store), before_summary)
        restored_run = get_category_evaluation_run(self.store, run["run_id"])
        self.assertEqual(restored_run["status"], "COMPLETED")
        generated = self.store.events("CategorySuggestionGenerated")[0]["payload"]
        self.assertEqual(generated["model_version"], "1.0.0")
        self.assertEqual(generated["inference_policy_version"], "1.1.0")
        self.assertEqual(generated["taxonomy_version"], "1.0.0")

    def test_public_application_commands_and_queries_expose_shadow_evaluation(self) -> None:
        model = MockLocalModelProvider()
        application = FinanceApplicationService(
            self.store, local_model_service=model  # type: ignore[arg-type]
        )
        application.command("EnableCategoryShadowMode", {})
        application.command(
            "ConfirmClassification",
            {"transaction_id": self.debit, "category_code": "FOOD_GROCERIES"},
        )

        run = application.command("StartCategoryEvaluationRun", {})["result"]
        status = application.query("GetCategoryShadowStatus")["data"]
        summary = application.query("GetCategoryEvaluationSummary")["data"]

        self.assertEqual(run["status"], "COMPLETED")
        self.assertTrue(status["enabled"])
        self.assertEqual(summary["prediction_count"], 1)
        self.assertEqual(summary["evaluated_count"], 1)


if __name__ == "__main__":
    unittest.main()
