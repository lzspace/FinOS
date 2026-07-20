"""Pure, fail-closed validation for Finance Extension runtime paths.

This module never creates a directory or opens a network connection. Callers
must validate a path immediately before every runtime write, including exports,
temporary files, caches and backups.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class StoragePolicyViolation(ValueError):
    code: str
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.code}: {self.reason} ({self.path})"


_CLOUD_PATH_MARKERS = (
    "dropbox",
    "onedrive",
    "google drive",
    "google-drive",
    "icloud drive",
    "mobile documents",
    "box sync",
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _has_git_ancestor(path: Path) -> bool:
    current = _nearest_existing_parent(path)
    return any((candidate / ".git").exists() for candidate in (current, *current.parents))


def validate_runtime_path(
    path: str | Path,
    *,
    repository_roots: Iterable[str | Path] = (),
    known_network_roots: Iterable[str | Path] = (),
) -> Path:
    """Return a canonical path or raise a stable Finance policy violation.

    `known_network_roots` lets the host provide mount information obtained from
    the operating system. Unknown mount types must be resolved by the host
    before it treats this result as authorization to write.
    """

    raw_path = str(path)
    candidate = Path(path).expanduser()

    if not candidate.is_absolute():
        raise StoragePolicyViolation(
            "FINANCE_STORAGE_PATH_NOT_ABSOLUTE",
            candidate,
            "runtime storage requires an absolute local path",
        )

    if raw_path.startswith(("//", "\\\\")):
        raise StoragePolicyViolation(
            "FINANCE_NETWORK_STORAGE_BLOCKED",
            candidate,
            "UNC and network-share paths are forbidden",
        )

    resolved = candidate.resolve(strict=False)
    normalized = str(resolved).casefold()

    if any(marker in normalized for marker in _CLOUD_PATH_MARKERS):
        raise StoragePolicyViolation(
            "FINANCE_CLOUD_SYNC_STORAGE_BLOCKED",
            resolved,
            "the path appears to be managed by a cloud-sync provider",
        )

    if _has_git_ancestor(resolved):
        raise StoragePolicyViolation(
            "FINANCE_STORAGE_INSIDE_GIT_REPOSITORY",
            resolved,
            "runtime finance data cannot be stored in a Git worktree",
        )

    for root in repository_roots:
        resolved_root = Path(root).expanduser().resolve(strict=False)
        if _is_within(resolved, resolved_root):
            raise StoragePolicyViolation(
                "FINANCE_STORAGE_INSIDE_GIT_REPOSITORY",
                resolved,
                f"runtime path is inside configured repository root {resolved_root}",
            )

    for root in known_network_roots:
        resolved_root = Path(root).expanduser().resolve(strict=False)
        if _is_within(resolved, resolved_root):
            raise StoragePolicyViolation(
                "FINANCE_NETWORK_STORAGE_BLOCKED",
                resolved,
                f"runtime path is inside network mount {resolved_root}",
            )

    return resolved

