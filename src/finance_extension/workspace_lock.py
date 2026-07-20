"""Exclusive writer lock for a local finance workspace."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class WorkspaceLockError(RuntimeError):
    """A stable, machine-readable workspace locking failure."""


@dataclass(frozen=True)
class WorkspaceLockOwner:
    pid: int
    started_at: str
    instance_id: str


def lock_path(data_dir: str | Path) -> Path:
    """Keep the lock outside the replaceable workspace directory."""
    root = Path(data_dir)
    return root.parent / f".{root.name}.workspace.lock"


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def inspect_workspace_lock(data_dir: str | Path) -> dict[str, Any]:
    path = lock_path(data_dir)
    if not path.exists():
        return {"status": "UNLOCKED", "lock_path": str(path)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        owner = WorkspaceLockOwner(
            pid=int(raw["pid"]),
            started_at=str(raw["started_at"]),
            instance_id=str(raw["instance_id"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID",
            "lock_path": str(path),
            "error_code": "FINANCE_WORKSPACE_LOCK_INVALID",
            "detail": type(exc).__name__,
        }
    active = _process_exists(owner.pid)
    return {
        "status": "LOCKED" if active else "STALE",
        "lock_path": str(path),
        **asdict(owner),
    }


class WorkspaceLock:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = lock_path(data_dir)
        self.owner: WorkspaceLockOwner | None = None

    def acquire(self) -> WorkspaceLockOwner:
        if self.owner is not None:
            return self.owner
        self.path.parent.mkdir(parents=True, exist_ok=True)
        owner = WorkspaceLockOwner(
            pid=os.getpid(),
            started_at=datetime.now(UTC).isoformat(),
            instance_id=secrets.token_hex(16),
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            status = inspect_workspace_lock(self.data_dir)
            code = (
                "FINANCE_WORKSPACE_LOCK_STALE"
                if status["status"] == "STALE"
                else "FINANCE_WORKSPACE_LOCKED"
            )
            raise WorkspaceLockError(code) from exc
        try:
            payload = json.dumps(asdict(owner), sort_keys=True, separators=(",", ":")).encode()
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.owner = owner
        return owner

    def release(self) -> None:
        if self.owner is None:
            return
        status = inspect_workspace_lock(self.data_dir)
        if status.get("instance_id") != self.owner.instance_id:
            raise WorkspaceLockError("FINANCE_WORKSPACE_LOCK_OWNERSHIP_LOST")
        self.path.unlink()
        self.owner = None


def recover_stale_workspace_lock(
    data_dir: str | Path, *, expected_instance_id: str
) -> dict[str, Any]:
    """Remove only a verified stale lock after an explicit owner match."""
    status = inspect_workspace_lock(data_dir)
    if status["status"] == "UNLOCKED":
        return {"recovered": False, **status}
    if status["status"] != "STALE":
        raise WorkspaceLockError("FINANCE_WORKSPACE_LOCK_NOT_STALE")
    if status.get("instance_id") != expected_instance_id:
        raise WorkspaceLockError("FINANCE_WORKSPACE_LOCK_OWNER_MISMATCH")
    path = lock_path(data_dir)
    path.unlink()
    return {"recovered": True, "status": "UNLOCKED", "lock_path": str(path)}


# Stable capability names used by the release contract documentation.
AcquireWorkspaceLock = WorkspaceLock.acquire
ReleaseWorkspaceLock = WorkspaceLock.release
InspectWorkspaceLock = inspect_workspace_lock
RecoverStaleWorkspaceLock = recover_stale_workspace_lock


__all__ = [
    "AcquireWorkspaceLock",
    "InspectWorkspaceLock",
    "RecoverStaleWorkspaceLock",
    "ReleaseWorkspaceLock",
    "WorkspaceLock",
    "WorkspaceLockError",
    "WorkspaceLockOwner",
    "inspect_workspace_lock",
    "lock_path",
    "recover_stale_workspace_lock",
]
