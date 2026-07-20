#!/usr/bin/env python3
"""Reject finance runtime data and likely secrets before they reach Git."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_ROOT = Path("extensions/finance/tests/fixtures/synthetic")
SYNTHETIC_MARKER = b"SYNTHETIC_TEST_DATA"
BLOCKED_SUFFIXES = {
    ".csv", ".tsv", ".xlsx", ".xls", ".ofx", ".qif", ".mt940", ".sta",
    ".camt", ".pdf", ".db", ".sqlite", ".sqlite3", ".backup", ".jsonl", ".log",
    ".key", ".pem", ".p12", ".pfx",
}
BLOCKED_PATH_PARTS = {".finance-data", "finance-runtime", "runtime-data"}
IGNORED_PATH_PARTS = {
    ".git",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "dist",
}
CONTENT_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "likely IBAN": re.compile(rb"\b[A-Z]{2}[0-9]{2}(?:[ ]?[A-Z0-9]){11,30}\b"),
    "likely German tax ID": re.compile(rb"(?<![0-9])[0-9]{11}(?![0-9])"),
    "bank export header": re.compile(
        rb"\x42\x75\x63\x68\x75\x6e\x67\x73\x74\x61\x67"
        rb".{0,200}Verwendungszweck.{0,200}Betrag",
        re.IGNORECASE | re.DOTALL,
    ),
    "secret assignment": re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+-]{12,}"),
}


def _is_synthetic_fixture(relative_path: Path, content: bytes) -> bool:
    try:
        relative_path.relative_to(SYNTHETIC_ROOT)
    except ValueError:
        return False
    return SYNTHETIC_MARKER in content[:4096]


def inspect_file(relative_path: Path, content: bytes) -> list[str]:
    reasons: list[str] = []
    lowered_parts = {part.casefold() for part in relative_path.parts}
    synthetic = _is_synthetic_fixture(relative_path, content)

    if BLOCKED_PATH_PARTS & lowered_parts:
        reasons.append("path is reserved for Finance runtime data")
    if relative_path.suffix.casefold() in BLOCKED_SUFFIXES and not synthetic:
        reasons.append(f"blocked Finance/runtime file type {relative_path.suffix}")

    # Scan text-sized files. Binary office/PDF/database formats are rejected by
    # suffix and do not need to be decoded.
    if len(content) <= 5_000_000 and not synthetic:
        for label, pattern in CONTENT_PATTERNS.items():
            if pattern.search(content):
                reasons.append(label)
    return reasons


def _staged_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _all_paths() -> list[Path]:
    paths: list[Path] = []
    for absolute in REPOSITORY_ROOT.rglob("*"):
        if not absolute.is_file():
            continue
        relative = absolute.relative_to(REPOSITORY_ROOT)
        if IGNORED_PATH_PARTS & set(relative.parts):
            continue
        paths.append(relative)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="inspect staged Git files")
    mode.add_argument("--all", action="store_true", help="inspect the current workspace")
    args = parser.parse_args()

    try:
        paths = _staged_paths() if args.staged else _all_paths()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Finance repository guard could not enumerate files: {exc}", file=sys.stderr)
        return 2

    violations: list[tuple[Path, str]] = []
    for relative in paths:
        absolute = REPOSITORY_ROOT / relative
        try:
            content = absolute.read_bytes()
        except OSError as exc:
            violations.append((relative, f"cannot inspect file: {exc}"))
            continue
        violations.extend((relative, reason) for reason in inspect_file(relative, content))

    if violations:
        print("Finance repository guard blocked the operation:", file=sys.stderr)
        for path, reason in violations:
            print(f"  {path}: {reason}", file=sys.stderr)
        return 1

    print(f"Finance repository guard: {len(paths)} file(s) checked, no violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
