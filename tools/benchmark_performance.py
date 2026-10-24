"""Reproducible local performance baseline using synthetic finance data only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import tempfile
from time import monotonic
import sys

from cryptography.fernet import Fernet

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from finance_extension.application import FinanceApplicationService  # noqa: E402
from finance_extension.accounts import create_account  # noqa: E402
from finance_extension.crypto import StaticKeyProvider  # noqa: E402
from finance_extension.importer import import_csv, normalize_batch  # noqa: E402
from finance_extension.store import LocalFinanceStore  # noqa: E402


SIZES = (100, 1_000, 10_000)
OPERATIONS = (
    ("GetDashboard", {"mode": "MONTH", "year": 2026, "month": 10}),
    ("ListAccountOverviews", {"mode": "MONTH", "year": 2026, "month": 10}),
    (
        "GetAccountDetail",
        {"account_id": "acc_benchmark", "mode": "MONTH", "year": 2026, "month": 10},
    ),
    ("ListTransactions", {"month": "2026-10", "limit": 50}),
    ("ListPendingCategorySuggestions", {"limit": 50}),
    ("GetCategorySuggestionReviewSummary", {}),
    ("GetReviewInbox", {}),
    ("GetImportHistory", {}),
)


def _csv(rows: int) -> str:
    header = "booking_date,value_date,amount,currency,counterparty,description\n"
    records = []
    for index in range(rows):
        day = index % 28 + 1
        amount = "2500.00" if index % 31 == 0 else f"-{(index % 97) + 1}.00"
        records.append(
            f"2026-10-{day:02d},2026-10-{day:02d},{amount},EUR,SYNTHETIC_{index % 40},ROW_{index}"
        )
    return header + "\n".join(records) + "\n"


def benchmark(size: int, runs: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "synthetic.csv"
        source.write_text(_csv(size), encoding="utf-8")
        store = LocalFinanceStore(
            root / "data", StaticKeyProvider(Fernet.generate_key())
        ).open()
        try:
            for index in range(10):
                create_account(
                    store,
                    account_id="acc_benchmark" if index == 0 else f"acc_benchmark_{index}",
                    display_name=f"Synthetic account {index + 1}",
                    account_type="CHECKING",
                    institution="Synthetic Bank",
                    currency="EUR",
                    opened_at="2026-01-01",
                )
            batch = import_csv(store, source, "acc_benchmark")
            normalize_batch(store, batch)
            application = FinanceApplicationService(store)
            results: dict[str, object] = {}
            for operation, payload in OPERATIONS:
                application.query(operation, payload)  # warm-up
                durations = []
                for _ in range(runs):
                    started = monotonic()
                    application.query(operation, payload)
                    durations.append((monotonic() - started) * 1000)
                samples = application.query("GetQueryPerformanceSummary")["data"]["samples"]
                sample = next(item for item in reversed(samples) if item["operation"] == operation)
                results[operation] = {
                    "median_ms": round(median(durations), 2),
                    "events_loaded": sample["events_loaded"],
                    "events_decoded": sample["events_decoded"],
                    "store_queries": sample["store_queries"],
                    "cache_hits": sample["request_cache_hits"],
                    "cache_misses": sample["request_cache_misses"],
                    "response_bytes": sample["response_bytes"],
                    "items_returned": sample["result_count"],
                }
            return {"transactions": size, "runs": runs, "operations": results}
        finally:
            store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.runs < 1 or any(size < 1 for size in arguments.sizes):
        parser.error("sizes and runs must be positive")
    report = {
        "performance_instrumentation_schema": "1.0.0",
        "datasets": [benchmark(size, arguments.runs) for size in arguments.sizes],
    }
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
