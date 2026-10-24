"""Deterministic, disposable persisted read-model infrastructure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from .store import LocalFinanceStore, StoreInvariantError


PROJECTION_INFRASTRUCTURE_VERSION = "1.0.0"


@dataclass(frozen=True)
class ProjectionDefinition:
    projection_name: str
    projection_version: str


@dataclass(frozen=True)
class ProjectionCheckpoint:
    projection_name: str
    projection_version: str
    last_applied_sequence: int
    status: str


class ProjectionRegistry:
    """Stores reproducible documents, never authoritative domain state."""

    def __init__(self, store: LocalFinanceStore) -> None:
        self.store = store

    @staticmethod
    def document_key(parameters: dict[str, Any]) -> str:
        canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _serialize(value: Any) -> tuple[bytes, str]:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return encoded, hashlib.sha256(encoded).hexdigest()

    def checkpoint(self, definition: ProjectionDefinition) -> ProjectionCheckpoint:
        row = self.store.connection.execute(
            "SELECT projection_version,last_applied_sequence,status "
            "FROM projection_registry WHERE projection_name=?",
            (definition.projection_name,),
        ).fetchone()
        if row is None or row["projection_version"] != definition.projection_version:
            return ProjectionCheckpoint(
                definition.projection_name, definition.projection_version, 0, "STALE"
            )
        return ProjectionCheckpoint(
            definition.projection_name,
            str(row["projection_version"]),
            int(row["last_applied_sequence"]),
            str(row["status"]),
        )

    def load(
        self,
        definition: ProjectionDefinition,
        parameters: dict[str, Any],
        through_sequence: int,
    ) -> Any | None:
        key = self.document_key(parameters)
        row = self.store.connection.execute(
            "SELECT document_json,document_hash FROM projection_document "
            "WHERE projection_name=? AND projection_version=? AND document_key=? "
            "AND last_applied_sequence=?",
            (
                definition.projection_name,
                definition.projection_version,
                key,
                through_sequence,
            ),
        ).fetchone()
        if row is None:
            return None
        encoded = bytes(row["document_json"])
        if hashlib.sha256(encoded).hexdigest() != row["document_hash"]:
            self.invalidate(definition, status="FAILED")
            raise StoreInvariantError("FINANCE_PROJECTION_INTEGRITY_FAILED")
        return json.loads(encoded)

    def store_document(
        self,
        definition: ProjectionDefinition,
        parameters: dict[str, Any],
        through_sequence: int,
        value: Any,
    ) -> None:
        key = self.document_key(parameters)
        encoded, digest = self._serialize(value)
        now = datetime.now(UTC).isoformat()
        # Document and checkpoint advance in the same SQLite transaction.
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM projection_document WHERE projection_name=? "
                "AND projection_version<>?",
                (definition.projection_name, definition.projection_version),
            )
            connection.execute(
                "INSERT INTO projection_document VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(projection_name,projection_version,document_key) DO UPDATE SET "
                "last_applied_sequence=excluded.last_applied_sequence," 
                "document_json=excluded.document_json,document_hash=excluded.document_hash," 
                "updated_at=excluded.updated_at",
                (
                    definition.projection_name,
                    definition.projection_version,
                    key,
                    through_sequence,
                    encoded,
                    digest,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO projection_registry VALUES (?,?,?,?,?,NULL) "
                "ON CONFLICT(projection_name) DO UPDATE SET "
                "projection_version=excluded.projection_version," 
                "last_applied_sequence=excluded.last_applied_sequence," 
                "status='CURRENT',updated_at=excluded.updated_at,error_code=NULL",
                (
                    definition.projection_name,
                    definition.projection_version,
                    through_sequence,
                    "CURRENT",
                    now,
                ),
            )

    def materialize(
        self,
        definition: ProjectionDefinition,
        parameters: dict[str, Any],
        through_sequence: int,
        builder: Callable[[], Any],
    ) -> Any:
        try:
            cached = self.load(definition, parameters, through_sequence)
        except StoreInvariantError as exc:
            if str(exc) != "FINANCE_PROJECTION_INTEGRITY_FAILED":
                raise
            # A derived projection may be discarded and rebuilt. Domain events
            # remain untouched and authoritative.
            self.delete(definition)
            cached = None
        if cached is not None:
            return cached
        try:
            value = builder()
            self.store_document(definition, parameters, through_sequence, value)
            return value
        except Exception:
            self.invalidate(definition, status="FAILED")
            raise

    def invalidate(
        self, definition: ProjectionDefinition, *, status: str = "STALE"
    ) -> None:
        if status not in {"STALE", "REBUILDING", "FAILED"}:
            raise ValueError("FINANCE_PROJECTION_STATUS_INVALID")
        self.store.connection.execute(
            "INSERT INTO projection_registry VALUES (?,?,0,?,?,NULL) "
            "ON CONFLICT(projection_name) DO UPDATE SET status=excluded.status," 
            "updated_at=excluded.updated_at,error_code=NULL",
            (
                definition.projection_name,
                definition.projection_version,
                status,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.store.connection.commit()

    def delete(self, definition: ProjectionDefinition) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM projection_document WHERE projection_name=?",
                (definition.projection_name,),
            )
            connection.execute(
                "DELETE FROM projection_registry WHERE projection_name=?",
                (definition.projection_name,),
            )

    def canonical_hash(self, definition: ProjectionDefinition) -> str:
        rows = self.store.connection.execute(
            "SELECT document_key,last_applied_sequence,document_hash "
            "FROM projection_document WHERE projection_name=? "
            "ORDER BY projection_version,document_key",
            (definition.projection_name,),
        ).fetchall()
        encoded = json.dumps([dict(row) for row in rows], sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


class ProjectionRunner:
    def __init__(self, registry: ProjectionRegistry) -> None:
        self.registry = registry

    def rebuild(
        self,
        definition: ProjectionDefinition,
        parameters: dict[str, Any],
        through_sequence: int,
        builder: Callable[[], Any],
    ) -> Any:
        self.registry.delete(definition)
        return self.registry.materialize(
            definition, parameters, through_sequence, builder
        )

    def catch_up(
        self,
        definition: ProjectionDefinition,
        through_sequence: int,
        apply_event: Callable[[Any, dict[str, Any]], None],
        *,
        reset: Callable[[Any], None] | None = None,
    ) -> ProjectionCheckpoint:
        """Apply each missing event exactly once with its checkpoint atomically."""

        checkpoint = self.registry.checkpoint(definition)
        if checkpoint.projection_version != definition.projection_version:
            checkpoint = ProjectionCheckpoint(
                definition.projection_name, definition.projection_version, 0, "STALE"
            )
        if checkpoint.last_applied_sequence > through_sequence:
            raise StoreInvariantError("FINANCE_PROJECTION_SEQUENCE_INVALID")
        events = self.registry.store.events_in_sequence_range(
            checkpoint.last_applied_sequence, through_sequence
        )
        try:
            if checkpoint.last_applied_sequence == 0 and reset is not None:
                with self.registry.store.transaction() as connection:
                    reset(connection)
            for event in events:
                sequence = int(event["sequence_number"])
                with self.registry.store.transaction() as connection:
                    apply_event(connection, event)
                    connection.execute(
                        "INSERT INTO projection_registry VALUES (?,?,?,?,?,NULL) "
                        "ON CONFLICT(projection_name) DO UPDATE SET "
                        "projection_version=excluded.projection_version," 
                        "last_applied_sequence=excluded.last_applied_sequence," 
                        "status='CURRENT',updated_at=excluded.updated_at,error_code=NULL",
                        (
                            definition.projection_name,
                            definition.projection_version,
                            sequence,
                            "CURRENT",
                            datetime.now(UTC).isoformat(),
                        ),
                    )
        except Exception:
            self.registry.invalidate(definition, status="FAILED")
            raise
        return self.registry.checkpoint(definition)


__all__ = [
    "PROJECTION_INFRASTRUCTURE_VERSION",
    "ProjectionCheckpoint",
    "ProjectionDefinition",
    "ProjectionRegistry",
    "ProjectionRunner",
]
