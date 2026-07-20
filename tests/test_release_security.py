from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from finance_extension.release_security import (
    ReleaseIntegrityError,
    create_integrity_manifest,
    sign_file,
    verify_file_signature,
    verify_integrity_manifest,
)


class ReleaseSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.schemas = self.root / "schemas"
        self.ui = self.root / "ui"
        self.schemas.mkdir()
        self.ui.mkdir()
        (self.schemas / "event.schema.json").write_text("{}")
        (self.ui / "index.js").write_text("safe")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_embedded_integrity_detects_schema_and_ui_tampering(self) -> None:
        manifest = create_integrity_manifest(self.schemas, self.ui, application_version="0.9.0")
        self.assertEqual(
            verify_integrity_manifest(manifest, self.schemas, self.ui)["status"], "VALID"
        )
        (self.ui / "index.js").write_text("tampered")
        with self.assertRaisesRegex(ReleaseIntegrityError, "FINANCE_BUNDLE_TAMPERED"):
            verify_integrity_manifest(manifest, self.schemas, self.ui)
        (self.ui / "index.js").write_text("safe")
        (self.schemas / "event.schema.json").write_text('{"tampered":true}')
        with self.assertRaisesRegex(ReleaseIntegrityError, "FINANCE_BUNDLE_TAMPERED"):
            verify_integrity_manifest(manifest, self.schemas, self.ui)

    def test_ed25519_signature_rejects_modified_artifact(self) -> None:
        artifact = self.root / "artifact.whl"
        artifact.write_bytes(b"release")
        key = Ed25519PrivateKey.generate()
        signature = sign_file(artifact, key)
        verify_file_signature(artifact, signature, key.public_key())
        artifact.write_bytes(b"modified")
        with self.assertRaisesRegex(
            ReleaseIntegrityError, "FINANCE_RELEASE_SIGNATURE_INVALID"
        ):
            verify_file_signature(artifact, signature, key.public_key())


if __name__ == "__main__":
    unittest.main()
