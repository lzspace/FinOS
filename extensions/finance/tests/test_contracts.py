from __future__ import annotations

import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from check_contracts import check_contracts
from finance_repo_guard import inspect_file


class ContractTests(unittest.TestCase):
    def test_schema_catalogs_and_references_are_consistent(self) -> None:
        self.assertEqual(check_contracts(), [])

    def test_repository_guard_rejects_unmarked_csv(self) -> None:
        reasons = inspect_file(Path("private/account.csv"), b"date,amount\n2026-01-01,10.00\n")
        self.assertTrue(any("blocked" in reason for reason in reasons))

    def test_repository_guard_accepts_marked_synthetic_csv(self) -> None:
        path = Path("extensions/finance/tests/fixtures/synthetic/example.csv")
        content = b"SYNTHETIC_TEST_DATA,date,amount\ntrue,2026-01-01,10.00\n"
        self.assertEqual(inspect_file(path, content), [])

    def test_repository_guard_detects_likely_iban(self) -> None:
        synthetic_iban = b"DE89" + b"370400440532013000"
        reasons = inspect_file(Path("notes.txt"), b"account " + synthetic_iban)
        self.assertIn("likely IBAN", reasons)

    def test_repository_guard_ignores_tax_id_shape_only_in_generated_hash_manifest(self) -> None:
        content = (
            b'{"application_version":"1.1.0","schemas":{"files":'
            b'{"a":"abcdef' + b"12345" + b"678901" + b'abcdef"}},'
            b'"ui_bundle":{"files":{}}}'
        )
        self.assertEqual(
            inspect_file(Path("src/finance_extension/release_integrity.json"), content), []
        )
        self.assertIn(
            "likely German tax ID",
            inspect_file(Path("notes.txt"), b"identifier " + b"12345" + b"678901"),
        )

    def test_repository_guard_rejects_recovery_archives(self) -> None:
        for path in (Path("finance.finance-backup"), Path("finance.finance-archive")):
            reasons = inspect_file(path, b"synthetic encrypted archive")
            self.assertTrue(any("blocked" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
