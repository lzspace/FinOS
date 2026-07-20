"""Encrypted append-only local event store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .crypto import KeyProvider, cipher_for
from .schema_validation import validate_event

# Kept separate so callers can use the pre-existing policy module without an import-time side effect.
from .storage_policy import validate_runtime_path
from .workspace_lock import WorkspaceLock


class StoreInvariantError(ValueError):
    pass


class LocalFinanceStore:
    """SQLite state held in memory and persisted only as authenticated ciphertext.

    The on-disk database is an encrypted snapshot (not a plaintext SQLite file).
    Every database operation re-checks the runtime root before access.
    """

    DB_FILE = "finance.sqlite.fernet"
    CURRENT_SCHEMA_VERSION = 3

    def __init__(
        self,
        data_dir: str | Path,
        key_provider: KeyProvider,
        *,
        repository_roots: tuple[str | Path, ...] = (),
        known_network_roots: tuple[str | Path, ...] = (),
        acquire_workspace_lock: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.key_provider = key_provider
        self.repository_roots = repository_roots
        self.known_network_roots = known_network_roots
        self.acquire_workspace_lock = acquire_workspace_lock
        self._connection: sqlite3.Connection | None = None
        self._workspace_lock = WorkspaceLock(self.data_dir) if acquire_workspace_lock else None

    def _guard(self) -> Path:
        return validate_runtime_path(
            self.data_dir,
            repository_roots=self.repository_roots,
            known_network_roots=self.known_network_roots,
        )

    def open(self) -> "LocalFinanceStore":
        root = self._guard()
        root.mkdir(parents=True, exist_ok=True)
        self._guard()
        if self._workspace_lock:
            self._workspace_lock.acquire()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        encrypted = root / self.DB_FILE
        if encrypted.exists():
            try:
                conn.deserialize(cipher_for(self.key_provider).decrypt(encrypted.read_bytes()))
            except Exception as exc:
                conn.close()
                if self._workspace_lock:
                    self._workspace_lock.release()
                raise StoreInvariantError("FINANCE_STORE_DECRYPTION_FAILED") from exc
        self._connection = conn
        try:
            self._migrate()
        except Exception:
            conn.close()
            self._connection = None
            if self._workspace_lock:
                self._workspace_lock.release()
            raise
        return self

    def close(self) -> None:
        if not self._connection:
            if self._workspace_lock:
                self._workspace_lock.release()
            return
        try:
            self.persist_snapshot()
        finally:
            self._connection.close()
            self._connection = None
            if self._workspace_lock:
                self._workspace_lock.release()

    def persist_snapshot(self) -> Path:
        """Atomically persist the current in-memory database without closing it."""
        root = self._guard()
        serialized = self.connection.serialize()
        encrypted = cipher_for(self.key_provider).encrypt(serialized)
        temporary = root / f".{self.DB_FILE}.tmp"
        temporary.write_bytes(encrypted)
        temporary.replace(root / self.DB_FILE)
        return root / self.DB_FILE

    def serialized_snapshot(self) -> bytes:
        """Return a consistent plaintext SQLite image for an encrypted archive."""
        return bytes(self.connection.serialize())

    def discard(self) -> None:
        """Close memory state without overwriting an externally restored snapshot."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "LocalFinanceStore":
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        self._guard()
        if not self._connection:
            raise StoreInvariantError("FINANCE_STORE_NOT_OPEN")
        return self._connection

    def _migrate(self) -> None:
        had_core_schema = (
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_store'"
            ).fetchone()
            is not None
        )
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS event_store (
          sequence_number INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
          event_type TEXT NOT NULL, aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
          event_version INTEGER NOT NULL, occurred_at TEXT NOT NULL, correlation_id TEXT NOT NULL,
          causation_id TEXT NOT NULL, schema_version TEXT NOT NULL, payload BLOB NOT NULL,
          payload_hash TEXT NOT NULL, UNIQUE(aggregate_type, aggregate_id, event_version));
        CREATE TABLE IF NOT EXISTS command_log (
          command_id TEXT PRIMARY KEY, command_type TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, received_at TEXT NOT NULL, completed_at TEXT, result_event_ids TEXT, error_code TEXT);
        CREATE TABLE IF NOT EXISTS import_files (
          import_id TEXT PRIMARY KEY, source_path_hash TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
          stored_file_reference TEXT NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projection_checkpoint (
          projection_name TEXT PRIMARY KEY, last_sequence_number INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS store_metadata (
          metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_log (
          migration_id TEXT PRIMARY KEY, from_version INTEGER NOT NULL, to_version INTEGER NOT NULL,
          applied_at TEXT NOT NULL, application_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS release_metadata (
          metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
        """)
        stored = self.connection.execute(
            "SELECT metadata_value FROM store_metadata WHERE metadata_key='schema_version'"
        ).fetchone()
        if stored is None:
            # Existing databases without metadata are the pre-0.8.0 schema.
            initial = 1 if had_core_schema else self.CURRENT_SCHEMA_VERSION
            self.connection.execute(
                "INSERT INTO store_metadata(metadata_key, metadata_value) VALUES ('schema_version', ?)",
                (str(initial),),
            )
            current = initial
        else:
            current = int(stored[0])
        if current > self.CURRENT_SCHEMA_VERSION:
            raise StoreInvariantError("FINANCE_STORE_DOWNGRADE_BLOCKED")
        while current < self.CURRENT_SCHEMA_VERSION:
            target = current + 1
            self._apply_migration(current, target)
            current = target
        self.connection.commit()

    def _apply_migration(self, current: int, target: int) -> None:
        self.connection.execute(
            "INSERT INTO migration_log VALUES (?, ?, ?, ?, ?)",
            (
                f"store_v{current}_to_v{target}",
                current,
                target,
                datetime.now(UTC).isoformat(),
                __version__,
            ),
        )
        self.connection.execute(
            "UPDATE store_metadata SET metadata_value=? WHERE metadata_key='schema_version'",
            (str(target),),
        )

    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT metadata_value FROM store_metadata WHERE metadata_key='schema_version'"
        ).fetchone()
        if row is None:
            raise StoreInvariantError("FINANCE_STORE_SCHEMA_VERSION_MISSING")
        return int(row[0])

    def migration_history(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM migration_log ORDER BY applied_at, migration_id"
        ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def append_events(self, command: dict[str, str], events: list[dict[str, Any]]) -> list[int]:
        """Atomically append a command's events, enforcing idempotency and versions."""
        with self.transaction() as conn:
            previous = conn.execute(
                "SELECT result_event_ids FROM command_log WHERE idempotency_key = ?",
                (command["idempotency_key"],),
            ).fetchone()
            if previous:
                return [int(value) for value in json.loads(previous[0] or "[]")]
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO command_log(command_id, command_type, idempotency_key, status, received_at) VALUES (?, ?, ?, 'RECEIVED', ?)",
                (command["command_id"], command["command_type"], command["idempotency_key"], now),
            )
            sequences: list[int] = []
            for event in events:
                validate_event(event)
                current = conn.execute(
                    "SELECT COALESCE(MAX(event_version), 0) FROM event_store WHERE aggregate_type=? AND aggregate_id=?",
                    (event["aggregate_type"], event["aggregate_id"]),
                ).fetchone()[0]
                if event["aggregate_version"] != current + 1:
                    raise StoreInvariantError("FINANCE_AGGREGATE_VERSION_GAP")
                payload = json.dumps(
                    event["payload"], separators=(",", ":"), sort_keys=True
                ).encode()
                digest = hashlib.sha256(payload).hexdigest()
                record = conn.execute(
                    """INSERT INTO event_store(event_id,event_type,aggregate_type,aggregate_id,event_version,occurred_at,correlation_id,causation_id,schema_version,payload,payload_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event["event_id"],
                        event["event_type"],
                        event["aggregate_type"],
                        event["aggregate_id"],
                        event["aggregate_version"],
                        event["occurred_at"],
                        event["correlation_id"],
                        event["causation_id"],
                        event["schema_version"],
                        payload,
                        digest,
                    ),
                )
                sequences.append(int(record.lastrowid))
            conn.execute(
                "UPDATE command_log SET status='COMPLETED', completed_at=?, result_event_ids=? WHERE command_id=?",
                (datetime.now(UTC).isoformat(), json.dumps(sequences), command["command_id"]),
            )
        return sequences

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM event_store"
            + (" WHERE event_type=?" if event_type else "")
            + " ORDER BY sequence_number"
        )
        rows = self.connection.execute(query, (() if not event_type else (event_type,))).fetchall()
        result = []
        for row in rows:
            if hashlib.sha256(row["payload"]).hexdigest() != row["payload_hash"]:
                raise StoreInvariantError("FINANCE_EVENT_INTEGRITY_FAILED")
            result.append({**dict(row), "payload": json.loads(row["payload"])})
        return result

    def next_aggregate_version(self, aggregate_type: str, aggregate_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(event_version), 0) FROM event_store "
            "WHERE aggregate_type=? AND aggregate_id=?",
            (aggregate_type, aggregate_id),
        ).fetchone()
        return int(row[0]) + 1

    def store_import_file(
        self, import_id: str, source_path: Path, content: bytes, parser_version: str
    ) -> None:
        root = self._guard()
        imports = root / "imports"
        imports.mkdir(exist_ok=True)
        content_hash = hashlib.sha256(content).hexdigest()
        reference = f"imports/{import_id}.csv.fernet"
        (root / reference).write_bytes(cipher_for(self.key_provider).encrypt(content))
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO import_files VALUES (?,?,?,?,?,?,?)",
                (
                    import_id,
                    hashlib.sha256(str(source_path).encode()).hexdigest(),
                    content_hash,
                    reference,
                    parser_version,
                    "IMPORTED",
                    datetime.now(UTC).isoformat(),
                ),
            )

    def has_import_content_hash(self, content: bytes) -> bool:
        digest = hashlib.sha256(content).hexdigest()
        return (
            self.connection.execute(
                "SELECT 1 FROM import_files WHERE content_hash=?", (digest,)
            ).fetchone()
            is not None
        )
