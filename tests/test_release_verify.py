from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from finance_extension.release_security import (
    ReleaseIntegrityError,
    public_key_pem,
    sha256_file,
    sign_file,
)
from finance_extension.release_verify import verify_release


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        public = public_key_pem(self.key)
        self.public_path = self.root / "release-public-key.pem"
        self.public_path.write_bytes(public)
        self.fingerprint = hashlib.sha256(public).hexdigest()
        self.wheel_name = "agent_os_finance-1.0.0-py3-none-any.whl"
        files = {
            self.wheel_name: b"wheel",
            "contract-catalog.json": json.dumps(
                {"schemas": {"a": {}}, "sha256": {"a": "0" * 64}}
            ).encode(),
            "ui-bundle.json": json.dumps({"sha256": "1" * 64, "files": {}}).encode(),
            "cyclonedx-sbom.json": b"{}",
            "spdx-sbom.json": b"{}",
        }
        for name, content in files.items():
            (self.root / name).write_bytes(content)
        manifest = {
            "format_version": 1,
            "product": "agent-os-finance",
            "version": "1.0.0",
            "artifact": {"name": self.wheel_name},
            "wheel_sha256": sha256_file(self.root / self.wheel_name),
            "ui_bundle_sha256": "1" * 64,
            "schema_catalog_sha256": "2" * 64,
            "contract_catalog_sha256": sha256_file(self.root / "contract-catalog.json"),
            "integrity": {"schemas": {"sha256": "2" * 64}},
            "sbom": {
                "cyclonedx-sbom.json": sha256_file(self.root / "cyclonedx-sbom.json"),
                "spdx-sbom.json": sha256_file(self.root / "spdx-sbom.json"),
            },
            "signing": {"public_key_sha256": self.fingerprint},
            "offline_verified": True,
        }
        manifest_path = self.root / "release-manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        sign_file(manifest_path, self.key, self.root / "release-manifest.sig")
        for name in files:
            sign_file(self.root / name, self.key)
        checksummed = [manifest_path, self.public_path, *(self.root / name for name in files)]
        checksums = self.root / "checksums.sha256"
        checksums.write_text(
            "".join(
                f"{sha256_file(path)}  {path.name}\n" for path in sorted(checksummed)
            )
        )
        sign_file(checksums, self.key)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_release_and_intended_fingerprint_are_verified(self) -> None:
        result = verify_release(
            self.root,
            self.public_path,
            expected_fingerprint=self.fingerprint,
        )
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["version"], "1.0.0")

    def test_modified_artifact_and_wrong_trust_anchor_are_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseIntegrityError, "FINANCE_RELEASE_TRUST_MISMATCH"):
            verify_release(self.root, self.public_path, expected_fingerprint="0" * 64)
        (self.root / self.wheel_name).write_bytes(b"tampered")
        with self.assertRaisesRegex(
            ReleaseIntegrityError, "FINANCE_RELEASE_ARTIFACT_HASH_MISMATCH"
        ):
            verify_release(self.root, self.public_path)


if __name__ == "__main__":
    unittest.main()
