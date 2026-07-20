"""Offline verification of a signed Agent OS Finance release directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .release_security import ReleaseIntegrityError, sha256_file, verify_file_signature


_CHECKSUM_LINE = re.compile(r"^([a-f0-9]{64})  ([A-Za-z0-9_.-]+)$")


def _load_public_key(path: Path) -> tuple[Ed25519PublicKey, str]:
    try:
        content = path.read_bytes()
        key = serialization.load_pem_public_key(content)
    except Exception as exc:
        raise ReleaseIntegrityError("FINANCE_RELEASE_PUBLIC_KEY_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ReleaseIntegrityError("FINANCE_RELEASE_PUBLIC_KEY_INVALID")
    return key, hashlib.sha256(content).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("FINANCE_RELEASE_MANIFEST_INVALID") from exc
    if value.get("product") != "agent-os-finance" or value.get("format_version") != 1:
        raise ReleaseIntegrityError("FINANCE_RELEASE_MANIFEST_INVALID")
    return value


def verify_release(
    release_directory: str | Path,
    public_key_path: str | Path,
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    root = Path(release_directory).resolve(strict=True)
    key, fingerprint = _load_public_key(Path(public_key_path).resolve(strict=True))
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise ReleaseIntegrityError("FINANCE_RELEASE_TRUST_MISMATCH")
    manifest_path = root / "release-manifest.json"
    verify_file_signature(manifest_path, root / "release-manifest.sig", key)
    manifest = _load_manifest(manifest_path)
    if manifest.get("signing", {}).get("public_key_sha256") != fingerprint:
        raise ReleaseIntegrityError("FINANCE_RELEASE_TRUST_MISMATCH")

    checksum_path = root / "checksums.sha256"
    verify_file_signature(checksum_path, root / "checksums.sha256.sig", key)
    checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        match = _CHECKSUM_LINE.fullmatch(line)
        if not match or match.group(2) in checksums:
            raise ReleaseIntegrityError("FINANCE_RELEASE_CHECKSUMS_INVALID")
        checksums[match.group(2)] = match.group(1)
    for name, digest in checksums.items():
        path = root / name
        if path.parent != root or not path.is_file() or sha256_file(path) != digest:
            raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")

    wheel_name = str(manifest.get("artifact", {}).get("name", ""))
    signed_names = [
        wheel_name,
        "contract-catalog.json",
        "ui-bundle.json",
        "cyclonedx-sbom.json",
        "spdx-sbom.json",
    ]
    for name in signed_names:
        if not name or Path(name).name != name:
            raise ReleaseIntegrityError("FINANCE_RELEASE_MANIFEST_INVALID")
        verify_file_signature(root / name, root / f"{name}.sig", key)
    if checksums.get(wheel_name) != manifest.get("wheel_sha256"):
        raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")
    ui_bundle = json.loads((root / "ui-bundle.json").read_text(encoding="utf-8"))
    if ui_bundle.get("sha256") != manifest.get("ui_bundle_sha256"):
        raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")
    schema_catalog = json.loads((root / "contract-catalog.json").read_text(encoding="utf-8"))
    if not schema_catalog.get("schemas") or (
        manifest.get("integrity", {}).get("schemas", {}).get("sha256")
        != manifest.get("schema_catalog_sha256")
    ):
        raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")
    for name, digest in manifest.get("sbom", {}).items():
        if checksums.get(name) != digest:
            raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")
    acceptance = manifest.get("acceptance")
    if acceptance is not None:
        acceptance_path = root / "acceptance-report.json"
        verify_file_signature(
            acceptance_path, root / "acceptance-report.json.sig", key
        )
        if checksums.get(acceptance_path.name) != acceptance.get("report_sha256"):
            raise ReleaseIntegrityError("FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH")
    if manifest.get("security_review", {}).get("release_blocked"):
        raise ReleaseIntegrityError("FINANCE_RELEASE_SECURITY_GATE_FAILED")
    return {
        "status": "VALID",
        "product": manifest["product"],
        "version": manifest["version"],
        "public_key_sha256": fingerprint,
        "artifact_count": len(checksums),
        "offline_verified": manifest["offline_verified"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_directory")
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--expected-fingerprint")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            verify_release(
                args.release_directory,
                args.public_key,
                expected_fingerprint=args.expected_fingerprint,
            ),
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
