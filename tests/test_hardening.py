from __future__ import annotations

import json
import os
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.crypto import StaticKeyProvider
from finance_extension.cashflow import monthly_cashflow
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.recovery import ARCHIVE_MAGIC, RecoveryInvariantError, verify_archive
from finance_extension.storage_policy import StoragePolicyViolation, validate_runtime_path
from finance_extension.store import LocalFinanceStore
from finance_extension.workspace_lock import (
    WorkspaceLockError,
    inspect_workspace_lock,
    lock_path,
    recover_stale_workspace_lock,
)


class WorkspaceHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "finance-data"
        self.key = StaticKeyProvider(Fernet.generate_key())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_one_writer_can_open_a_workspace(self) -> None:
        first = LocalFinanceStore(self.data, self.key).open()
        try:
            status = inspect_workspace_lock(self.data)
            self.assertEqual(status["status"], "LOCKED")
            self.assertEqual(status["pid"], os.getpid())
            with self.assertRaisesRegex(WorkspaceLockError, "FINANCE_WORKSPACE_LOCKED"):
                LocalFinanceStore(self.data, self.key).open()
        finally:
            first.close()
        self.assertEqual(inspect_workspace_lock(self.data)["status"], "UNLOCKED")

    def test_stale_lock_requires_explicit_matching_recovery(self) -> None:
        path = lock_path(self.data)
        path.write_text(
            json.dumps(
                {
                    "pid": 999_999_999,
                    "started_at": "2026-07-20T00:00:00+00:00",
                    "instance_id": "stale-instance",
                }
            )
        )
        self.assertEqual(inspect_workspace_lock(self.data)["status"], "STALE")
        with self.assertRaisesRegex(
            WorkspaceLockError, "FINANCE_WORKSPACE_LOCK_OWNER_MISMATCH"
        ):
            recover_stale_workspace_lock(self.data, expected_instance_id="wrong")
        self.assertTrue(
            recover_stale_workspace_lock(
                self.data, expected_instance_id="stale-instance"
            )["recovered"]
        )

    def test_runtime_path_rejects_symlink_components(self) -> None:
        real = self.root / "real"
        real.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(
            StoragePolicyViolation, "FINANCE_SYMLINK_PATH_BLOCKED"
        ):
            validate_runtime_path(linked / "finance")

    def test_archive_bomb_is_rejected_before_members_are_extracted(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"0" * 1_000_000)
            archive.writestr("store.sqlite", b"sqlite")
        archive_path = self.root / "bomb.finance-backup"
        archive_path.write_bytes(
            ARCHIVE_MAGIC + Fernet(self.key.get_key()).encrypt(payload.getvalue())
        )
        with self.assertRaisesRegex(
            RecoveryInvariantError, "FINANCE_ARCHIVE_BOMB_DETECTED"
        ):
            verify_archive(archive_path, self.key)

    def test_failed_migration_does_not_replace_the_encrypted_store(self) -> None:
        legacy = LocalFinanceStore(self.data, self.key).open()
        legacy.connection.execute(
            "UPDATE store_metadata SET metadata_value='2' WHERE metadata_key='schema_version'"
        )
        legacy.connection.commit()
        legacy.close()
        encrypted = self.data / LocalFinanceStore.DB_FILE
        before = encrypted.read_bytes()
        original = LocalFinanceStore._apply_migration

        def fail_migration(store: LocalFinanceStore, current: int, target: int) -> None:
            raise RuntimeError("injected migration failure")

        LocalFinanceStore._apply_migration = fail_migration
        try:
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                LocalFinanceStore(self.data, self.key).open()
        finally:
            LocalFinanceStore._apply_migration = original
        self.assertEqual(encrypted.read_bytes(), before)
        reopened = LocalFinanceStore(self.data, self.key).open()
        try:
            self.assertEqual(reopened.schema_version(), 3)
        finally:
            reopened.close()

    def test_migration_matrix_preserves_events_and_rebuilds_projection(self) -> None:
        source_versions = {
            "0.2.0": 1,
            "0.3.0": 1,
            "0.4.0": 1,
            "0.5.0": 1,
            "0.6.0": 1,
            "0.7.0": 1,
            "0.8.0": 2,
        }
        for application_version, schema_version in source_versions.items():
            with self.subTest(source=application_version):
                data = self.root / f"data-{application_version}"
                source = self.root / f"source-{application_version}.csv"
                source.write_text(
                    "booking_date,value_date,amount,currency,counterparty,description\n"
                    "2026-07-01,2026-07-01,100.00,EUR,Synthetic,Salary\n"
                )
                store = LocalFinanceStore(data, self.key).open()
                batch = import_csv(store, source, "acc_01")
                normalize_batch(store, batch)
                before = [
                    (event["event_id"], event["payload_hash"]) for event in store.events()
                ]
                projection = monthly_cashflow(store, "2026-07")
                store.connection.execute(
                    "UPDATE store_metadata SET metadata_value=? WHERE metadata_key='schema_version'",
                    (str(schema_version),),
                )
                store.connection.execute("DELETE FROM migration_log")
                store.connection.commit()
                store.close()

                migrated = LocalFinanceStore(data, self.key).open()
                try:
                    self.assertEqual(migrated.schema_version(), 3)
                    self.assertEqual(
                        [(event["event_id"], event["payload_hash"]) for event in migrated.events()],
                        before,
                    )
                    self.assertEqual(monthly_cashflow(migrated, "2026-07"), projection)
                    self.assertEqual(
                        migrated.migration_history()[0]["from_version"], schema_version
                    )
                finally:
                    migrated.close()


if __name__ == "__main__":
    unittest.main()
