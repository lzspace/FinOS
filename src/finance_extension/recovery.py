"""Local encrypted backup, restore, export, integrity and disaster-recovery services."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from . import __version__
from .crypto import KeyProvider, StaticKeyProvider, cipher_for, key_fingerprint
from .schema_validation import validate_event
from .storage_policy import validate_runtime_path
from .store import LocalFinanceStore


ARCHIVE_MAGIC = b"AGENT_OS_FINANCE_ARCHIVE_V1\n"
ARCHIVE_FORMAT = "agent-os-finance-archive"
ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_SCHEMA_VERSION = "1.0.0"
BACKUP_SUFFIX = ".finance-backup"
EXPORT_SUFFIX = ".finance-archive"
_ARCHIVE_ID = re.compile(r"^(?:bkp|exp)_[a-f0-9]{32}$")


class RecoveryInvariantError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedArchive:
    path: Path
    manifest: dict[str, Any]
    files: dict[str, bytes]


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_VERSION_INVALID")
    return tuple(int(part) for part in match.groups())


def default_backup_directory(store: LocalFinanceStore) -> Path:
    return store.data_dir.parent / f"{store.data_dir.name}-backups"


def default_export_directory(store: LocalFinanceStore) -> Path:
    return store.data_dir.parent / f"{store.data_dir.name}-exports"


def _local_directory(path: str | Path, store: LocalFinanceStore) -> Path:
    root = validate_runtime_path(
        path,
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    try:
        root.relative_to(store.data_dir.resolve(strict=False))
    except ValueError:
        pass
    else:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_DIRECTORY_INSIDE_STORE")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _archive_key(provider: KeyProvider, *, create: bool) -> bytes:
    if create and hasattr(provider, "get_or_create_key"):
        return provider.get_or_create_key()  # type: ignore[attr-defined, no-any-return]
    try:
        return provider.get_key()
    except Exception as exc:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_KEY_UNAVAILABLE") from exc


def _import_plaintexts(store: LocalFinanceStore) -> dict[str, bytes]:
    rows = store.connection.execute(
        "SELECT stored_file_reference, content_hash FROM import_files ORDER BY stored_file_reference"
    ).fetchall()
    result: dict[str, bytes] = {}
    for row in rows:
        reference = str(row["stored_file_reference"])
        path = store.data_dir / reference
        try:
            plaintext = cipher_for(store.key_provider).decrypt(path.read_bytes())
        except Exception as exc:
            raise RecoveryInvariantError("FINANCE_IMPORT_SOURCE_DECRYPTION_FAILED") from exc
        if hashlib.sha256(plaintext).hexdigest() != row["content_hash"]:
            raise RecoveryInvariantError("FINANCE_IMPORT_SOURCE_INTEGRITY_FAILED")
        result[reference] = plaintext
    return result


def _manifest_files(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(files.items())
    ]


def _archive_bytes(
    store: LocalFinanceStore,
    *,
    archive_id: str,
    kind: str,
    archive_key_provider: KeyProvider,
) -> tuple[bytes, dict[str, Any]]:
    files = {"store.sqlite": store.serialized_snapshot(), **_import_plaintexts(store)}
    events = store.events()
    manifest = {
        "archive_format": ARCHIVE_FORMAT,
        "archive_format_version": ARCHIVE_FORMAT_VERSION,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive_id": archive_id,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "minimum_application_version": "0.8.0",
        "store_schema_version": store.schema_version(),
        "event_store_sequence": events[-1]["sequence_number"] if events else 0,
        "complete": True,
        "encryption": "FERNET_AUTHENTICATED",
        "key_strategy": "INDEPENDENT_LOCAL_ARCHIVE_KEY",
        "files": _manifest_files(files),
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )
        for name, content in files.items():
            archive.writestr(name, content)
    key = _archive_key(archive_key_provider, create=True)
    if key == store.key_provider.get_key():
        raise RecoveryInvariantError("FINANCE_ARCHIVE_KEY_NOT_INDEPENDENT")
    return ARCHIVE_MAGIC + Fernet(key).encrypt(payload.getvalue()), manifest


def create_backup(
    store: LocalFinanceStore,
    archive_key_provider: KeyProvider,
    backup_directory: str | Path | None = None,
) -> dict[str, Any]:
    integrity = validate_store_integrity(store)
    if integrity["status"] != "VALID":
        raise RecoveryInvariantError("FINANCE_STORE_INTEGRITY_REQUIRED")
    root = _local_directory(backup_directory or default_backup_directory(store), store)
    archive_id = f"bkp_{uuid4().hex}"
    content, manifest = _archive_bytes(
        store,
        archive_id=archive_id,
        kind="BACKUP",
        archive_key_provider=archive_key_provider,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / f"finance-{stamp}-{archive_id}{BACKUP_SUFFIX}"
    temporary = root / f".{destination.name}.tmp"
    temporary.write_bytes(content)
    temporary.replace(destination)
    verified = verify_archive(
        destination,
        archive_key_provider,
        expected_kind="BACKUP",
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    return {
        **verified.manifest,
        "path": str(destination),
        "size": destination.stat().st_size,
        "verification_status": "VALID",
        "created_at": manifest["created_at"],
    }


def export_finance_data(
    store: LocalFinanceStore,
    archive_key_provider: KeyProvider,
    destination_directory: str | Path | None = None,
) -> dict[str, Any]:
    root = _local_directory(destination_directory or default_export_directory(store), store)
    archive_id = f"exp_{uuid4().hex}"
    content, _ = _archive_bytes(
        store,
        archive_id=archive_id,
        kind="EXPORT",
        archive_key_provider=archive_key_provider,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / f"finance-{stamp}-{archive_id}{EXPORT_SUFFIX}"
    temporary = root / f".{destination.name}.tmp"
    temporary.write_bytes(content)
    temporary.replace(destination)
    verified = verify_archive(
        destination,
        archive_key_provider,
        expected_kind="EXPORT",
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    return {
        **verified.manifest,
        "path": str(destination),
        "size": destination.stat().st_size,
        "verification_status": "VALID",
    }


def _safe_archive_members(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if len(names) != len(set(names)) or "manifest.json" not in names or "store.sqlite" not in names:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_STRUCTURE_INVALID")
    for name in names:
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise RecoveryInvariantError("FINANCE_ARCHIVE_PATH_INVALID")
        if name not in {"manifest.json", "store.sqlite"} and not name.startswith("imports/"):
            raise RecoveryInvariantError("FINANCE_ARCHIVE_MEMBER_UNEXPECTED")
    return names


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "schema_version": row["schema_version"],
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": row["aggregate_id"],
        "aggregate_version": row["event_version"],
        "occurred_at": row["occurred_at"],
        "correlation_id": row["correlation_id"],
        "causation_id": row["causation_id"],
        "payload": json.loads(row["payload"]),
    }


def _validate_sqlite_image(database: bytes, files: dict[str, bytes]) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.deserialize(database)
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RecoveryInvariantError("FINANCE_SQLITE_INTEGRITY_FAILED")
        required = {
            "event_store",
            "command_log",
            "import_files",
            "projection_checkpoint",
            "store_metadata",
            "migration_log",
        }
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not required.issubset(tables):
            raise RecoveryInvariantError("FINANCE_STORE_TABLE_MISSING")
        version_row = connection.execute(
            "SELECT metadata_value FROM store_metadata WHERE metadata_key='schema_version'"
        ).fetchone()
        if version_row is None:
            raise RecoveryInvariantError("FINANCE_STORE_SCHEMA_VERSION_MISSING")
        store_version = int(version_row[0])
        if store_version > LocalFinanceStore.CURRENT_SCHEMA_VERSION:
            raise RecoveryInvariantError("FINANCE_STORE_DOWNGRADE_BLOCKED")
        previous_versions: dict[tuple[str, str], int] = {}
        rows = connection.execute("SELECT * FROM event_store ORDER BY sequence_number").fetchall()
        for row in rows:
            payload = bytes(row["payload"])
            if hashlib.sha256(payload).hexdigest() != row["payload_hash"]:
                raise RecoveryInvariantError("FINANCE_EVENT_INTEGRITY_FAILED")
            key = (row["aggregate_type"], row["aggregate_id"])
            expected = previous_versions.get(key, 0) + 1
            if row["event_version"] != expected:
                raise RecoveryInvariantError("FINANCE_AGGREGATE_VERSION_GAP")
            previous_versions[key] = expected
            try:
                validate_event(_event_from_row(row))
            except Exception as exc:
                raise RecoveryInvariantError("FINANCE_EVENT_SCHEMA_INVALID") from exc
        imports = connection.execute(
            "SELECT stored_file_reference, content_hash FROM import_files"
        ).fetchall()
        expected_imports = {str(row["stored_file_reference"]) for row in imports}
        actual_imports = {name for name in files if name.startswith("imports/")}
        if expected_imports != actual_imports:
            raise RecoveryInvariantError("FINANCE_ARCHIVE_IMPORT_SET_INCOMPLETE")
        for row in imports:
            content = files[str(row["stored_file_reference"])]
            if hashlib.sha256(content).hexdigest() != row["content_hash"]:
                raise RecoveryInvariantError("FINANCE_IMPORT_SOURCE_INTEGRITY_FAILED")
        return {
            "store_schema_version": store_version,
            "event_count": len(rows),
            "last_event_sequence": rows[-1]["sequence_number"] if rows else 0,
            "import_file_count": len(imports),
        }
    except sqlite3.DatabaseError as exc:
        raise RecoveryInvariantError("FINANCE_SQLITE_INTEGRITY_FAILED") from exc
    finally:
        connection.close()


def verify_archive(
    archive_path: str | Path,
    archive_key_provider: KeyProvider,
    *,
    expected_kind: str | None = None,
    repository_roots: tuple[str | Path, ...] = (),
    known_network_roots: tuple[str | Path, ...] = (),
) -> VerifiedArchive:
    path = validate_runtime_path(
        Path(archive_path).expanduser().resolve(strict=True),
        repository_roots=repository_roots,
        known_network_roots=known_network_roots,
    )
    content = path.read_bytes()
    if not content.startswith(ARCHIVE_MAGIC):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_FORMAT_INVALID")
    try:
        payload = Fernet(_archive_key(archive_key_provider, create=False)).decrypt(
            content[len(ARCHIVE_MAGIC) :]
        )
    except (InvalidToken, ValueError) as exc:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_AUTHENTICATION_FAILED") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            names = _safe_archive_members(archive)
            manifest = json.loads(archive.read("manifest.json"))
            files = {name: archive.read(name) for name in names if name != "manifest.json"}
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_STRUCTURE_INVALID") from exc
    if manifest.get("archive_format") != ARCHIVE_FORMAT:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_FORMAT_INVALID")
    if manifest.get("archive_format_version") != ARCHIVE_FORMAT_VERSION:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_FORMAT_UNSUPPORTED")
    if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION or not manifest.get("complete"):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_INCOMPLETE")
    if not _ARCHIVE_ID.fullmatch(str(manifest.get("archive_id", ""))):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_ID_INVALID")
    kind = manifest.get("kind")
    if kind not in {"BACKUP", "EXPORT"} or (expected_kind and kind != expected_kind):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_KIND_INVALID")
    if _version_tuple(manifest.get("minimum_application_version", "")) > _version_tuple(
        __version__
    ) or _version_tuple(manifest.get("application_version", "")) > _version_tuple(__version__):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_DOWNGRADE_BLOCKED")
    declared = manifest.get("files")
    if not isinstance(declared, list) or len(declared) != len(files):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_FILE_MANIFEST_INVALID")
    expected = {item.get("path"): item for item in declared if isinstance(item, dict)}
    if set(expected) != set(files):
        raise RecoveryInvariantError("FINANCE_ARCHIVE_FILE_MANIFEST_INVALID")
    for name, data in files.items():
        item = expected[name]
        if item.get("size") != len(data) or item.get("sha256") != hashlib.sha256(data).hexdigest():
            raise RecoveryInvariantError("FINANCE_ARCHIVE_FILE_INTEGRITY_FAILED")
    details = _validate_sqlite_image(files["store.sqlite"], files)
    if manifest.get("store_schema_version") != details["store_schema_version"]:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_SCHEMA_MISMATCH")
    if manifest.get("event_store_sequence") != details["last_event_sequence"]:
        raise RecoveryInvariantError("FINANCE_ARCHIVE_SEQUENCE_MISMATCH")
    return VerifiedArchive(path=path, manifest=manifest, files=files)


def list_backups(
    store: LocalFinanceStore,
    archive_key_provider: KeyProvider,
    backup_directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = _local_directory(backup_directory or default_backup_directory(store), store)
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob(f"*{BACKUP_SUFFIX}"), reverse=True):
        try:
            verified = verify_archive(
                path,
                archive_key_provider,
                expected_kind="BACKUP",
                repository_roots=store.repository_roots,
                known_network_roots=store.known_network_roots,
            )
            rows.append(
                {
                    **verified.manifest,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "verification_status": "VALID",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "archive_id": None,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "verification_status": "INVALID",
                    "error_code": str(exc),
                }
            )
    return rows


def delete_backup(
    store: LocalFinanceStore,
    archive_key_provider: KeyProvider,
    archive_id: str,
    backup_directory: str | Path | None = None,
) -> dict[str, Any]:
    if not _ARCHIVE_ID.fullmatch(archive_id) or not archive_id.startswith("bkp_"):
        raise RecoveryInvariantError("FINANCE_BACKUP_ID_INVALID")
    for row in list_backups(store, archive_key_provider, backup_directory):
        if row.get("archive_id") == archive_id:
            path = Path(str(row["path"]))
            path.unlink()
            return {"archive_id": archive_id, "deleted": True, "path": str(path)}
    raise RecoveryInvariantError("FINANCE_BACKUP_NOT_FOUND")


def _write_restored_store(
    root: Path,
    files: dict[str, bytes],
    store_key_provider: KeyProvider,
) -> None:
    root.mkdir(parents=True, exist_ok=False)
    (root / LocalFinanceStore.DB_FILE).write_bytes(
        cipher_for(store_key_provider).encrypt(files["store.sqlite"])
    )
    for name, content in files.items():
        if not name.startswith("imports/"):
            continue
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(cipher_for(store_key_provider).encrypt(content))


def _atomic_replace(store: LocalFinanceStore, files: dict[str, bytes]) -> None:
    root = store.data_dir.resolve(strict=False)
    parent = root.parent
    stage = parent / f".{root.name}.restore-{uuid4().hex}"
    rollback = parent / f".{root.name}.rollback-{uuid4().hex}"
    _write_restored_store(stage, files, store.key_provider)
    store.discard()
    moved_original = False
    installed_stage = False
    try:
        if root.exists():
            root.replace(rollback)
            moved_original = True
        stage.replace(root)
        installed_stage = True
        store.open()
        integrity = validate_store_integrity(store)
        if integrity["status"] != "VALID":
            raise RecoveryInvariantError("FINANCE_RESTORED_STORE_INTEGRITY_FAILED")
    except Exception:
        store.discard()
        if installed_stage and root.exists():
            shutil.rmtree(root)
        if moved_original and rollback.exists():
            rollback.replace(root)
        if root.exists():
            store.open()
        if stage.exists():
            shutil.rmtree(stage)
        raise
    if rollback.exists():
        shutil.rmtree(rollback)


def restore_backup(
    store: LocalFinanceStore,
    archive_path: str | Path,
    archive_key_provider: KeyProvider,
) -> dict[str, Any]:
    verified = verify_archive(
        archive_path,
        archive_key_provider,
        expected_kind="BACKUP",
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    try:
        verified.path.relative_to(store.data_dir.resolve(strict=False))
    except ValueError:
        pass
    else:
        raise RecoveryInvariantError("FINANCE_RESTORE_ARCHIVE_INSIDE_STORE")
    _atomic_replace(store, verified.files)
    return {
        "archive_id": verified.manifest["archive_id"],
        "restored": True,
        "event_store_sequence": verified.manifest["event_store_sequence"],
        "store_schema_version": verified.manifest["store_schema_version"],
    }


def import_finance_archive(
    store: LocalFinanceStore,
    archive_path: str | Path,
    archive_key_provider: KeyProvider,
) -> dict[str, Any]:
    verified = verify_archive(
        archive_path,
        archive_key_provider,
        expected_kind="EXPORT",
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    try:
        verified.path.relative_to(store.data_dir.resolve(strict=False))
    except ValueError:
        pass
    else:
        raise RecoveryInvariantError("FINANCE_IMPORT_ARCHIVE_INSIDE_STORE")
    _atomic_replace(store, verified.files)
    return {
        "archive_id": verified.manifest["archive_id"],
        "imported": True,
        "event_store_sequence": verified.manifest["event_store_sequence"],
        "store_schema_version": verified.manifest["store_schema_version"],
    }


def validate_store_integrity(store: LocalFinanceStore) -> dict[str, Any]:
    try:
        files = {"store.sqlite": store.serialized_snapshot(), **_import_plaintexts(store)}
        details = _validate_sqlite_image(files["store.sqlite"], files)
        return {
            "status": "VALID",
            "checked_at": datetime.now(UTC).isoformat(),
            **details,
            "storage_encryption": "FERNET_AUTHENTICATED",
        }
    except Exception as exc:
        return {
            "status": "INVALID",
            "checked_at": datetime.now(UTC).isoformat(),
            "error_code": str(exc),
        }


def repair_local_store(store: LocalFinanceStore) -> dict[str, Any]:
    before = validate_store_integrity(store)
    if before["status"] != "VALID":
        raise RecoveryInvariantError("FINANCE_REPAIR_REQUIRES_VALID_EVENT_HISTORY")
    store.connection.execute("DELETE FROM projection_checkpoint")
    store.connection.execute("PRAGMA optimize")
    store.connection.commit()
    temporary = store.data_dir / f".{LocalFinanceStore.DB_FILE}.tmp"
    removed = False
    if temporary.exists():
        temporary.unlink()
        removed = True
    store.persist_snapshot()
    after = validate_store_integrity(store)
    if after["status"] != "VALID":
        raise RecoveryInvariantError("FINANCE_REPAIR_VALIDATION_FAILED")
    return {
        "status": "REPAIRED",
        "projection_checkpoints_reset": True,
        "temporary_file_removed": removed,
        "integrity": after,
    }


def rotate_encryption_key(
    store: LocalFinanceStore,
    archive_key_provider: KeyProvider,
    backup_directory: str | Path | None = None,
) -> dict[str, Any]:
    provider = store.key_provider
    if not hasattr(provider, "replace_key"):
        raise RecoveryInvariantError("FINANCE_KEY_PROVIDER_NOT_ROTATABLE")
    recovery_backup = create_backup(store, archive_key_provider, backup_directory)
    files = {"store.sqlite": store.serialized_snapshot(), **_import_plaintexts(store)}
    old_key = provider.get_key()
    new_key = Fernet.generate_key()
    root = store.data_dir.resolve(strict=False)
    stage = root.parent / f".{root.name}.key-rotation-{uuid4().hex}"
    rollback = root.parent / f".{root.name}.key-rollback-{uuid4().hex}"
    _write_restored_store(stage, files, StaticKeyProvider(new_key))
    store.discard()
    moved_original = False
    installed_stage = False
    try:
        root.replace(rollback)
        moved_original = True
        stage.replace(root)
        installed_stage = True
        provider.replace_key(new_key)  # type: ignore[attr-defined]
        store.open()
        if validate_store_integrity(store)["status"] != "VALID":
            raise RecoveryInvariantError("FINANCE_KEY_ROTATION_VALIDATION_FAILED")
    except Exception:
        store.discard()
        try:
            provider.replace_key(old_key)  # type: ignore[attr-defined]
        finally:
            if installed_stage and root.exists():
                shutil.rmtree(root)
            if moved_original and rollback.exists():
                rollback.replace(root)
            if stage.exists():
                shutil.rmtree(stage)
            if root.exists():
                store.open()
        raise
    if rollback.exists():
        shutil.rmtree(rollback)
    return {
        "status": "ROTATED",
        "key_fingerprint": key_fingerprint(provider),
        "recovery_backup_id": recovery_backup["archive_id"],
    }


def key_status(store: LocalFinanceStore, archive_key_provider: KeyProvider) -> dict[str, Any]:
    database = {
        "strategy": type(store.key_provider).__name__,
        "status": "AVAILABLE",
        "fingerprint": key_fingerprint(store.key_provider),
    }
    try:
        archive_key = _archive_key(archive_key_provider, create=False)
        backup = {
            "strategy": type(archive_key_provider).__name__,
            "status": "AVAILABLE",
            "fingerprint": hashlib.sha256(archive_key).hexdigest()[:16],
            "independent_from_store": archive_key != store.key_provider.get_key(),
        }
    except Exception:
        backup = {
            "strategy": type(archive_key_provider).__name__,
            "status": "NOT_PROVISIONED",
            "fingerprint": None,
            "independent_from_store": None,
        }
    return {"database_key": database, "archive_key": backup}


def migration_status(store: LocalFinanceStore) -> dict[str, Any]:
    return {
        "current_store_schema_version": store.schema_version(),
        "supported_store_schema_version": LocalFinanceStore.CURRENT_SCHEMA_VERSION,
        "status": "CURRENT",
        "history": store.migration_history(),
        "downgrade_protection": "ENABLED",
    }


__all__ = [
    "RecoveryInvariantError",
    "create_backup",
    "delete_backup",
    "export_finance_data",
    "import_finance_archive",
    "key_status",
    "list_backups",
    "migration_status",
    "repair_local_store",
    "restore_backup",
    "rotate_encryption_key",
    "validate_store_integrity",
    "verify_archive",
]
