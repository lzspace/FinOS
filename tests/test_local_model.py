from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from finance_extension.local_model import (
    CATEGORY_SUGGESTION_GBNF,
    IMPORT_MAPPING_GBNF,
    LlamaCppProvider,
    LocalModelConfiguration,
    LocalModelError,
    LocalModelService,
    StructuredInferenceRequest,
    StructuredOutputBackend,
    load_model_manifest,
    verify_model_artifact,
    verify_llama_cpp_runtime,
)


class LocalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model = self.root / "model.gguf"
        self.model.write_bytes(b"GGUFsynthetic-for-tests")
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
        self.model.write_bytes(b"GGUFtampered")

        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_HASH_MISMATCH"):
            verify_model_artifact(self.model, self.manifest)

    def test_manifest_is_strict_and_rejects_external_purpose(self) -> None:
        raw = json.loads(self.manifest.read_text(encoding="utf-8"))
        raw["purpose"] = ["INVESTMENT_ADVICE"]
        self.manifest.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_PURPOSE_UNSUPPORTED"):
            load_model_manifest(self.manifest)

    def test_versioned_private_local_manifest_is_supported(self) -> None:
        versioned = {
            "schema_version": "1.0.0",
            "model_id": "finance-assistant-small",
            "model_version": "1.0.0",
            "format": "GGUF",
            "artifact": {
                "filename": "model.gguf",
                "sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
            },
            "capabilities": ["IMPORT_MAPPING", "CATEGORY_SUGGESTION"],
            "security": {
                "trust_zone": "PRIVATE_LOCAL",
                "network_required": False,
                "external_processing": False,
            },
        }
        self.manifest.write_text(json.dumps(versioned), encoding="utf-8")

        manifest = verify_model_artifact(self.model, self.manifest)

        self.assertEqual(manifest.runtime, "llama.cpp")
        self.assertEqual(manifest.sha256, versioned["artifact"]["sha256"])
        self.assertEqual(manifest.purpose, ("IMPORT_MAPPING", "CATEGORY_SUGGESTION"))

    def test_versioned_manifest_rejects_unsafe_security_boundary(self) -> None:
        versioned = {
            "schema_version": "1.0.0",
            "model_id": "finance-assistant-small",
            "model_version": "1.0.0",
            "format": "GGUF",
            "artifact": {
                "filename": "model.gguf",
                "sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
            },
            "capabilities": ["IMPORT_MAPPING"],
            "security": {
                "trust_zone": "PRIVATE_LOCAL",
                "network_required": True,
                "external_processing": False,
            },
        }
        self.manifest.write_text(json.dumps(versioned), encoding="utf-8")

        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_MANIFEST_INVALID"):
            load_model_manifest(self.manifest)

    def test_missing_runtime_does_not_block_finance_service(self) -> None:
        service = LocalModelService(self.root / "registered-models")
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_NOT_FOUND"):
            service.register(self.model, self.manifest)

        status = service.status()
        self.assertEqual(status["status"], "DISABLED")
        self.assertFalse(status["available"])
        self.assertFalse(status["automatic_classification"])
        self.assertEqual(status["network_egress"], "DENY")

    def rewrite_manifest(self, **updates: object) -> None:
        raw = json.loads(self.manifest.read_text(encoding="utf-8"))
        raw.update(updates)
        self.manifest.write_text(json.dumps(raw), encoding="utf-8")

    def executable(self, body: str | None = None) -> Path:
        runtime = self.root / "llama-cli"
        body = (
            body
            or """if [ \"$1\" = \"--version\" ]; then
  echo 'llama.cpp b11794-f84909536'
else
  printf '%s\\n' '{\"status\":\"READY\"}'
fi"""
        )
        runtime.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        runtime.chmod(0o700)
        return runtime

    def test_registration_failure_codes_are_specific(self) -> None:
        cases: list[tuple[str, Callable[[], object]]] = [
            ("LOCAL_MODEL_FILE_NOT_FOUND", lambda: self.model.unlink()),
            (
                "LOCAL_MODEL_MANIFEST_INVALID",
                lambda: self.manifest.write_text("not json", encoding="utf-8"),
            ),
            (
                "LOCAL_MODEL_MANIFEST_INCOMPATIBLE",
                lambda: self.rewrite_manifest(runtime="other-runtime"),
            ),
            ("LOCAL_MODEL_FORMAT_UNSUPPORTED", lambda: self.rewrite_manifest(format="SAFETENSORS")),
            (
                "LOCAL_MODEL_PURPOSE_UNSUPPORTED",
                lambda: self.rewrite_manifest(purpose=["INVESTMENT_ADVICE"]),
            ),
            (
                "LOCAL_MODEL_HASH_MISMATCH",
                lambda: self.model.write_bytes(b"GGUFdifferent-actual-bytes"),
            ),
        ]
        original_model = self.model.read_bytes()
        original_manifest = self.manifest.read_text(encoding="utf-8")
        for code, mutate in cases:
            with self.subTest(code=code):
                self.model.write_bytes(original_model)
                self.manifest.write_text(original_manifest, encoding="utf-8")
                mutate()
                with self.assertRaisesRegex(LocalModelError, code):
                    LocalModelService(self.root / "registered").register(self.model, self.manifest)

    def test_runtime_failure_codes_are_specific(self) -> None:
        missing = self.root / "missing-llama-cli"
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_NOT_FOUND"):
            from finance_extension.local_model import LlamaCppProvider

            LlamaCppProvider(missing, self.model, load_model_manifest(self.manifest)).start()

        runtime = self.root / "llama-cli"
        runtime.write_text("not executable", encoding="utf-8")
        runtime.chmod(0o600)
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_NOT_EXECUTABLE"):
            LlamaCppProvider(runtime, self.model, load_model_manifest(self.manifest)).start()

        runtime = self.executable("exit 9")
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_INCOMPATIBLE"):
            LlamaCppProvider(runtime, self.model, load_model_manifest(self.manifest)).start()

    def test_external_runtime_registration_is_opaque_and_persistent(self) -> None:
        runtime = self.executable()
        models = self.root / "registered-runtime"
        result = LocalModelService(models).register_runtime(runtime)

        self.assertEqual(result["status"], "REGISTERED")
        self.assertEqual(result["provider"], "LLAMA_CPP")
        self.assertEqual(result["integrity_status"], "VALID")
        self.assertNotIn("path", result)
        restarted = LocalModelService(models)
        restarted.start_async()
        status = restarted.status()["runtime_details"]
        self.assertEqual(status["runtime_id"], result["runtime_id"])
        self.assertEqual(status["version"], "llama.cpp b11794-f84909536")
        self.assertEqual(status["source"], "EXTERNAL")

    def test_runtime_rejects_wrong_file_type(self) -> None:
        directory = self.root / "llama-directory"
        directory.mkdir()
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_INCOMPATIBLE"):
            verify_llama_cpp_runtime(directory)

    def test_tampered_and_missing_registered_runtime_are_detected_after_restart(self) -> None:
        for expected, mutate in (
            (
                "LOCAL_AI_RUNTIME_HASH_MISMATCH",
                lambda path: path.write_text("tampered", encoding="utf-8"),
            ),
            ("LOCAL_AI_RUNTIME_NOT_FOUND", lambda path: path.unlink()),
        ):
            with self.subTest(expected=expected):
                runtime = self.executable()
                models = self.root / f"registered-{expected}"
                LocalModelService(models).register_runtime(runtime)
                mutate(runtime)
                restarted = LocalModelService(models)
                restarted.start_async()
                status = restarted.status()["runtime_details"]
                self.assertEqual(status["integrity_status"], "INVALID")
                self.assertEqual(status["error_code"], expected)

    def test_bundled_runtime_wins_before_registered_external(self) -> None:
        external = self.root / "external-llama-cli"
        external.write_text("#!/bin/sh\necho 'llama.cpp b11794-external'\n", encoding="utf-8")
        external.chmod(0o700)
        bundled = self.root / "bundled-llama-cli"
        bundled.write_text("#!/bin/sh\necho 'llama.cpp b11795-bundled'\n", encoding="utf-8")
        bundled.chmod(0o700)
        models = self.root / "preferred-runtime"
        LocalModelService(models).register_runtime(external)

        restarted = LocalModelService(models, executable_path=bundled)
        restarted.start_async()
        status = restarted.status()["runtime_details"]
        self.assertEqual(status["source"], "BUNDLED")
        self.assertEqual(status["version"], "llama.cpp b11795-bundled")

    def test_runtime_rejects_incompatible_build(self) -> None:
        runtime = self.executable("echo 'llama.cpp b11793-old'")
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_INCOMPATIBLE"):
            verify_llama_cpp_runtime(runtime)

    def test_runtime_start_failure_has_stable_code(self) -> None:
        runtime = self.root / "broken-llama-cli"
        runtime.write_text("#!/missing/interpreter\n", encoding="utf-8")
        runtime.chmod(0o700)
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_START_FAILED"):
            verify_llama_cpp_runtime(runtime)

    def test_registration_write_failure_has_stable_code(self) -> None:
        blocked = self.root / "blocked"
        blocked.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(LocalModelError, "LOCAL_MODEL_REGISTRATION_FAILED"):
            LocalModelService(blocked, executable_path=self.executable()).register(
                self.model, self.manifest
            )

    def test_hash_is_computed_from_actual_gguf_and_registration_survives_restart(self) -> None:
        runtime = self.executable()
        service = LocalModelService(self.root / "registered", executable_path=runtime)
        service.register(self.model, self.manifest)
        restarted = LocalModelService(self.root / "registered", executable_path=runtime)
        restarted.start_async()
        for _ in range(1000):
            if restarted.status()["status"] != "LOADING_MODEL":
                break
            time.sleep(0.002)
        self.assertEqual(restarted.status()["status"], "READY")
        registration = json.loads(
            (self.root / "registered/registration.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            registration["model_sha256"], hashlib.sha256(self.model.read_bytes()).hexdigest()
        )
        self.assertNotEqual(registration["model_sha256"], "0" * 64)
        self.assertEqual(registration["runtime_version"], "llama.cpp b11794-f84909536")
        self.assertEqual(registration["structured_output_backend"], "GBNF")
        self.assertEqual(registration["inference_policy_version"], "2.0.0")
        self.assertEqual(restarted.status()["model"]["structured_output_backend"], "GBNF")

    def provider_with_response(self, response: str) -> LlamaCppProvider:
        body = f"""if [ \"$1\" = \"--version\" ]; then
  echo 'llama.cpp b11794-f84909536'
else
  last=''
  for value in \"$@\"; do last=\"$value\"; done
  case \"$last\" in
    'Return exactly '* ) printf '%s\\n' '{{\"status\":\"READY\"}}' ;;
    * ) printf '%s\\n' '{response}' ;;
  esac
fi"""
        provider = LlamaCppProvider(
            self.executable(body), self.model, load_model_manifest(self.manifest)
        )
        provider.start()
        return provider

    def request(
        self, semantic_validator: Callable[[dict[str, object]], bool] | None = None
    ) -> StructuredInferenceRequest:
        return StructuredInferenceRequest(
            purpose="IMPORT_MAPPING",
            prompt="Map synthetic columns.",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"const": "OK"}},
            },
            semantic_validator=semantic_validator,
        )

    def test_health_check_uses_reasoning_off_and_exact_gbnf(self) -> None:
        arguments = self.root / "arguments.txt"
        body = f"""if [ \"$1\" = \"--version\" ]; then
  echo 'llama.cpp b11794-f84909536'
else
  printf '%s\\n' \"$@\" > '{arguments}'
  printf '%s\\n' '{{\"status\":\"READY\"}}'
fi"""
        provider = LlamaCppProvider(
            self.executable(body), self.model, load_model_manifest(self.manifest)
        )
        provider.start()

        invoked = arguments.read_text(encoding="utf-8")
        self.assertIn("--reasoning\noff", invoked)
        self.assertIn("--reasoning-budget\n0", invoked)
        self.assertIn("--single-turn", invoked)
        self.assertIn("--simple-io", invoked)
        self.assertIn("--no-show-timings", invoked)
        self.assertIn("--color\noff", invoked)
        self.assertIn("--log-disable", invoked)
        self.assertIn("--grammar", invoked)
        self.assertIn('root ::= "{\\"status\\":\\"READY\\"}"', invoked)
        self.assertNotIn("--json-schema", invoked)
        self.assertEqual(provider.health_check()["structured_output_backend"], "GBNF")
        self.assertEqual(provider.health_check()["runtime_version"], "llama.cpp b11794-f84909536")

    def test_health_check_accepts_known_llama_cli_banner(self) -> None:
        banner = """llama.cpp
build      : 11794 (f84909536)
model      : synthetic.gguf
available commands:
  /exit or Ctrl+C    exit
{"status":"READY"}"""
        body = f"""if [ \"$1\" = \"--version\" ]; then
  echo 'llama.cpp b11794-f84909536'
else
  printf '%s\\n' '{banner}'
fi"""
        provider = LlamaCppProvider(
            self.executable(body), self.model, load_model_manifest(self.manifest)
        )

        provider.start()

        self.assertEqual(provider.health_check()["status"], "READY")

    def test_health_check_accepts_llama_cli_single_turn_exit_framing(self) -> None:
        output = """Loading model...

llama.cpp
build      : b11794-f84909536
model      : synthetic.gguf
available commands:
  /exit or Ctrl+C    exit

> Return exactly {"status":"READY"}.
{"status":"READY"}

Exiting..."""
        provider = LlamaCppProvider(
            self.executable(), self.model, load_model_manifest(self.manifest)
        )
        with patch.object(provider, "_run_process", return_value=output):
            provider.start()

        self.assertEqual(provider.health_check()["status"], "READY")

    def test_nested_structured_json_after_llama_banner_is_accepted(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        raw = """llama.cpp
build      : 11794 (f84909536)
model      : synthetic.gguf
available commands:
  /exit or Ctrl+C    exit
{"status":"OK","mapping":{"date":"Buchungstag"}}"""
        request = StructuredInferenceRequest(
            purpose="IMPORT_MAPPING",
            prompt="Map synthetic columns.",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "mapping"],
                "properties": {
                    "status": {"const": "OK"},
                    "mapping": {"type": "object"},
                },
            },
            semantic_validator=lambda result: result["mapping"].get("date") == "Buchungstag",
        )
        with patch.object(provider, "_run_process", return_value=raw):
            result = provider.infer_structured(request)

        self.assertEqual(result["mapping"], {"date": "Buchungstag"})

    def test_multiple_json_documents_are_rejected(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with patch.object(
            provider,
            "_run_process",
            return_value='{"status":"OK"}\n{"status":"OK"}',
        ):
            with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_JSON_PARSE_FAILED"):
                provider.infer_structured(self.request(lambda _result: True))

    def test_arbitrary_text_before_json_is_rejected(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with patch.object(
            provider,
            "_run_process",
            return_value='Here is the answer:\n{"status":"OK"}',
        ):
            with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_JSON_PARSE_FAILED"):
                provider.infer_structured(self.request(lambda _result: True))

    def test_arbitrary_text_after_json_is_rejected(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with patch.object(
            provider,
            "_run_process",
            return_value='{"status":"OK"}\nThis must not be accepted.\nExiting...',
        ):
            with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_JSON_PARSE_FAILED"):
                provider.infer_structured(self.request(lambda _result: True))

    def test_invalid_json_has_stable_error(self) -> None:
        provider = self.provider_with_response("not-json")
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_JSON_PARSE_FAILED"):
            provider.infer_structured(self.request())

    def test_schema_invalid_json_has_stable_error(self) -> None:
        provider = self.provider_with_response('{"status":"WRONG"}')
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_SCHEMA_VALIDATION_FAILED"):
            provider.infer_structured(self.request())

    def test_semantically_invalid_json_has_stable_error(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_SEMANTIC_VALIDATION_FAILED"):
            provider.infer_structured(self.request(lambda _result: False))

    def test_semantic_validator_is_mandatory_after_gbnf_and_schema(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_SEMANTIC_VALIDATION_FAILED"):
            provider.infer_structured(self.request())

    def test_runtime_version_cannot_expose_a_local_path(self) -> None:
        executable = self.executable(
            """if [ \"$1\" = \"--version\" ]; then
  echo '/Users/example/private/llama.cpp b11794'
else
  echo '{\"status\":\"READY\"}'
fi"""
        )
        provider = LlamaCppProvider(executable, self.model, load_model_manifest(self.manifest))
        with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_RUNTIME_INCOMPATIBLE"):
            provider.start()

    def test_timeout_has_stable_error_and_never_retries_as_free_text(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        with patch(
            "finance_extension.local_model.subprocess.run",
            side_effect=subprocess.TimeoutExpired("llama-cli", 1),
        ) as run:
            with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_TIMEOUT"):
                provider.infer_structured(self.request())
        self.assertEqual(run.call_count, 1)

    def test_grammar_initialization_failure_has_stable_error(self) -> None:
        provider = self.provider_with_response('{"status":"OK"}')
        failure = subprocess.CalledProcessError(
            1,
            ["llama-cli"],
            stderr="Failed to initialize samplers: std::exception",
        )
        with patch("finance_extension.local_model.subprocess.run", side_effect=failure) as run:
            with self.assertRaisesRegex(LocalModelError, "LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED"):
                provider.infer_structured(self.request())
        self.assertEqual(run.call_count, 1)

    def test_experimental_json_schema_backend_is_explicit_only(self) -> None:
        configuration = LocalModelConfiguration(
            structured_output_backend=StructuredOutputBackend.JSON_SCHEMA_LLAMA_CPP
        )
        self.assertEqual(configuration.structured_output_backend.value, "JSON_SCHEMA_LLAMA_CPP")
        self.assertIn("root ::=", IMPORT_MAPPING_GBNF)
        self.assertIn('string ::= "\\"" char* "\\""', IMPORT_MAPPING_GBNF)
        self.assertIn('char ::= [^"\\\\\\x7F\\x00-\\x1F]', IMPORT_MAPPING_GBNF)
        self.assertIn('"\\"raw_confidence\\":"', CATEGORY_SUGGESTION_GBNF)
        self.assertIn("MERCHANT_SEMANTICS", CATEGORY_SUGGESTION_GBNF)
        self.assertNotIn("reason ::=", CATEGORY_SUGGESTION_GBNF)

    @unittest.skipUnless(
        os.environ.get("FINANCE_LLAMA_CPP_INTEGRATION") == "1",
        "requires local llama.cpp, GGUF and manifest",
    )
    def test_real_llama_cpp_gbnf_health_check(self) -> None:
        executable = Path(os.environ["FINANCE_LLAMA_CPP_EXECUTABLE"])
        model = Path(os.environ["FINANCE_LLAMA_CPP_MODEL"])
        manifest_path = Path(os.environ["FINANCE_LLAMA_CPP_MANIFEST"])
        provider = LlamaCppProvider(executable, model, verify_model_artifact(model, manifest_path))

        provider.start()

        self.assertEqual(provider.health_check()["status"], "READY")
        self.assertEqual(provider.health_check()["structured_output_backend"], "GBNF")
