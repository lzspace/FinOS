from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from finance_extension.ai_import_mapping import (
    analyze_unknown_import_format,
    build_import_format_fingerprint,
    confirm_import_mapping,
    get_import_format_status,
    list_import_profiles,
    validate_import_mapping,
)
from finance_extension.application import FinanceApplicationService
from finance_extension.crypto import StaticKeyProvider
from finance_extension.local_model import LocalModelError
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.review_inbox import review_inbox
from finance_extension.store import LocalFinanceStore


CSV = """Datum;Valuta;Text;Betrag;Währung
01.08.2024;01.08.2024;Miete;-850,00;EUR
02.08.2024;02.08.2024;Gehalt;3200,00;EUR
"""


def mapping() -> dict[str, Any]:
    return {
        "section_type": "CHECKING",
        "field_mappings": [
            {"source_column": "Datum", "target_field": "booking_date", "confidence": 0.99},
            {"source_column": "Valuta", "target_field": "value_date", "confidence": 0.98},
            {"source_column": "Text", "target_field": "description", "confidence": 0.95},
            {"source_column": "Betrag", "target_field": "amount", "confidence": 0.99},
            {"source_column": "Währung", "target_field": "currency", "confidence": 0.99},
        ],
        "transformations": [
            {"target_field": "booking_date", "type": "PARSE_DATE", "parameters": {"format": "DD.MM.YYYY"}},
            {"target_field": "value_date", "type": "PARSE_DATE", "parameters": {"format": "DD.MM.YYYY"}},
            {"target_field": "amount", "type": "PARSE_DECIMAL", "parameters": {"decimal_separator": ",", "thousands_separator": "."}},
        ],
        "constants": {},
        "ignored_columns": [],
        "confidence": 0.97,
        "warnings": [],
    }


class MockLocalModelProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request: Any = None

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "import_mapping": True,
            "model": {
                "model_id": "test-model",
                "model_version": "1.0.0",
                "runtime_version": "llama.cpp b11794-f84909536",
                "structured_output_backend": "GBNF",
                "inference_policy_version": "1.1.0",
            },
        }

    def infer_structured(self, _request: object) -> dict[str, Any]:
        self.calls += 1
        self.last_request = _request
        return mapping()

    def start_async(self) -> None:
        return None


class UnavailableLocalModel:
    def status(self) -> dict[str, Any]:
        return {"available": False, "import_mapping": False, "model": None}

    def start_async(self) -> None:
        return None


FakeLocalModel = MockLocalModelProvider


class SemanticallyInvalidLocalModel(MockLocalModelProvider):
    def infer_structured(self, _request: object) -> dict[str, Any]:
        self.calls += 1
        invalid = mapping()
        invalid["field_mappings"][0]["source_column"] = "UnknownSource"
        return invalid


class GrammarFailingLocalModel(MockLocalModelProvider):
    def infer_structured(self, _request: object) -> dict[str, Any]:
        self.calls += 1
        raise LocalModelError("LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED")


class AiImportMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "unknown.csv"
        self.source.write_text(CSV, encoding="utf-8")
        self.store_key = StaticKeyProvider(Fernet.generate_key())
        self.store = LocalFinanceStore(self.root / "data", self.store_key).open()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_valid_proposal_is_dry_run_and_requires_confirmation(self) -> None:
        model = FakeLocalModel()
        result = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )

        self.assertEqual(result["status"], "PROPOSAL_READY")
        self.assertEqual(result["proposal"]["validation"]["status"], "VALID")
        self.assertEqual(result["proposal"]["validation"]["rows_valid"], 2)
        self.assertEqual(model.calls, 1)
        self.assertNotIn("Miete", model.last_request.prompt)
        self.assertNotIn("Gehalt", model.last_request.prompt)
        self.assertNotIn(CSV, model.last_request.prompt)
        self.assertEqual(len(self.store.events("ImportProfileCreated")), 0)
        self.assertEqual(review_inbox(self.store)["items_by_type"]["FORMAT_CHANGE"], 1)

        confirmed = confirm_import_mapping(
            self.store,
            result["proposal"]["proposal_id"],
            mapping(),
            validation_result_id=result["proposal"]["validation"]["validation_result_id"],
        )
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(review_inbox(self.store)["items_by_type"]["FORMAT_CHANGE"], 0)
        self.assertEqual(len(list_import_profiles(self.store)), 1)
        confirmed_event = self.store.events("ImportMappingConfirmed")[-1]["payload"]
        profile_event = self.store.events("ImportProfileCreated")[-1]["payload"]
        for payload in (confirmed_event, profile_event):
            self.assertEqual(payload["model_id"], "test-model")
            self.assertEqual(payload["model_version"], "1.0.0")
            self.assertEqual(
                payload["runtime_version"], "llama.cpp b11794-f84909536"
            )
            self.assertEqual(payload["structured_output_backend"], "GBNF")
            self.assertEqual(payload["inference_policy_version"], "1.1.0")

    def test_retry_reuses_pending_proposal_without_second_inference(self) -> None:
        model = FakeLocalModel()
        first = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )
        retried = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )

        self.assertEqual(
            retried["proposal"]["proposal_id"], first["proposal"]["proposal_id"]
        )
        self.assertTrue(retried["reused_pending_proposal"])
        self.assertEqual(model.calls, 1)

    def test_exact_profile_is_reused_without_ai(self) -> None:
        model = FakeLocalModel()
        first = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )
        confirm_import_mapping(
            self.store,
            first["proposal"]["proposal_id"],
            mapping(),
            validation_result_id=first["proposal"]["validation"]["validation_result_id"],
        )
        self.store.close()
        self.store = LocalFinanceStore(self.root / "data", self.store_key).open()
        second_file = self.root / "next-month.csv"
        second_file.write_text(CSV.replace("01.08.2024", "01.09.2024").replace("02.08.2024", "02.09.2024"), encoding="utf-8")

        second = analyze_unknown_import_format(
            self.store, model, second_file, bank_identifier="example-bank"
        )

        self.assertEqual(second["status"], "KNOWN_EXACT")
        self.assertFalse(second["ai_used"])
        self.assertEqual(model.calls, 1)

    def test_unavailable_ai_returns_manual_fallback(self) -> None:
        result = analyze_unknown_import_format(
            self.store, UnavailableLocalModel(), self.source, bank_identifier="example-bank"
        )

        self.assertEqual(result["status"], "MANUAL_MAPPING_REQUIRED")
        self.assertTrue(result["manual_mapping_available"])
        self.assertEqual(result["ai_error_code"], "LOCAL_AI_UNAVAILABLE")
        status = get_import_format_status(self.store, result["analysis_id"])
        self.assertEqual(status["status"], "UNKNOWN")

    def test_semantically_invalid_ai_mapping_falls_back_to_manual_mapping(self) -> None:
        result = analyze_unknown_import_format(
            self.store,
            SemanticallyInvalidLocalModel(),
            self.source,
            bank_identifier="example-bank",
        )

        self.assertEqual(result["status"], "MANUAL_MAPPING_REQUIRED")
        self.assertEqual(
            result["ai_error_code"], "LOCAL_AI_SEMANTIC_VALIDATION_FAILED"
        )
        self.assertEqual(result["proposal"]["model_id"], "manual")

    def test_grammar_failure_falls_back_to_manual_mapping(self) -> None:
        result = analyze_unknown_import_format(
            self.store,
            GrammarFailingLocalModel(),
            self.source,
            bank_identifier="example-bank",
        )

        self.assertEqual(result["status"], "MANUAL_MAPPING_REQUIRED")
        self.assertEqual(
            result["ai_error_code"], "LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED"
        )
        self.assertEqual(result["proposal"]["model_id"], "manual")

    def test_proposal_audits_runtime_backend_and_additive_policy_version(self) -> None:
        result = analyze_unknown_import_format(
            self.store, MockLocalModelProvider(), self.source, bank_identifier="example-bank"
        )
        proposal = result["proposal"]

        self.assertEqual(proposal["runtime_version"], "llama.cpp b11794-f84909536")
        self.assertEqual(proposal["structured_output_backend"], "GBNF")
        self.assertEqual(proposal["inference_policy_version"], "1.1.0")

    def test_validator_rejects_unknown_columns_and_duplicate_targets(self) -> None:
        invalid = mapping()
        invalid["field_mappings"][0]["source_column"] = "Erfunden"
        invalid["field_mappings"][1]["target_field"] = "amount"
        result = validate_import_mapping(
            invalid,
            columns=["Datum", "Valuta", "Text", "Betrag", "Währung"],
            rows=[{"Datum": "01.08.2024", "Valuta": "01.08.2024", "Text": "X", "Betrag": "1,00", "Währung": "EUR"}],
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("IMPORT_MAPPING_SOURCE_COLUMN_UNKNOWN", result["errors"])
        self.assertIn("IMPORT_MAPPING_TARGET_FIELD_DUPLICATE", result["errors"])

    def test_validator_rejects_missing_fields_and_unapproved_transformations(self) -> None:
        invalid = mapping()
        invalid["field_mappings"] = [
            item for item in invalid["field_mappings"] if item["target_field"] != "amount"
        ]
        invalid["transformations"] = [
            {"target_field": "description", "type": "EXECUTE_CODE", "parameters": {}}
        ]
        result = validate_import_mapping(
            invalid,
            columns=["Datum", "Valuta", "Text", "Betrag", "Währung"],
            rows=[{"Datum": "01.08.2024", "Valuta": "01.08.2024", "Text": "X", "Betrag": "1,00", "Währung": "EUR"}],
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("IMPORT_MAPPING_SCHEMA_INVALID", result["errors"])
        self.assertIn("IMPORT_MAPPING_REQUIRED_FIELD_MISSING", result["errors"])
        self.assertIn("IMPORT_MAPPING_TRANSFORMATION_INVALID", result["errors"])

    def test_full_dry_run_reports_date_decimal_and_required_value_errors(self) -> None:
        rows = [
            {"Datum": "31-08-2024", "Valuta": "31.08.2024", "Text": "X", "Betrag": "nicht-zahl", "Währung": ""},
            {"Datum": "30.08.2024", "Valuta": "30.08.2024", "Text": "Y", "Betrag": "10,00", "Währung": "EUR"},
        ]
        result = validate_import_mapping(
            mapping(),
            columns=["Datum", "Valuta", "Text", "Betrag", "Währung"],
            rows=rows,
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["rows_total"], 2)
        self.assertEqual(result["rows_valid"], 1)
        self.assertEqual(result["rows_invalid"], 1)
        self.assertEqual(result["date_parse_errors"], 1)
        self.assertEqual(result["decimal_parse_errors"], 1)
        self.assertEqual(result["missing_required_values"], 1)

    def test_fingerprint_ignores_values_but_detects_column_change(self) -> None:
        base = {
            "encoding": "utf-8", "delimiter": ";", "quote_character": '"',
            "section_title": "Import", "columns": ["Datum", "Betrag"],
            "date_shapes": ["DD.MM.YYYY"], "decimal_shapes": ["DE"],
        }
        same = {**base, "rows": [{"Datum": "x", "Betrag": "1"}]}
        changed = {**base, "columns": ["Buchungstag", "Betrag"]}

        self.assertEqual(
            build_import_format_fingerprint(base)["fingerprint_id"],
            build_import_format_fingerprint(same)["fingerprint_id"],
        )
        self.assertNotEqual(
            build_import_format_fingerprint(base)["fingerprint_id"],
            build_import_format_fingerprint(changed)["fingerprint_id"],
        )

    def test_relevant_format_change_is_classified_and_does_not_reuse_profile(self) -> None:
        model = FakeLocalModel()
        first = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )
        confirm_import_mapping(
            self.store,
            first["proposal"]["proposal_id"],
            mapping(),
            validation_result_id=first["proposal"]["validation"]["validation_result_id"],
        )
        changed = self.root / "changed.csv"
        changed.write_text(CSV.replace("Datum;", "Buchungstag;"), encoding="utf-8")

        result = analyze_unknown_import_format(
            self.store, model, changed, bank_identifier="example-bank"
        )
        status = get_import_format_status(self.store, result["analysis_id"])

        self.assertEqual(result["format_status"], "FORMAT_CHANGED")
        self.assertEqual(status["status"], "FORMAT_CHANGED")
        self.assertIn("COLUMN_RENAMED", status["status"] and next(
            event["payload"]["change_types"]
            for event in self.store.events("ImportFormatChangeDetected")
            if event["payload"]["analysis_id"] == result["analysis_id"]
        ))
        self.assertEqual(model.calls, 2)

    def test_explicit_additive_compatibility_rule_reuses_profile_after_full_dry_run(self) -> None:
        model = FakeLocalModel()
        first = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )
        confirm_import_mapping(
            self.store,
            first["proposal"]["proposal_id"],
            mapping(),
            validation_result_id=first["proposal"]["validation"]["validation_result_id"],
            compatibility_rules=["ALLOW_ADDITIONAL_COLUMNS"],
        )
        changed = self.root / "additive.csv"
        changed.write_text(
            CSV.replace("Währung\n", "Währung;Hinweis\n").replace(";EUR\n", ";EUR;optional\n"),
            encoding="utf-8",
        )

        result = analyze_unknown_import_format(
            self.store, model, changed, bank_identifier="example-bank"
        )
        status = get_import_format_status(self.store, result["analysis_id"])

        self.assertEqual(result["status"], "KNOWN_COMPATIBLE")
        self.assertEqual(status["status"], "KNOWN_COMPATIBLE")
        self.assertFalse(result["ai_used"])
        self.assertEqual(result["validation"]["rows_valid"], 2)
        self.assertEqual(model.calls, 1)

    def test_application_commands_and_queries_keep_file_path_in_host_boundary(self) -> None:
        model = FakeLocalModel()
        application = FinanceApplicationService(
            self.store,
            local_model_service=model,
            import_file_resolver=lambda reference: self.source
            if reference == "opaque-file-ref"
            else "",
        )

        analyzed = application.command(
            "AnalyzeUnknownImportFormat",
            {"source_file_reference": "opaque-file-ref", "bank_identifier": "example-bank"},
        )["result"]
        proposal = analyzed["proposal"]
        validation = application.command(
            "ValidateImportMapping",
            {"proposal_id": proposal["proposal_id"], "mapping": mapping()},
        )["result"]
        confirmed = application.command(
            "ConfirmImportMapping",
            {
                "proposal_id": proposal["proposal_id"],
                "final_mapping": mapping(),
                "validation_result_id": validation["validation_result_id"],
            },
        )["result"]
        queried = application.query(
            "GetImportProfile", {"profile_id": confirmed["profile"]["profile_id"]}
        )

        self.assertEqual(queried["state"], "READY")
        self.assertEqual(queried["data"]["source"], "USER_CONFIRMED_AI")
        self.assertNotIn(str(self.source), str(queried))

    def test_backup_restore_preserves_profile_and_mapping_history(self) -> None:
        model = FakeLocalModel()
        first = analyze_unknown_import_format(
            self.store, model, self.source, bank_identifier="example-bank"
        )
        confirmed = confirm_import_mapping(
            self.store,
            first["proposal"]["proposal_id"],
            mapping(),
            validation_result_id=first["proposal"]["validation"]["validation_result_id"],
        )
        backup_key = StaticKeyProvider(Fernet.generate_key())
        backup = create_backup(self.store, backup_key, self.root / "backups")
        changed = self.root / "changed-for-restore.csv"
        changed.write_text(CSV.replace("Datum;", "Buchungstag;"), encoding="utf-8")
        analyze_unknown_import_format(
            self.store, model, changed, bank_identifier="example-bank"
        )

        restore_backup(self.store, backup["path"], backup_key)

        profiles = list_import_profiles(self.store)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["profile_id"], confirmed["profile"]["profile_id"])
        self.assertEqual(profiles[0]["status"], "ACTIVE")
        self.assertEqual(len(self.store.events("ImportMappingConfirmed")), 1)


if __name__ == "__main__":
    unittest.main()
