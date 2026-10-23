from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.categories import categories, create_category, rename_category, set_category_archived
from finance_extension.crypto import StaticKeyProvider
from finance_extension.store import LocalFinanceStore, StoreInvariantError


class CategoryCatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "data"
        self.key = Fernet.generate_key()
        self.store = LocalFinanceStore(self.data_dir, StaticKeyProvider(self.key)).open()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_category_lifecycle_survives_restart(self) -> None:
        created = create_category(self.store, display_name="Haustiere", category_type="EXPENSE")
        self.assertEqual(created["category_code"], "CUSTOM_HAUSTIERE")
        rename_category(self.store, created["category_code"], "Tierbedarf")
        set_category_archived(self.store, created["category_code"], True)
        self.store.close()
        self.store = LocalFinanceStore(self.data_dir, StaticKeyProvider(self.key)).open()

        item = next(row for row in categories(self.store) if row["category_code"] == "CUSTOM_HAUSTIERE")
        self.assertEqual(item["display_name"], "Tierbedarf")
        self.assertEqual(item["status"], "ARCHIVED")
        set_category_archived(self.store, created["category_code"], False)
        self.assertEqual(next(row for row in categories(self.store) if row["category_code"] == "CUSTOM_HAUSTIERE")["status"], "ACTIVE")

    def test_builtin_categories_cannot_be_changed(self) -> None:
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_CATEGORY_SYSTEM_IMMUTABLE"):
            rename_category(self.store, "FOOD_GROCERIES", "Essen")

    def test_duplicate_generated_code_is_rejected(self) -> None:
        create_category(self.store, display_name="ÖPNV & Bahn", category_type="EXPENSE")
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_CATEGORY_ALREADY_EXISTS"):
            create_category(self.store, display_name="OPNV Bahn", category_type="EXPENSE")

if __name__ == "__main__":
    unittest.main()
