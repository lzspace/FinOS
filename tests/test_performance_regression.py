from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from statistics import median
from time import monotonic

from cryptography.fernet import Fernet

from finance_extension.application import FinanceApplicationService
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.store import LocalFinanceStore


def _synthetic_csv(rows: int) -> str:
    lines = ["booking_date,value_date,amount,currency,counterparty,description"]
    for index in range(rows):
        day = index % 28 + 1
        amount = "2500.00" if index % 31 == 0 else f"-{index % 97 + 1}.00"
        lines.append(
            f"2026-10-{day:02d},2026-10-{day:02d},{amount},EUR,SHOP_{index % 40},ROW_{index}"
        )
    return "\n".join(lines) + "\n"


class PerformanceRegressionTests(unittest.TestCase):
    """Broad local budgets; intended to catch regressions, not benchmark hardware."""

    def test_large_hot_paths_stay_within_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.csv"
            source.write_text(_synthetic_csv(10_000), encoding="utf-8")
            store = LocalFinanceStore(
                root / "data", StaticKeyProvider(Fernet.generate_key())
            ).open()
            try:
                batch = import_csv(store, source, "acc_performance")
                normalize_batch(store, batch)
                application = FinanceApplicationService(store)
                cases = (
                    (
                        "GetDashboard",
                        {"mode": "MONTH", "year": 2026, "month": 10},
                        100.0,
                    ),
                    (
                        "ListAccountOverviews",
                        {"mode": "MONTH", "year": 2026, "month": 10},
                        100.0,
                    ),
                    ("ListTransactions", {"month": "2026-10", "limit": 50}, 15.0),
                    ("GetReviewInbox", {"limit": 50}, 75.0),
                )
                for operation, payload, budget_ms in cases:
                    application.query(operation, payload)
                    durations = []
                    for _ in range(3):
                        started = monotonic()
                        response = application.query(operation, payload)
                        durations.append((monotonic() - started) * 1000)
                    self.assertLess(
                        median(durations),
                        budget_ms,
                        f"{operation} exceeded its {budget_ms:.0f} ms median budget",
                    )
                    if operation == "ListTransactions":
                        self.assertEqual(len(response["data"]["transactions"]), 50)
                        samples = application.query("GetQueryPerformanceSummary")["data"][
                            "samples"
                        ]
                        sample = next(
                            row for row in reversed(samples) if row["operation"] == operation
                        )
                        self.assertLessEqual(sample["events_decoded"], 150)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
