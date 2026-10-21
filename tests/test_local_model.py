from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from finance_extension.local_model import (
    LocalModelError,
    LocalModelService,
    load_model_manifest,
    verify_model_artifact,
)


class LocalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model = self.root / "model.gguf"
        self.model.write_bytes(b"synthetic-gguf-for-tests")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "model_id": "finance-assistant-small",
                    "model_version": "1.0.0",
                    "runtime": "llama.cpp",
                    "format": "GGUF",
                    "sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
                    "purpose": ["IMPORT_MAPPING", "CATEGORY_SUGGESTION"],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_verified_gguf_manifest_is_accepted(self) -> None:
        manifest = verify_model_artifact(self.model, self.manifest)

        self.assertEqual(manifest.model_id, "finance-assistant-small")
        self.assertEqual(manifest.purpose, ("IMPORT_MAPPING", "CATEGORY_SUGGESTION"))

    def test_tampered_model_is_rejected(self) -> None:
        self.model.write_bytes(b"tampered")

        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_INTEGRITY_FAILED"):
            verify_model_artifact(self.model, self.manifest)

    def test_manifest_is_strict_and_rejects_external_purpose(self) -> None:
        raw = json.loads(self.manifest.read_text(encoding="utf-8"))
        raw["purpose"] = ["INVESTMENT_ADVICE"]
        self.manifest.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_MANIFEST_INVALID"):
            load_model_manifest(self.manifest)

    def test_missing_runtime_does_not_block_finance_service(self) -> None:
        service = LocalModelService(self.root / "registered-models")
        registered = service.register(self.model, self.manifest)
        for _ in range(100):
            if service.status()["status"] != "LOADING_MODEL":
                break
            time.sleep(0.001)

        status = service.status()
        self.assertEqual(registered["status"], "REGISTERED")
        self.assertEqual(status["status"], "AI_UNAVAILABLE")
        self.assertEqual(status["error_code"], "LOCAL_MODEL_RUNTIME_UNAVAILABLE")
        self.assertFalse(status["available"])
        self.assertFalse(status["automatic_classification"])
        self.assertEqual(status["network_egress"], "DENY")

