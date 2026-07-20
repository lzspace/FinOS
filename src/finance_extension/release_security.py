"""Integrity metadata and Ed25519 signing for release artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class ReleaseIntegrityError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_manifest(root: str | Path, *, suffixes: Iterable[str] | None = None) -> dict[str, Any]:
    directory = Path(root)
    allowed = tuple(suffixes or ())
    files = {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and (not allowed or path.name.endswith(allowed))
    }
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(canonical).hexdigest(), "files": files}


def create_integrity_manifest(
    schema_root: str | Path, ui_root: str | Path, *, application_version: str
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "application_version": application_version,
        "schemas": tree_manifest(schema_root, suffixes=(".schema.json",)),
        "ui_bundle": tree_manifest(ui_root),
    }


def verify_integrity_manifest(
    manifest: dict[str, Any], schema_root: str | Path, ui_root: str | Path
) -> dict[str, Any]:
    actual_schemas = tree_manifest(schema_root, suffixes=(".schema.json",))
    actual_ui = tree_manifest(ui_root)
    checks = {
        "schemas": actual_schemas == manifest.get("schemas"),
        "ui_bundle": actual_ui == manifest.get("ui_bundle"),
    }
    if not all(checks.values()):
        raise ReleaseIntegrityError("FINANCE_BUNDLE_TAMPERED")
    return {"status": "VALID", "checks": checks}


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except Exception as exc:
        raise ReleaseIntegrityError("FINANCE_RELEASE_SIGNING_KEY_INVALID") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseIntegrityError("FINANCE_RELEASE_SIGNING_KEY_INVALID")
    return key


def public_key_pem(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sign_file(path: str | Path, key: Ed25519PrivateKey) -> Path:
    source = Path(path)
    signature_path = source.with_name(f"{source.name}.sig")
    signature_path.write_bytes(key.sign(source.read_bytes()))
    return signature_path


def verify_file_signature(
    path: str | Path, signature_path: str | Path, public_key: Ed25519PublicKey
) -> None:
    try:
        public_key.verify(Path(signature_path).read_bytes(), Path(path).read_bytes())
    except InvalidSignature as exc:
        raise ReleaseIntegrityError("FINANCE_RELEASE_SIGNATURE_INVALID") from exc


__all__ = [
    "ReleaseIntegrityError",
    "create_integrity_manifest",
    "load_private_key",
    "public_key_pem",
    "sha256_file",
    "sign_file",
    "tree_manifest",
    "verify_file_signature",
    "verify_integrity_manifest",
]
