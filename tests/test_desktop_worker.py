from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from cryptography.fernet import Fernet

from finance_extension.desktop_worker import (
    DESKTOP_CONTRACT_VERSION,
    DesktopRequestHandler,
    _recover_interrupted_desktop_session,
    serve,
)
from finance_extension.workspace_lock import inspect_workspace_lock, lock_path


FIXTURE = (
    Path(__file__).parents[1]
    / "extensions/finance/tests/fixtures/synthetic/multi-account-december-2024.csv"
)


class _FakeApplication:
    def query(self, name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return {"name": name, "payload": payload or {}}

    def command(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        return {"name": name, "payload": payload}


class DesktopWorkerContractTests(unittest.TestCase):
    def request(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        version: str = DESKTOP_CONTRACT_VERSION,
    ) -> dict[str, object]:
        return {
            "request_id": f"req_{uuid4().hex}",
            "operation": operation,
            "contract_version": version,
            "payload": payload,
        }

    def test_dispatches_structured_application_requests(self) -> None:
        handler = DesktopRequestHandler(_FakeApplication())  # type: ignore[arg-type]
        request = self.request(
            "query",
            {"name": "GetStartupStatus", "payload": {}},
        )
        response = handler.handle(request)
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["operation"], "query")
        self.assertEqual(response["contract_version"], DESKTOP_CONTRACT_VERSION)
        self.assertEqual(response["status"], "OK")
        self.assertEqual(response["result"], {"name": "GetStartupStatus", "payload": {}})

    def test_rejects_contract_incompatibility_without_dispatch(self) -> None:
        handler = DesktopRequestHandler(_FakeApplication())  # type: ignore[arg-type]
        response = handler.handle(self.request("query", {}, version="2.0.0"))
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(response["error"]["code"], "DESKTOP_CONTRACT_INCOMPATIBLE")

    def test_invalid_input_never_echoes_financial_payload(self) -> None:
        from io import StringIO

        source = StringIO('{"amount":"987654.32","account":"secret"}\n')
        destination = StringIO()
        serve(DesktopRequestHandler(_FakeApplication()), source, destination)  # type: ignore[arg-type]
        response = destination.getvalue()
        self.assertNotIn("987654.32", response)
        self.assertNotIn("secret", response)
        self.assertIn("DESKTOP_PROTOCOL_INVALID", response)

    def test_startup_recovers_only_a_verified_stale_workspace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "store"
            path = lock_path(data_dir)
            path.write_text(
                json.dumps(
                    {
                        "pid": 2_147_483_647,
                        "started_at": "2026-07-25T10:00:00+00:00",
                        "instance_id": "stale-desktop-session",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(inspect_workspace_lock(data_dir)["status"], "STALE")
            self.assertTrue(_recover_interrupted_desktop_session(data_dir))
            self.assertEqual(inspect_workspace_lock(data_dir)["status"], "UNLOCKED")
            self.assertFalse(_recover_interrupted_desktop_session(data_dir))


class DesktopImportEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="finance-desktop-e2e-")
        self.data_dir = Path(self.temporary.name) / "data"
        self.fixture = Path(self.temporary.name) / "synthetic-export.csv"
        self.fixture.write_bytes(FIXTURE.read_bytes())
        self.key = Fernet.generate_key().decode("ascii")
        self.process: subprocess.Popen[str] | None = None
        self.start()

    def tearDown(self) -> None:
        self.stop()
        self.temporary.cleanup()

    def start(self) -> None:
        environment = {
            **os.environ,
            "FINANCE_DESKTOP_E2E": "1",
            "FINANCE_TEST_KEY": self.key,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        }
        bundled_worker = os.environ.get("FINANCE_E2E_WORKER")
        command = (
            [bundled_worker]
            if bundled_worker
            else [sys.executable, "-m", "finance_extension.desktop_worker"]
        )
        self.process = subprocess.Popen(
            [
                *command,
                "--data-dir",
                str(self.data_dir),
                "--e2e-test-key-env",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=environment,
        )

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is None:
            try:
                self.call("shutdown", {})
                self.process.wait(timeout=10)
            except (BrokenPipeError, TimeoutError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None

    def call(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        assert self.process and self.process.stdin and self.process.stdout
        request = {
            "request_id": f"req_{uuid4().hex}",
            "operation": operation,
            "contract_version": DESKTOP_CONTRACT_VERSION,
            "payload": payload,
        }
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["status"], "OK", response)
        return response["result"]

    def command(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        return self.call("command", {"name": name, "payload": payload})

    def query(self, name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return self.call("query", {"name": name, "payload": payload or {}})

    def test_full_import_reconciliation_relations_and_restart(self) -> None:
        runtime = self.call("runtime_status", {})
        self.assertEqual(runtime["data"]["status"], "READY")
        accounts: dict[str, str] = {}
        for account_type, display_name in (
            ("CHECKING", "Synthetisches Giro"),
            ("SAVINGS", "Synthetisches Tagesgeld"),
            ("BROKERAGE", "Synthetisches Depot"),
        ):
            created = self.command(
                "CreateAccount",
                {
                    "display_name": display_name,
                    "account_type": account_type,
                    "institution": "SYNTHETIC_BANK",
                    "currency": "EUR",
                    "opened_at": "2024-01-01",
                },
            )
            accounts[account_type] = str(created["result"])

        analyzed = self.command(
            "AnalyzeImportFile",
            {
                "source_file_path": str(self.fixture),
                "requested_profile": "GermanMultiAccountCsvV1",
            },
        )["result"]
        self.command(
            "MapImportSections",
            {
                "analysis_id": analyzed["analysis_id"],
                "section_mappings": [
                    {
                        "section_id": section["section_id"],
                        "account_id": accounts[section["section_type"]],
                        "action": "USE_EXISTING_ACCOUNT",
                    }
                    for section in analyzed["sections"]
                ],
            },
        )
        for account_type, opening, closing in (
            ("CHECKING", "2000.00", "2246.15"),
            ("SAVINGS", "5000.00", "4999.00"),
        ):
            account_id = accounts[account_type]
            self.command(
                "RecordOpeningBalance",
                {
                    "account_id": account_id,
                    "balance_date": "2024-11-30",
                    "booked_balance": opening,
                    "available_balance": None,
                    "currency": "EUR",
                    "source": "MANUAL_ENTRY",
                    "confirmation": True,
                    "comment": "synthetic e2e",
                },
            )
            self.command(
                "RecordClosingBalance",
                {
                    "account_id": account_id,
                    "balance_date": "2024-12-31",
                    "booked_balance": closing,
                    "available_balance": None,
                    "currency": "EUR",
                    "source": "BANK_EXPORT",
                    "confirmation": True,
                },
            )
        self.command(
            "ConfirmEmptyOpeningSecurityPositions",
            {
                "account_id": accounts["BROKERAGE"],
                "valuation_date": "2024-11-30",
            },
        )
        validated = self.command(
            "ImportMappedSections",
            {
                "analysis_id": analyzed["analysis_id"],
                "parser_profile": "GermanMultiAccountCsvV1",
                "parser_version": "1.0.0",
                "import_mode": "VALIDATE_ONLY",
            },
        )["result"]
        self.assertEqual(validated["status"], "VALIDATED")
        imported = self.command(
            "ImportMappedSections",
            {
                "analysis_id": analyzed["analysis_id"],
                "parser_profile": "GermanMultiAccountCsvV1",
                "parser_version": "1.0.0",
                "import_mode": "IMPORT_NEW",
            },
        )["result"]
        self.assertEqual(imported["status"], "COMPLETED")

        self.command(
            "RecordClosingSecurityPosition",
            {
                "account_id": accounts["BROKERAGE"],
                "valuation_date": "2024-12-31",
                "security_identifier_type": "WKN",
                "security_identifier": "ABC123",
                "security_name": "Synthetischer Fonds",
                "quantity": "10",
                "confirmation": True,
            },
        )
        checking = self.command(
            "ReconcileImportedPeriodBalance",
            {
                "account_id": accounts["CHECKING"],
                "section_id": next(
                    section["section_id"]
                    for section in analyzed["sections"]
                    if section["section_type"] == "CHECKING"
                ),
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        savings = self.command(
            "ReconcileImportedPeriodBalance",
            {
                "account_id": accounts["SAVINGS"],
                "section_id": next(
                    section["section_id"]
                    for section in analyzed["sections"]
                    if section["section_type"] == "SAVINGS"
                ),
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        positions = self.command(
            "ReconcileImportedPeriodPositions",
            {
                "account_id": accounts["BROKERAGE"],
                "section_id": next(
                    section["section_id"]
                    for section in analyzed["sections"]
                    if section["section_type"] == "BROKERAGE"
                ),
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        relations = self.command("DetectInvestmentFundingRelations", {})["result"]
        self.assertEqual(checking["status"], "MATCHED")
        self.assertEqual(savings["status"], "DIFFERENCE")
        self.assertEqual(positions["status"], "MATCHED")
        self.assertGreaterEqual(relations, 1)

        export_id = analyzed["export_id"]
        self.stop()
        self.start()
        resumed = self.query("GetImportWizardState", {"export_id": export_id})["data"]
        result = self.query("GetImportExecutionResult", {"export_id": export_id})["data"]
        self.assertEqual(resumed["status"], "COMPLETED")
        self.assertEqual(result["normalized_transaction_count"], 4)
        self.assertTrue(result["relations"])
