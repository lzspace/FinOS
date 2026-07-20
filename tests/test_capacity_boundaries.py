from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.recovery import create_backup, verify_archive
from finance_extension.store import LocalFinanceStore


class CapacityBoundaryTests(unittest.TestCase):
    def test_ten_thousand_csv_rows_and_half_megabyte_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic-capacity.csv"
            rows = [
                "2026-07-01,2026-07-01,-1.00,EUR,Synthetic Capacity,"
                + hashlib.sha256(str(index).encode()).hexdigest()
                for index in range(10_000)
            ]
            source.write_text(
                "booking_date,value_date,amount,currency,counterparty,description\n"
                + "\n".join(rows)
                + "\n",
                encoding="utf-8",
            )
            store = LocalFinanceStore(
                root / "data", StaticKeyProvider(Fernet.generate_key())
            ).open()
            try:
                batch = import_csv(store, source, "acc_capacity")
                self.assertEqual(normalize_batch(store, batch), 10_000)
                archive_key = StaticKeyProvider(Fernet.generate_key())
                backup = create_backup(store, archive_key, root / "backups")
                self.assertGreaterEqual(backup["size"], 512 * 1024)
                self.assertEqual(
                    verify_archive(backup["path"], archive_key).manifest["archive_id"],
                    backup["archive_id"],
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
