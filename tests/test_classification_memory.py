from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.classification import (
    active_classifications,
    classify_transactions,
    confirm_classification,
    create_rule,
)
from finance_extension.classification_memory import (
    active_memory_entries,
    classification_memory_summary,
    normalize_merchant,
    retrieve_classification_context,
)
from finance_extension.category_shadow import (
    category_evaluation_summary,
    generate_visible_category_suggestions,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-42.80,EUR,REWE MARKT 1234,Kartenzahlung Einkauf
2026-07-02,2026-07-02,-18.20,EUR,REWE CITY,Einkauf
2026-07-03,2026-07-03,-12.10,EUR,REWE SAGT DANKE,Einkauf
2026-07-04,2026-07-04,-42.80,EUR,REWE FILIALE 88,Einkauf
"""


class ClassificationMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "memory.csv"
        source.write_text(CSV, encoding="utf-8")
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.data = root / "data"
        self.store = LocalFinanceStore(self.data, self.key).open()
        normalize_batch(self.store, import_csv(self.store, source, "acc_memory"))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _transactions(self) -> list[str]:
        return [
            event["payload"]["transaction_id"]
            for event in self.store.events("TransactionNormalized")
        ]

    def test_merchant_variants_share_a_conservative_identity(self) -> None:
        self.assertEqual(normalize_merchant("REWE MARKT 1234"), "REWE")
        self.assertEqual(normalize_merchant("REWE CITY"), "REWE")
        self.assertEqual(normalize_merchant("REWE SAGT DANKE"), "REWE")

    def test_confirmed_decision_produces_a_memory_proposal_for_similar_transaction(self) -> None:
        first, second, *_ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")

        classify_transactions(self.store)

        proposal = active_classifications(self.store)[second]
        self.assertEqual(proposal["payload"]["category_code"], "FOOD_GROCERIES")
        self.assertEqual(
            proposal["payload"]["classification_source"],
            "LOCAL_CLASSIFICATION_MEMORY",
        )
        self.assertEqual(proposal["payload"]["memory_match"]["support_count"], 1)
        self.assertEqual(len(self.store.events("ClassificationMemoryMatched")), 3)
        comparison = category_evaluation_summary(self.store)["comparison"]
        self.assertEqual(comparison["memory_hit_rate"], 1.0)
        self.assertEqual(comparison["memory_conflict_rate"], 0.0)

    def test_explicit_user_rule_always_wins_over_memory(self) -> None:
        first, second, *_ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")
        create_rule(
            self.store,
            field="counterparty",
            operator="EQUALS",
            value="REWE CITY",
            category_code="OTHER_EXPENSE",
            priority=1,
        )

        classify_transactions(self.store)

        proposal = active_classifications(self.store)[second]
        self.assertEqual(proposal["payload"]["category_code"], "OTHER_EXPENSE")
        self.assertEqual(proposal["payload"]["classification_source"], "USER_RULE")
        self.assertNotIn("memory_match", proposal["payload"])

    def test_conflicting_memory_is_ambiguous_and_not_applied(self) -> None:
        first, second, third, _ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")
        confirm_classification(self.store, second, "OTHER_EXPENSE")

        classify_transactions(self.store)

        proposal = active_classifications(self.store)[third]
        self.assertEqual(proposal["payload"]["status"], "CONFLICT")
        self.assertEqual(proposal["payload"]["category_code"], "UNCLASSIFIED")
        self.assertEqual(proposal["payload"]["memory_match"]["status"], "AMBIGUOUS")
        self.assertFalse(
            any(
                event["payload"]["transaction_id"] == third
                for event in self.store.events("ClassificationMemoryMatched")
            )
        )
        self.assertEqual(
            category_evaluation_summary(self.store)["comparison"][
                "memory_conflict_rate"
            ],
            1.0,
        )

    def test_user_correction_supersedes_old_source_memory_and_survives_restart(self) -> None:
        first, *_ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")
        confirm_classification(self.store, first, "OTHER_EXPENSE")

        active = list(active_memory_entries(self.store).values())
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["category_code"], "OTHER_EXPENSE")
        self.assertEqual(active[0]["confirmation_type"], "USER_CORRECTED")
        self.assertEqual(len(self.store.events("ClassificationMemorySuperseded")), 1)

        before = classification_memory_summary(self.store)
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        self.assertEqual(classification_memory_summary(self.store), before)

    def test_memory_match_prevents_an_unnecessary_llm_request(self) -> None:
        class FailingIfCalledProvider:
            def status(self) -> dict[str, object]:
                raise AssertionError("LLM must not be consulted after a memory match")

        first, *_ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")
        classify_transactions(self.store)

        result = generate_visible_category_suggestions(
            self.store, FailingIfCalledProvider()  # type: ignore[arg-type]
        )

        self.assertEqual(result, {"generated": 0, "failed": 0, "skipped": 4})

    def test_retrieval_contains_only_minimized_confirmed_examples(self) -> None:
        first, _, third, _ = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")

        context = retrieve_classification_context(self.store, third)

        self.assertEqual(context["retrieved_count"], 1)
        self.assertEqual(
            context["examples"],
            [
                {
                    "merchant": "REWE",
                    "description_pattern": "EINKAUF",
                    "category_code": "FOOD_GROCERIES",
                }
            ],
        )
        self.assertEqual(
            set(context),
            {
                "examples",
                "retrieval_policy_version",
                "memory_snapshot_version",
                "retrieved_count",
            },
        )

    def test_retrieval_excludes_self_and_identical_duplicates(self) -> None:
        first, _, _, duplicate = self._transactions()
        confirm_classification(self.store, first, "FOOD_GROCERIES")

        self.assertEqual(
            retrieve_classification_context(self.store, first)["examples"], []
        )
        self.assertEqual(
            retrieve_classification_context(self.store, duplicate)["examples"], []
        )


if __name__ == "__main__":
    unittest.main()
