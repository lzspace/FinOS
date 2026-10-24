"""Encrypted append-only local event store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
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


@dataclass
class ApplicationReadContext:
    """Request-local, sequence-bounded read state.

    Rows may be fetched through different selectors, but an event is integrity
    checked and decoded at most once for the lifetime of this context.
    """

    sequence: int
    decoded_event_cache: dict[int, dict[str, Any]] = field(default_factory=dict)
    event_query_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = field(default_factory=dict)
    projection_cache: dict[tuple[Any, ...], Any] = field(default_factory=dict)
    events_loaded: int = 0
    events_decoded: int = 0
    store_queries: int = 0
    request_cache_hits: int = 0
    request_cache_misses: int = 0

    def memoize(self, key: tuple[Any, ...], factory: Any) -> Any:
        if key in self.projection_cache:
            self.request_cache_hits += 1
            return self.projection_cache[key]
        self.request_cache_misses += 1
        value = factory()
        self.projection_cache[key] = value
        return value


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
        self._read_context: ApplicationReadContext | None = None

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
        CREATE TABLE IF NOT EXISTS projection_registry (
          projection_name TEXT PRIMARY KEY, projection_version TEXT NOT NULL,
          last_applied_sequence INTEGER NOT NULL, status TEXT NOT NULL,
          updated_at TEXT NOT NULL, error_code TEXT);
        CREATE TABLE IF NOT EXISTS projection_document (
          projection_name TEXT NOT NULL, projection_version TEXT NOT NULL,
          document_key TEXT NOT NULL, last_applied_sequence INTEGER NOT NULL,
          document_json BLOB NOT NULL, document_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(projection_name, projection_version, document_key));
        CREATE INDEX IF NOT EXISTS idx_projection_document_sequence
          ON projection_document(projection_name, last_applied_sequence);
        CREATE TABLE IF NOT EXISTS store_metadata (
          metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_log (
          migration_id TEXT PRIMARY KEY, from_version INTEGER NOT NULL, to_version INTEGER NOT NULL,
          applied_at TEXT NOT NULL, application_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS release_metadata (
          metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_event_store_type_sequence
          ON event_store(event_type, sequence_number);
        CREATE INDEX IF NOT EXISTS idx_event_store_aggregate_sequence
          ON event_store(aggregate_type, aggregate_id, sequence_number);
        CREATE INDEX IF NOT EXISTS idx_event_store_transaction_page
          ON event_store(
            event_type,
            json_extract(payload, '$.booking_date') DESC,
            json_extract(payload, '$.transaction_id') DESC
          );
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
        # Read models are disposable. A failure to mark them stale must never
        # roll back an already committed domain event.
        try:
            self.connection.execute(
                "UPDATE projection_registry SET status='STALE', updated_at=? "
                "WHERE last_applied_sequence < ?",
                (datetime.now(UTC).isoformat(), self.latest_sequence()),
            )
            self.connection.commit()
        except sqlite3.DatabaseError:
            pass
        return sequences

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        return self.events_snapshot(event_type=event_type)

    def latest_sequence(self) -> int:
        """Return the current event position without loading event payloads."""

        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) FROM event_store"
        ).fetchone()
        return int(row[0])

    @contextmanager
    def read_context(self) -> Iterator[ApplicationReadContext]:
        if self._read_context is not None:
            yield self._read_context
            return
        context = ApplicationReadContext(sequence=self.latest_sequence())
        self._read_context = context
        try:
            yield context
        finally:
            self._read_context = None

    @property
    def current_read_context(self) -> ApplicationReadContext | None:
        return self._read_context

    def _decode_event_row(
        self, row: sqlite3.Row, context: ApplicationReadContext | None
    ) -> dict[str, Any]:
        sequence = int(row["sequence_number"])
        if context is not None and sequence in context.decoded_event_cache:
            context.request_cache_hits += 1
            return context.decoded_event_cache[sequence]
        if hashlib.sha256(row["payload"]).hexdigest() != row["payload_hash"]:
            raise StoreInvariantError("FINANCE_EVENT_INTEGRITY_FAILED")
        decoded = {**dict(row), "payload": json.loads(row["payload"])}
        if context is not None:
            context.events_decoded += 1
            context.decoded_event_cache[sequence] = decoded
        return decoded

    def events_snapshot(
        self,
        *,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        after_sequence: int | None = None,
        through_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        context = self._read_context
        upper = through_sequence if through_sequence is not None else (
            context.sequence if context is not None else self.latest_sequence()
        )
        key = ("events", event_type, aggregate_type, aggregate_id, after_sequence, upper)
        if context is not None and key in context.event_query_cache:
            context.request_cache_hits += 1
            return context.event_query_cache[key]
        clauses = ["sequence_number <= ?"]
        parameters: list[Any] = [upper]
        for column, value in (
            ("event_type", event_type),
            ("aggregate_type", aggregate_type),
            ("aggregate_id", aggregate_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if after_sequence is not None:
            clauses.append("sequence_number > ?")
            parameters.append(after_sequence)
        rows = self.connection.execute(
            f"SELECT * FROM event_store WHERE {' AND '.join(clauses)} ORDER BY sequence_number",
            tuple(parameters),
        ).fetchall()
        if context is not None:
            context.store_queries += 1
            context.events_loaded += len(rows)
            context.request_cache_misses += 1
        result = [self._decode_event_row(row, context) for row in rows]
        if context is not None:
            context.event_query_cache[key] = result
        return result

    def events_by_type(self, event_type: str) -> list[dict[str, Any]]:
        return self.events_snapshot(event_type=event_type)

    def events_by_type_prefix(self, prefix: str) -> list[dict[str, Any]]:
        context = self._read_context
        upper = context.sequence if context is not None else self.latest_sequence()
        key = ("events-prefix", prefix, upper)
        if context is not None and key in context.event_query_cache:
            context.request_cache_hits += 1
            return context.event_query_cache[key]
        rows = self.connection.execute(
            "SELECT * FROM event_store WHERE sequence_number <= ? AND event_type LIKE ? "
            "ORDER BY sequence_number",
            (upper, f"{prefix}%"),
        ).fetchall()
        if context is not None:
            context.store_queries += 1
            context.events_loaded += len(rows)
            context.request_cache_misses += 1
        result = [self._decode_event_row(row, context) for row in rows]
        if context is not None:
            context.event_query_cache[key] = result
        return result

    def events_by_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> list[dict[str, Any]]:
        return self.events_snapshot(
            aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )

    def events_in_sequence_range(
        self, after_sequence: int, through_sequence: int
    ) -> list[dict[str, Any]]:
        return self.events_snapshot(
            after_sequence=after_sequence, through_sequence=through_sequence
        )

    def events_by_ids(self, event_ids: list[str]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        context = self._read_context
        upper = context.sequence if context is not None else self.latest_sequence()
        unique = tuple(sorted(set(event_ids)))
        key = ("event-ids", unique, upper)
        if context is not None and key in context.event_query_cache:
            context.request_cache_hits += 1
            return context.event_query_cache[key]
        placeholders = ",".join("?" for _ in unique)
        rows = self.connection.execute(
            f"SELECT * FROM event_store WHERE sequence_number <= ? "
            f"AND event_id IN ({placeholders}) ORDER BY sequence_number",
            (upper, *unique),
        ).fetchall()
        if context is not None:
            context.store_queries += 1
            context.events_loaded += len(rows)
            context.request_cache_misses += 1
        result = [self._decode_event_row(row, context) for row in rows]
        if context is not None:
            context.event_query_cache[key] = result
        return result

    def transaction_events_page(
        self,
        *,
        period_start: str | None,
        period_end: str | None,
        limit: int,
        after: tuple[str, str] | None = None,
        account_ids: list[str] | None = None,
        direction: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        context = self._read_context
        upper = context.sequence if context is not None else self.latest_sequence()
        clauses = ["sequence_number <= ?", "event_type = 'TransactionNormalized'"]
        parameters: list[Any] = [upper]
        if period_start:
            clauses.append("json_extract(payload, '$.booking_date') >= ?")
            parameters.append(period_start)
        if period_end:
            clauses.append("json_extract(payload, '$.booking_date') <= ?")
            parameters.append(period_end)
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            clauses.append(f"json_extract(payload, '$.account_id') IN ({placeholders})")
            parameters.extend(account_ids)
        if direction in {"INCOME", "CREDIT"}:
            clauses.append("CAST(json_extract(payload, '$.amount') AS REAL) >= 0")
        elif direction in {"EXPENSE", "DEBIT"}:
            clauses.append("CAST(json_extract(payload, '$.amount') AS REAL) < 0")
        if search:
            clauses.append(
                "lower(json_extract(payload, '$.counterparty') || ' ' || "
                "json_extract(payload, '$.normalized_description')) LIKE ?"
            )
            parameters.append(f"%{search.casefold()}%")
        if after:
            clauses.append(
                "(json_extract(payload, '$.booking_date') < ? OR "
                "(json_extract(payload, '$.booking_date') = ? AND "
                "json_extract(payload, '$.transaction_id') < ?))"
            )
            parameters.extend((after[0], after[0], after[1]))
        parameters.append(limit)
        rows = self.connection.execute(
            f"SELECT * FROM event_store WHERE {' AND '.join(clauses)} "
            "ORDER BY json_extract(payload, '$.booking_date') DESC, "
            "json_extract(payload, '$.transaction_id') DESC LIMIT ?",
            tuple(parameters),
        ).fetchall()
        if context is not None:
            context.store_queries += 1
            context.events_loaded += len(rows)
            context.request_cache_misses += 1
        return [self._decode_event_row(row, context) for row in rows]

    def next_aggregate_version(self, aggregate_type: str, aggregate_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(event_version), 0) FROM event_store "
            "WHERE aggregate_type=? AND aggregate_id=?",
            (aggregate_type, aggregate_id),
        ).fetchone()
        return int(row[0]) + 1

    def store_import_file(
        self,
        import_id: str,
        source_path: Path,
        content: bytes,
        parser_version: str,
        *,
        status: str = "IMPORTED",
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
                    status,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def update_import_status(self, content_hash: str, status: str) -> None:
        if status not in {
            "ANALYZED",
            "IMPORTED",
            "COMPLETED",
            "PARTIALLY_COMPLETED",
            "REVIEW_REQUIRED",
            "FAILED",
        }:
            raise StoreInvariantError("FINANCE_IMPORT_STATUS_INVALID")
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE import_files SET status=? WHERE content_hash=?",
                (status, content_hash),
            )
            if cursor.rowcount != 1:
                raise StoreInvariantError("FINANCE_IMPORT_SOURCE_NOT_FOUND")

    def has_import_content_hash(self, content: bytes) -> bool:
        digest = hashlib.sha256(content).hexdigest()
        return (
            self.connection.execute(
                "SELECT 1 FROM import_files WHERE content_hash=?", (digest,)
            ).fetchone()
            is not None
        )

    def load_import_content(self, content_hash: str) -> bytes:
        """Return an authenticated imported source without exposing its original path."""
        row = self.connection.execute(
            "SELECT stored_file_reference FROM import_files WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        if row is None:
            raise StoreInvariantError("FINANCE_IMPORT_SOURCE_NOT_FOUND")
        reference = str(row[0])
        if not reference.startswith("imports/") or ".." in Path(reference).parts:
            raise StoreInvariantError("FINANCE_IMPORT_REFERENCE_INVALID")
        encrypted = self._guard() / reference
        try:
            content = cipher_for(self.key_provider).decrypt(encrypted.read_bytes())
        except Exception as exc:
            raise StoreInvariantError("FINANCE_IMPORT_DECRYPTION_FAILED") from exc
        if hashlib.sha256(content).hexdigest() != content_hash:
            raise StoreInvariantError("FINANCE_IMPORT_CONTENT_HASH_MISMATCH")
        return content
