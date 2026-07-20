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


if __name__ == "__main__":
    unittest.main()
