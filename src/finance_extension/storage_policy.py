"""Runtime-path policy used before every persistence access."""

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


_CLOUD_MARKERS = (
    "dropbox",
    "onedrive",
    "google drive",
    "google-drive",
    "icloud drive",
    "mobile documents",
    "box sync",
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _git_ancestor(path: Path) -> bool:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return any((candidate / ".git").exists() for candidate in (current, *current.parents))


def _contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        if current.is_symlink():
            # macOS exposes system temporary roots such as /var through one
            # root-level compatibility link. Canonicalize that fixed OS alias;
            # links controlled deeper in a user-selected path remain forbidden.
            if index == 0:
                current = current.resolve(strict=True)
                continue
            return True
    return False


def validate_runtime_path(
    path: str | Path,
    *,
    repository_roots: Iterable[str | Path] = (),
    known_network_roots: Iterable[str | Path] = (),
) -> Path:
    raw = str(path)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise StoragePolicyViolation(
            "FINANCE_STORAGE_PATH_NOT_ABSOLUTE",
            candidate,
            "runtime storage requires an absolute local path",
        )
    if raw.startswith(("//", "\\\\")):
        raise StoragePolicyViolation(
            "FINANCE_NETWORK_STORAGE_BLOCKED",
            candidate,
            "UNC and network-share paths are forbidden",
        )
    if _contains_symlink(candidate):
        raise StoragePolicyViolation(
            "FINANCE_SYMLINK_PATH_BLOCKED",
            candidate,
            "runtime storage paths must not traverse symbolic links",
        )
    resolved = candidate.resolve(strict=False)
    if any(marker in str(resolved).casefold() for marker in _CLOUD_MARKERS):
        raise StoragePolicyViolation(
            "FINANCE_CLOUD_SYNC_STORAGE_BLOCKED", resolved, "cloud-sync storage is forbidden"
        )
    if _git_ancestor(resolved) or any(
        _within(resolved, Path(root).expanduser().resolve(strict=False))
        for root in repository_roots
    ):
        raise StoragePolicyViolation(
            "FINANCE_STORAGE_INSIDE_GIT_REPOSITORY",
            resolved,
            "runtime finance data cannot be stored in a Git worktree",
        )
    if any(
        _within(resolved, Path(root).expanduser().resolve(strict=False))
        for root in known_network_roots
    ):
        raise StoragePolicyViolation(
            "FINANCE_NETWORK_STORAGE_BLOCKED", resolved, "network mount storage is forbidden"
        )
    return resolved


__all__ = ["StoragePolicyViolation", "validate_runtime_path"]
