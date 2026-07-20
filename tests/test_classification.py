from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.cashflow import monthly_cashflow
from finance_extension.classification import (
    active_classifications,
    category_breakdown,
    classification_review,
    classify_transactions,
    confirm_classification,
    create_rule,
    reject_classification,
)
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-42.80,EUR,Supermarkt Beispiel,Lebensmittel
2026-07-02,2026-07-02,3200.00,EUR,Arbeitgeber Beispiel,Gehalt
2026-07-03,2026-07-03,-10.00,EUR,Unbekannt Beispiel,Einmalig
"""


class ClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "synthetic.csv"
        source.write_text(CSV)
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.data = root / "data"
        self.store = LocalFinanceStore(self.data, self.key).open()
        batch = import_csv(self.store, source, "acc_01")
        normalize_batch(self.store, batch)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _transactions(self) -> list[str]:
        return [
            event["payload"]["transaction_id"]
            for event in self.store.events("TransactionNormalized")
        ]

    def test_classify_is_idempotent_and_keeps_unmatched_visible(self) -> None:
        self.assertEqual(classify_transactions(self.store, "2026-07"), 5)
        self.assertEqual(classify_transactions(self.store, "2026-07"), 0)
        review = classification_review(self.store)
        self.assertEqual(len(review), 3)
        self.assertEqual(review[2]["classification"]["payload"]["status"], "UNCLASSIFIED")
        self.assertEqual(review[2]["classification"]["payload"]["category_code"], "UNCLASSIFIED")

    def test_confirmed_user_decision_is_never_overwritten(self) -> None:
        classify_transactions(self.store)
        transaction = self._transactions()[0]
        confirm_classification(self.store, transaction, "OTHER_EXPENSE")
        create_rule(
            self.store,
            field="counterparty",
            operator="CONTAINS",
            value="Supermarkt",
            category_code="LEISURE",
            priority=500,
        )
        classify_transactions(self.store)
        current = active_classifications(self.store)[transaction]
        self.assertEqual(current["event_type"], "TransactionClassificationConfirmed")
        self.assertEqual(current["payload"]["category_code"], "OTHER_EXPENSE")

    def test_priority_conflict_is_explicit(self) -> None:
        create_rule(
            self.store,
            field="counterparty",
            operator="CONTAINS",
            value="Supermarkt",
            category_code="LEISURE",
            priority=500,
            rule_id="rule_conflict_a",
        )
        create_rule(
            self.store,
            field="counterparty",
            operator="CONTAINS",
            value="Supermarkt",
            category_code="HEALTH",
            priority=500,
            rule_id="rule_conflict_b",
        )
        classify_transactions(self.store)
        current = active_classifications(self.store)[self._transactions()[0]]
        self.assertEqual(current["payload"]["status"], "CONFLICT")
        self.assertEqual(current["payload"]["category_code"], "UNCLASSIFIED")
        self.assertEqual(
            current["payload"]["matching_rule_ids"], ["rule_conflict_a", "rule_conflict_b"]
        )

    def test_reject_and_confirm_with_future_rule(self) -> None:
        classify_transactions(self.store)
        first, _, unmatched = self._transactions()
        reject_classification(self.store, first)
        self.assertEqual(
            active_classifications(self.store)[first]["event_type"],
            "TransactionClassificationRejected",
        )
        confirm_classification(
            self.store,
            unmatched,
            "FEES",
            create_rule_from="counterparty",
        )
        self.assertEqual(len(self.store.events("ClassificationRuleCreated")), 1)

    def test_category_projection_rebuild_preserves_cashflow(self) -> None:
        classify_transactions(self.store)
        for transaction_id, category in zip(
            self._transactions(),
            ("FOOD_GROCERIES", "INCOME_SALARY", "FEES"),
            strict=True,
        ):
            confirm_classification(self.store, transaction_id, category)
        original = monthly_cashflow(self.store, "2026-07")
        first = category_breakdown(self.store, "2026-07")
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.key).open()
        rebuilt = category_breakdown(self.store, "2026-07")
        self.assertEqual(first, rebuilt)
        self.assertEqual(sum(first["categories"].values()), original["net_cashflow"])
        self.assertEqual(
            original["net_cashflow"], monthly_cashflow(self.store, "2026-07")["net_cashflow"]
        )
