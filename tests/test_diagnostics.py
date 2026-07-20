from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from finance_extension.crypto import StaticKeyProvider
from finance_extension.application import FinanceApplicationService
from finance_extension.diagnostics import DiagnosticInvariantError, LocalDiagnosticRecorder
from finance_extension.error_catalog import error_definition
from finance_extension.store import LocalFinanceStore


class DiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.key = StaticKeyProvider(Fernet.generate_key())
        self.recorder = LocalDiagnosticRecorder(self.root / "data", self.key)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_diagnostic_log_is_encrypted_and_allowlisted(self) -> None:
        self.recorder.record(
            operation_kind="COMMAND",
            operation_name="CreateBackup",
            duration_ms=12,
            event_count=4,
            component_status="PASSED",
        )
        ciphertext = self.recorder.path.read_bytes()
        self.assertNotIn(b"CreateBackup", ciphertext)
        self.assertEqual(self.recorder.records()[0]["event_count"], 4)
        with self.assertRaisesRegex(
            DiagnosticInvariantError, "FINANCE_DIAGNOSTIC_FIELD_FORBIDDEN"
        ):
            self.recorder.record(amount="10.00")

    def test_export_is_explicit_and_plaintext_contains_only_safe_metadata(self) -> None:
        self.recorder.record(component="STORE", component_status="PASSED")
        destination = self.root / "exports" / "diagnostics.json"
        with self.assertRaisesRegex(
            DiagnosticInvariantError, "FINANCE_DIAGNOSTIC_EXPORT_CONFIRMATION_REQUIRED"
        ):
            self.recorder.export(destination, confirmed=False)
        result = self.recorder.export(destination, confirmed=True)
        self.assertTrue(result["exported"])
        self.assertNotIn("amount", destination.read_text())

    def test_error_catalog_never_allows_payload_context(self) -> None:
        definition = error_definition("FINANCE_STORE_DECRYPTION_FAILED")
        self.assertEqual(definition["severity"], "BLOCKING")
        self.assertEqual(
            definition["technical_context_policy"], "ALLOWLISTED_METADATA_ONLY"
        )

    def test_application_export_requires_explicit_true_confirmation(self) -> None:
        store = LocalFinanceStore(self.root / "store", self.key).open()
        try:
            app = FinanceApplicationService(store, diagnostic_recorder=self.recorder)
            destination = self.root / "export" / "diagnostics.json"
            with self.assertRaisesRegex(
                DiagnosticInvariantError,
                "FINANCE_DIAGNOSTIC_EXPORT_CONFIRMATION_REQUIRED",
            ):
                app.command(
                    "ExportDiagnostics",
                    {"destination_path": str(destination), "confirmed": False},
                )
            app.command(
                "ExportDiagnostics",
                {"destination_path": str(destination), "confirmed": True},
            )
            self.assertTrue(destination.exists())
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
