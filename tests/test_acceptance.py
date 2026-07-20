from __future__ import annotations

import unittest

from finance_extension.acceptance import run_acceptance


class AcceptanceScenarioTests(unittest.TestCase):
    def test_complete_synthetic_scenario_rebuilds_restores_and_rotates(self) -> None:
        result = run_acceptance()
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["source"], "SOURCE_TREE")
        self.assertTrue(result["offline"])
        self.assertTrue(result["restart_rebuild_equal"])
        self.assertTrue(result["restore_equal"])
        self.assertEqual(result["key_rotation"], "ROTATED")


if __name__ == "__main__":
    unittest.main()
