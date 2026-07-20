from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.cli import _parser
from finance_extension.crypto import MutableStaticKeyProvider, StaticKeyProvider, cipher_for
from finance_extension.importer import import_csv, normalize_batch
from finance_extension.recovery import (
    ARCHIVE_MAGIC,
    RecoveryInvariantError,
    create_backup,
    delete_backup,
    export_finance_data,
    import_finance_archive,
    list_backups,
    migration_status,
    repair_local_store,
    restore_backup,
    rotate_encryption_key,
    validate_store_integrity,
    verify_archive,
)
from finance_extension.store import LocalFinanceStore, StoreInvariantError


HEADER = "booking_date,value_date,amount,currency,counterparty,description\n"


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.backups = self.root / "backups"
        self.exports = self.root / "exports"
        self.store_key = MutableStaticKeyProvider(Fernet.generate_key())
        self.backup_key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(
            self.data,
            self.store_key,
            known_network_roots=(self.root / "network-volume",),
        ).open()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _import(self, name: str, amount: str) -> str:
        source = self.root / name
        source.write_text(
            HEADER + f"2026-07-01,2026-07-01,{amount},EUR,Beispiel,{name}\n"
        )
        batch = import_csv(self.store, source, "acc_01")
        normalize_batch(self.store, batch)
        return batch

    def _rewrite_manifest(self, archive_path: Path, **changes: object) -> Path:
        encrypted = archive_path.read_bytes()[len(ARCHIVE_MAGIC) :]
        plaintext = Fernet(self.backup_key.get_key()).decrypt(encrypted)
        with zipfile.ZipFile(io.BytesIO(plaintext), "r") as source:
            files = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(files["manifest.json"])
        manifest.update(changes)
        files["manifest.json"] = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, content in files.items():
                target.writestr(name, content)
        rewritten = self.root / f"rewritten-{archive_path.name}"
        rewritten.write_bytes(
            ARCHIVE_MAGIC + Fernet(self.backup_key.get_key()).encrypt(payload.getvalue())
        )
        return rewritten

    def test_backup_is_verified_and_restore_replaces_the_complete_store(self) -> None:
        first_batch = self._import("first.csv", "100.00")
        backup = create_backup(self.store, self.backup_key, self.backups)
        sequence = backup["event_store_sequence"]
        self._import("second.csv", "200.00")
        self.assertGreater(len(self.store.events()), sequence)

        result = restore_backup(self.store, backup["path"], self.backup_key)

        self.assertTrue(result["restored"])
        self.assertEqual(len(self.store.events()), sequence)
        self.assertEqual(
            {
                event["payload"]["import_batch_id"]
                for event in self.store.events("ImportBatchStarted")
            },
            {first_batch},
        )
        self.assertEqual(validate_store_integrity(self.store)["status"], "VALID")
        self.store.close()
        self.store = LocalFinanceStore(self.data, self.store_key).open()
        self.assertEqual(len(self.store.events()), sequence)

    def test_manipulated_archive_is_rejected_without_touching_the_store(self) -> None:
        self._import("original.csv", "100.00")
        backup = create_backup(self.store, self.backup_key, self.backups)
        before = len(self.store.events())
        path = Path(backup["path"])
        content = bytearray(path.read_bytes())
        content[-8] ^= 0x01
        manipulated = self.root / "manipulated.finance-backup"
        manipulated.write_bytes(content)

        with self.assertRaisesRegex(
            RecoveryInvariantError, "FINANCE_ARCHIVE_AUTHENTICATION_FAILED"
        ):
            restore_backup(self.store, manipulated, self.backup_key)
        self.assertEqual(len(self.store.events()), before)

    def test_future_archive_is_blocked_as_downgrade_protection(self) -> None:
        self._import("future.csv", "100.00")
        backup = create_backup(self.store, self.backup_key, self.backups)
        future = self._rewrite_manifest(
            Path(backup["path"]),
            application_version="9.0.0",
            minimum_application_version="9.0.0",
        )
        with self.assertRaisesRegex(
            RecoveryInvariantError, "FINANCE_ARCHIVE_DOWNGRADE_BLOCKED"
        ):
            verify_archive(future, self.backup_key)

    def test_export_and_import_round_trip_is_complete(self) -> None:
        self._import("exported.csv", "100.00")
        exported = export_finance_data(self.store, self.backup_key, self.exports)
        sequence = len(self.store.events())
        self._import("later.csv", "50.00")

        result = import_finance_archive(self.store, exported["path"], self.backup_key)

        self.assertTrue(result["imported"])
        self.assertEqual(len(self.store.events()), sequence)

    def test_key_rotation_reencrypts_store_and_imports_after_recovery_backup(self) -> None:
        self._import("rotation.csv", "100.00")
        old_key = self.store_key.get_key()

        result = rotate_encryption_key(self.store, self.backup_key, self.backups)

        self.assertEqual(result["status"], "ROTATED")
        self.assertNotEqual(self.store_key.get_key(), old_key)
        self.assertEqual(validate_store_integrity(self.store)["status"], "VALID")
        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_STORE_DECRYPTION_FAILED"):
            LocalFinanceStore(self.data, StaticKeyProvider(old_key)).open()
        encrypted_import = next((self.data / "imports").iterdir()).read_bytes()
        self.assertIn(b"rotation.csv", cipher_for(self.store_key).decrypt(encrypted_import))
        self.assertEqual(len(list_backups(self.store, self.backup_key, self.backups)), 1)

    def test_list_delete_integrity_repair_and_migration_status(self) -> None:
        self._import("catalog.csv", "100.00")
        backup = create_backup(self.store, self.backup_key, self.backups)
        rows = list_backups(self.store, self.backup_key, self.backups)
        self.assertEqual(rows[0]["verification_status"], "VALID")
        self.assertEqual(migration_status(self.store)["current_store_schema_version"], 2)
        self.assertEqual(repair_local_store(self.store)["status"], "REPAIRED")
        self.assertTrue(
            delete_backup(
                self.store, self.backup_key, backup["archive_id"], self.backups
            )["deleted"]
        )
        self.assertEqual(list_backups(self.store, self.backup_key, self.backups), [])

    def test_archive_key_must_be_independent_and_paths_must_be_local(self) -> None:
        with self.assertRaisesRegex(
            RecoveryInvariantError, "FINANCE_ARCHIVE_KEY_NOT_INDEPENDENT"
        ):
            create_backup(
                self.store,
                StaticKeyProvider(self.store_key.get_key()),
                self.backups,
            )
        with self.assertRaisesRegex(ValueError, "FINANCE_CLOUD_SYNC_STORAGE_BLOCKED"):
            create_backup(self.store, self.backup_key, self.root / "iCloud Drive" / "backup")
        with self.assertRaisesRegex(
            RecoveryInvariantError, "FINANCE_ARCHIVE_DIRECTORY_INSIDE_STORE"
        ):
            create_backup(self.store, self.backup_key, self.data / "backups")
        with self.assertRaisesRegex(ValueError, "FINANCE_NETWORK_STORAGE_BLOCKED"):
            create_backup(self.store, self.backup_key, self.root / "network-volume" / "backup")

    def test_newer_store_schema_cannot_be_opened_by_older_runtime(self) -> None:
        other_data = self.root / "future-store"
        provider = StaticKeyProvider(Fernet.generate_key())
        future = LocalFinanceStore(other_data, provider).open()
        future.connection.execute(
            "UPDATE store_metadata SET metadata_value='999' WHERE metadata_key='schema_version'"
        )
        future.connection.commit()
        future.close()

        with self.assertRaisesRegex(StoreInvariantError, "FINANCE_STORE_DOWNGRADE_BLOCKED"):
            LocalFinanceStore(other_data, provider).open()

    def test_pre_080_store_is_migrated_once_and_recorded(self) -> None:
        legacy_data = self.root / "legacy-store"
        provider = StaticKeyProvider(Fernet.generate_key())
        legacy = LocalFinanceStore(legacy_data, provider).open()
        legacy.connection.executescript("DROP TABLE migration_log; DROP TABLE store_metadata;")
        legacy.connection.commit()
        legacy.close()

        migrated = LocalFinanceStore(legacy_data, provider).open()
        try:
            self.assertEqual(migrated.schema_version(), 2)
            self.assertEqual(
                [row["migration_id"] for row in migrated.migration_history()],
                ["store_v1_to_v2"],
            )
        finally:
            migrated.close()

    def test_cli_exposes_backup_export_integrity_and_key_rotation(self) -> None:
        parser = _parser()
        self.assertEqual(
            parser.parse_args(["--data-dir", str(self.data), "backup", "create"]).backup_action,
            "create",
        )
        self.assertEqual(
            parser.parse_args(["--data-dir", str(self.data), "data", "export"]).data_action,
            "export",
        )
        self.assertEqual(
            parser.parse_args(["--data-dir", str(self.data), "store", "validate"]).store_action,
            "validate",
        )
        self.assertEqual(
            parser.parse_args(["--data-dir", str(self.data), "key", "rotate"]).key_action,
            "rotate",
        )


if __name__ == "__main__":
    unittest.main()
