from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.application import FinanceApplicationService
from finance_extension.crypto import StaticKeyProvider
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.read_models import (
    ProjectionDefinition,
    ProjectionRegistry,
    ProjectionRunner,
)
from finance_extension.store import LocalFinanceStore


CSV = """booking_date,value_date,amount,currency,counterparty,description
2026-10-01,2026-10-01,1200.00,EUR,SYNTHETIC_EMPLOYER,Income
2026-10-02,2026-10-02,-20.00,EUR,SYNTHETIC_SHOP,Expense
"""


class PersistedReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "synthetic.csv"
        source.write_text(CSV, encoding="utf-8")
        self.store = LocalFinanceStore(
            root / "data", StaticKeyProvider(Fernet.generate_key())
        ).open()
        batch = import_csv(self.store, source, "acc_read_model")
        normalize_batch(self.store, batch)
        self.application = FinanceApplicationService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_incremental_result_equals_full_rebuild(self) -> None:
        parameters = {"mode": "MONTH", "year": 2026, "month": 10}
        incremental = self.application.query("GetDashboard", parameters)["data"]
        definition = ProjectionDefinition("dashboard_read_model", "1.0.0")
        registry = ProjectionRegistry(self.store)
        incremental_hash = registry.canonical_hash(definition)

        ProjectionRunner(registry).rebuild(
            definition,
            parameters,
            self.store.latest_sequence(),
            lambda: {
                "data": self.application._dashboard_uncached(parameters)["data"],
                "state": "READY",
                "projection_version": "1.0.0",
            },
        )
        rebuilt = self.application.query("GetDashboard", parameters)["data"]

        self.assertEqual(incremental, rebuilt)
        self.assertEqual(incremental_hash, registry.canonical_hash(definition))

    def test_checkpoint_is_atomic_and_projection_is_idempotent(self) -> None:
        registry = ProjectionRegistry(self.store)
        definition = ProjectionDefinition("test_projection", "1.0.0")
        calls = 0

        def build() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"count": 2}

        sequence = self.store.latest_sequence()
        first = registry.materialize(definition, {}, sequence, build)
        second = registry.materialize(definition, {}, sequence, build)
        checkpoint = registry.checkpoint(definition)

        self.assertEqual(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(checkpoint.last_applied_sequence, sequence)
        self.assertEqual(checkpoint.status, "CURRENT")

    def test_incremental_runner_applies_each_event_exactly_once(self) -> None:
        self.store.connection.execute(
            "CREATE TABLE test_projection_rows(sequence_number INTEGER PRIMARY KEY)"
        )
        self.store.connection.commit()
        registry = ProjectionRegistry(self.store)
        runner = ProjectionRunner(registry)
        definition = ProjectionDefinition("incremental_test", "1.0.0")
        applied: list[int] = []

        def apply_event(
            connection: sqlite3.Connection, event: dict[str, object]
        ) -> None:
            sequence = int(event["sequence_number"])
            applied.append(sequence)
            connection.execute(
                "INSERT INTO test_projection_rows VALUES (?)", (sequence,)
            )

        sequence = self.store.latest_sequence()
        first = runner.catch_up(definition, sequence, apply_event)
        second = runner.catch_up(definition, sequence, apply_event)

        self.assertEqual(applied, list(range(1, sequence + 1)))
        self.assertEqual(first.last_applied_sequence, sequence)
        self.assertEqual(second.last_applied_sequence, sequence)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM test_projection_rows"
            ).fetchone()[0],
            sequence,
        )

    def test_corrupted_projection_is_deleted_and_rebuilt(self) -> None:
        parameters = {"month": "2026-10", "limit": 50}
        expected = self.application.query("ListTransactions", parameters)["data"]
        self.store.connection.execute(
            "UPDATE projection_document SET document_json=? "
            "WHERE projection_name='transaction_read_model'",
            (b"{}",),
        )
        self.store.connection.commit()

        rebuilt = self.application.query("ListTransactions", parameters)["data"]

        self.assertEqual(rebuilt, expected)
        self.assertEqual(
            ProjectionRegistry(self.store).checkpoint(
                ProjectionDefinition("transaction_read_model", "1.0.0")
            ).status,
            "CURRENT",
        )

    def test_version_change_does_not_reuse_old_projection(self) -> None:
        registry = ProjectionRegistry(self.store)
        old = ProjectionDefinition("versioned_projection", "1.0.0")
        new = ProjectionDefinition("versioned_projection", "1.1.0")
        sequence = self.store.latest_sequence()
        registry.materialize(old, {}, sequence, lambda: {"version": 1})

        value = registry.materialize(new, {}, sequence, lambda: {"version": 2})

        self.assertEqual(value, {"version": 2})
        self.assertEqual(registry.checkpoint(new).projection_version, "1.1.0")

    def test_restart_catches_up_a_stale_projection(self) -> None:
        parameters = {"mode": "MONTH", "year": 2026, "month": 10}
        self.application.query("GetDashboard", parameters)
        definition = ProjectionDefinition("dashboard_read_model", "1.0.0")
        registry = ProjectionRegistry(self.store)
        previous_sequence = registry.checkpoint(definition).last_applied_sequence
        source = Path(self.temporary.name) / "second.csv"
        source.write_text(
            CSV.replace("SYNTHETIC_SHOP", "SYNTHETIC_SHOP_2").replace("-20.00", "-21.00"),
            encoding="utf-8",
        )
        batch = import_csv(self.store, source, "acc_read_model")
        normalize_batch(self.store, batch)
        self.assertGreater(self.store.latest_sequence(), previous_sequence)
        self.assertEqual(registry.checkpoint(definition).status, "STALE")

        key_provider = self.store.key_provider
        self.store.close()
        self.store = LocalFinanceStore(
            Path(self.temporary.name) / "data", key_provider
        ).open()
        self.application = FinanceApplicationService(self.store)
        self.application.query("GetDashboard", parameters)

        checkpoint = ProjectionRegistry(self.store).checkpoint(definition)
        self.assertEqual(checkpoint.status, "CURRENT")
        self.assertEqual(checkpoint.last_applied_sequence, self.store.latest_sequence())

    def test_application_rebuild_command_only_deletes_derived_state(self) -> None:
        parameters = {"mode": "MONTH", "year": 2026, "month": 10}
        expected = self.application.query("GetDashboard", parameters)["data"]
        event_count = self.store.latest_sequence()
        status = self.application.query("GetProjectionStatus")["data"]
        self.assertTrue(
            any(
                item["projection_name"] == "dashboard_read_model"
                and item["freshness_status"] == "CURRENT"
                for item in status["projections"]
            )
        )

        result = self.application.command("RebuildReadModels", {})

        self.assertEqual(result["result"]["status"], "REBUILDING")
        self.assertEqual(self.store.latest_sequence(), event_count)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM projection_document"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.application.query("GetDashboard", parameters)["data"], expected)


if __name__ == "__main__":
    unittest.main()
