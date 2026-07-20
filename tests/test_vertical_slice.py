from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.cashflow import monthly_cashflow
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-42.80,EUR,Supermarkt Beispiel,Lebensmittel
2026-07-02,2026-07-02,3200.00,EUR,Arbeitgeber Beispiel,Gehalt
"""


class VerticalSliceTests(unittest.TestCase):
    def test_import_normalize_persist_and_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            source = root / "synthetic.csv"
            source.write_text(CSV)
            key = StaticKeyProvider(Fernet.generate_key())
            with LocalFinanceStore(data, key, repository_roots=[root / "repository"]) as store:
                batch = import_csv(store, source, "acc_01")
                self.assertEqual(normalize_batch(store, batch), 2)
                # Same content does not append events again.
                self.assertEqual(import_csv(store, source, "acc_01"), batch)
                flow = monthly_cashflow(store, "2026-07")
                self.assertEqual(str(flow["net_cashflow"]), "3157.20")
                self.assertEqual(flow["transaction_count"], 2)
                self.assertEqual(len(store.events("RawTransactionImported")), 2)
            self.assertFalse((data / "finance.sqlite").exists())
            self.assertTrue((data / "finance.sqlite.fernet").read_bytes())
            with LocalFinanceStore(data, key) as reopened:
                self.assertEqual(len(reopened.events("TransactionNormalized")), 2)
