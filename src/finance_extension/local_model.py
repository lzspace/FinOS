"""Offline-only local model runtime with verified GGUF artifacts.

The finance domain consumes this module only through ``LocalModelProvider``.
Providers return validated proposals and never receive a store or database
handle, so model output cannot append events or mutate finance data directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ALLOWED_PURPOSES = frozenset({"IMPORT_MAPPING", "CATEGORY_SUGGESTION"})
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 128 * 1024
INFERENCE_POLICY_VERSION = "1.1.0"
RUNTIME_PROVIDER = "LLAMA_CPP"
MINIMUM_LLAMA_CPP_BUILD = 11794
RUNTIME_CAPABILITIES = (
    "GBNF_STRUCTURED_OUTPUT",
    "REASONING_CONTROL",
    "OFFLINE_INFERENCE",
)


class StructuredOutputBackend(StrEnum):
    GBNF = "GBNF"
    JSON_SCHEMA_LLAMA_CPP = "JSON_SCHEMA_LLAMA_CPP"


HEALTH_CHECK_GRAMMAR = 'root ::= "{\\"status\\":\\"READY\\"}"'

_JSON_PRIMITIVES_GBNF = r"""
ws ::= [ \t\n\r]*
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)?
boolean ::= "true" | "false"
null ::= "null"
json-value ::= string | number | boolean | null | json-object | json-array
json-object ::= "{" ws (string ws ":" ws json-value (ws "," ws string ws ":" ws json-value)*)? ws "}"
json-array ::= "[" ws (json-value (ws "," ws json-value)*)? ws "]"
""".strip()

IMPORT_MAPPING_GBNF = (
    r"""
root ::= "{" ws "\"section_type\":" ws section-type "," ws "\"field_mappings\":" ws field-mappings "," ws "\"transformations\":" ws transformations "," ws "\"constants\":" ws constants "," ws "\"ignored_columns\":" ws string-array "," ws "\"confidence\":" ws number "," ws "\"warnings\":" ws string-array ws "}"
section-type ::= "\"CHECKING\"" | "\"SAVINGS\"" | "\"BROKERAGE\"" | "\"UNKNOWN\""
target-field ::= "\"booking_date\"" | "\"value_date\"" | "\"transaction_type_raw\"" | "\"counterparty\"" | "\"description\"" | "\"amount\"" | "\"currency\""
transformation-type ::= "\"PARSE_DATE\"" | "\"PARSE_DECIMAL\"" | "\"NORMALIZE_CURRENCY\"" | "\"TRIM\"" | "\"CONCATENATE\"" | "\"CONSTANT\"" | "\"SIGN_NORMALIZATION\""
field-mapping ::= "{" ws "\"source_column\":" ws string "," ws "\"target_field\":" ws target-field "," ws "\"confidence\":" ws number ws "}"
field-mappings ::= "[" ws (field-mapping (ws "," ws field-mapping)*)? ws "]"
transformation ::= "{" ws "\"target_field\":" ws target-field "," ws "\"type\":" ws transformation-type "," ws "\"parameters\":" ws json-object ws "}"
transformations ::= "[" ws (transformation (ws "," ws transformation)*)? ws "]"
constants ::= "{}" | "{" ws "\"currency\":" ws string ws "}"
string-array ::= "[" ws (string (ws "," ws string)*)? ws "]"
"""
    + "\n"
    + _JSON_PRIMITIVES_GBNF
).strip()

CATEGORY_SUGGESTION_GBNF = (
    r"""
root ::= "{" ws "\"category_code\":" ws string "," ws "\"raw_confidence\":" ws number "," ws "\"alternatives\":" ws alternatives "," ws "\"reason_codes\":" ws reason-codes ws "}"
alternative ::= "{" ws "\"category_code\":" ws string "," ws "\"confidence\":" ws number ws "}"
alternatives ::= "[" ws (alternative (ws "," ws alternative)*)? ws "]"
reason-code ::= "\"MERCHANT_SEMANTICS\"" | "\"DESCRIPTION_SEMANTICS\"" | "\"RECURRING_PATTERN\"" | "\"TRANSACTION_DIRECTION\"" | "\"AMOUNT_PATTERN\"" | "\"KNOWN_SERVICE_TYPE\"" | "\"AMBIGUOUS_DESCRIPTION\"" | "\"INSUFFICIENT_INFORMATION\""
reason-codes ::= "[" ws reason-code (ws "," ws reason-code)* ws "]"
"""
    + "\n"
    + _JSON_PRIMITIVES_GBNF
).strip()


class LocalModelError(ValueError):
    """Safe error containing a stable code and no model input or output."""


def _safe_runtime_version(output: str) -> str:
    """Return an auditable runtime identifier without exposing a local path."""

    lines = output.strip().splitlines()
    if not lines:
        return "unknown"
    value = lines[0][:160]
    if "/" in value or "\\" in value:
        return "unknown"
    sanitized = re.sub(r"[^A-Za-z0-9 ._()+,:=-]", "?", value).strip()
    return sanitized or "unknown"


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    model_version: str
    runtime: str
    format: str
    sha256: str
    purpose: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeArtifact:
    runtime_id: str
    provider: str
    version: str
    binary_sha256: str
    capabilities: tuple[str, ...]
    registered_at: str
    source: str
    path: Path

    def public(self, *, integrity_status: str = "VALID") -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "provider": self.provider,
            "version": self.version,
            "binary_sha256": self.binary_sha256,
            "capabilities": list(self.capabilities),
            "registered_at": self.registered_at,
            "source": self.source,
            "integrity_status": integrity_status,
        }


@dataclass(frozen=True)
class LocalModelConfiguration:
    max_context: int = 4096
    max_tokens: int = 512
    threads: int = 4
    memory_limit_mb: int = 4096
    inference_timeout_seconds: float = 30.0
    structured_output_backend: StructuredOutputBackend = StructuredOutputBackend.GBNF

    def validate(self) -> None:
        if not 512 <= self.max_context <= 32768:
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")
        if not 32 <= self.max_tokens <= 4096:
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")
        if not 1 <= self.threads <= 64:
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")
        if not 512 <= self.memory_limit_mb <= 65536:
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")
        if not 1 <= self.inference_timeout_seconds <= 300:
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")
        if not isinstance(self.structured_output_backend, StructuredOutputBackend):
            raise LocalModelError("LOCAL_MODEL_CONFIGURATION_INVALID")


@dataclass(frozen=True)
class StructuredInferenceRequest:
    purpose: str
    prompt: str
    output_schema: dict[str, Any]
    semantic_validator: Callable[[dict[str, Any]], bool] | None = None


class LocalModelProvider(Protocol):
    def start(self) -> None: ...

    def health_check(self) -> dict[str, Any]: ...

    def infer_structured(self, request: StructuredInferenceRequest) -> dict[str, Any]: ...

    def model_info(self) -> dict[str, Any]: ...

    def shutdown(self) -> None: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_llama_cpp_runtime(
    executable_path: str | Path,
    *,
    expected_sha256: str | None = None,
    source: str = "EXTERNAL",
    registered_at: str | None = None,
) -> RuntimeArtifact:
    """Verify one llama.cpp binary without returning its path to callers."""

    path = Path(executable_path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LocalModelError("LOCAL_AI_RUNTIME_NOT_FOUND") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise LocalModelError("LOCAL_AI_RUNTIME_INCOMPATIBLE")
    if not os.access(path, os.X_OK):
        raise LocalModelError("LOCAL_AI_RUNTIME_NOT_EXECUTABLE")
    try:
        digest = _sha256_file(path)
    except OSError as exc:
        raise LocalModelError("LOCAL_AI_RUNTIME_NOT_FOUND") from exc
    if expected_sha256 is not None and digest != expected_sha256:
        raise LocalModelError("LOCAL_AI_RUNTIME_HASH_MISMATCH")
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalModelError("LOCAL_AI_RUNTIME_START_FAILED") from exc
    except subprocess.CalledProcessError as exc:
        raise LocalModelError("LOCAL_AI_RUNTIME_INCOMPATIBLE") from exc
    raw_version = completed.stdout or completed.stderr
    version = _safe_runtime_version(raw_version)
    build_match = re.search(r"(?:build\s+|\bb)(\d{3,})", raw_version, re.IGNORECASE)
    if (
        version == "unknown"
        or not (
            re.search(r"llama(?:\.cpp|-cli).*\d", raw_version, re.IGNORECASE)
            or re.search(r"version\s*:\s*\d+", raw_version, re.IGNORECASE)
        )
        or build_match is None
        or int(build_match.group(1)) < MINIMUM_LLAMA_CPP_BUILD
    ):
        raise LocalModelError("LOCAL_AI_RUNTIME_INCOMPATIBLE")
    return RuntimeArtifact(
        runtime_id=f"runtime_llama_cpp_{digest[:16]}",
        provider=RUNTIME_PROVIDER,
        version=version,
        binary_sha256=digest,
        capabilities=RUNTIME_CAPABILITIES,
        registered_at=registered_at or datetime.now(UTC).isoformat(),
        source=source,
        path=path.resolve(strict=True),
    )


def load_model_manifest(path: str | Path) -> ModelManifest:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise LocalModelError("LOCAL_MODEL_FILE_NOT_FOUND")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID") from exc
    if not isinstance(raw, dict):
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")

    legacy_fields = {
        "model_id",
        "model_version",
        "runtime",
        "format",
        "sha256",
        "purpose",
    }
    versioned_fields = {
        "schema_version",
        "model_id",
        "model_version",
        "format",
        "artifact",
        "capabilities",
        "security",
    }
    if set(raw) == legacy_fields:
        normalized = raw
    elif set(raw) == versioned_fields:
        artifact = raw.get("artifact")
        security = raw.get("security")
        if (
            raw.get("schema_version") != "1.0.0"
            or not isinstance(artifact, dict)
            or set(artifact) != {"filename", "sha256"}
            or not isinstance(artifact.get("filename"), str)
            or not artifact["filename"].casefold().endswith(".gguf")
            or Path(artifact["filename"]).name != artifact["filename"]
            or not isinstance(security, dict)
            or set(security) != {"trust_zone", "network_required", "external_processing"}
            or security.get("trust_zone") != "PRIVATE_LOCAL"
            or security.get("network_required") is not False
            or security.get("external_processing") is not False
        ):
            raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
        normalized = {
            "model_id": raw["model_id"],
            "model_version": raw["model_version"],
            "runtime": "llama.cpp",
            "format": raw["format"],
            "sha256": artifact["sha256"],
            "purpose": raw["capabilities"],
        }
    else:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")

    purposes = normalized["purpose"]
    if (
        not isinstance(normalized["model_id"], str)
        or MODEL_ID.fullmatch(normalized["model_id"]) is None
        or not isinstance(normalized["model_version"], str)
        or SEMVER.fullmatch(normalized["model_version"]) is None
        or not isinstance(normalized["runtime"], str)
        or not isinstance(normalized["format"], str)
        or not isinstance(normalized["sha256"], str)
        or SHA256.fullmatch(normalized["sha256"]) is None
        or not isinstance(purposes, list)
        or not purposes
        or len(purposes) != len(set(purposes))
        or any(not isinstance(item, str) for item in purposes)
    ):
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    if normalized["runtime"] != "llama.cpp":
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INCOMPATIBLE")
    if normalized["format"] != "GGUF":
        raise LocalModelError("LOCAL_MODEL_FORMAT_UNSUPPORTED")
    if any(item not in ALLOWED_PURPOSES for item in purposes):
        raise LocalModelError("LOCAL_MODEL_PURPOSE_UNSUPPORTED")
    return ModelManifest(
        model_id=normalized["model_id"],
        model_version=normalized["model_version"],
        runtime=normalized["runtime"],
        format=normalized["format"],
        sha256=normalized["sha256"],
        purpose=tuple(purposes),
    )


def verify_model_artifact(model_path: str | Path, manifest_path: str | Path) -> ModelManifest:
    path = Path(model_path)
    if not path.exists():
        raise LocalModelError("LOCAL_MODEL_FILE_NOT_FOUND")
    if path.is_symlink() or not path.is_file():
        raise LocalModelError("LOCAL_MODEL_FORMAT_UNSUPPORTED")
    if path.suffix.casefold() != ".gguf":
        raise LocalModelError("LOCAL_MODEL_FORMAT_UNSUPPORTED")
    try:
        if path.open("rb").read(4) != b"GGUF":
            raise LocalModelError("LOCAL_MODEL_FORMAT_UNSUPPORTED")
    except OSError as exc:
        raise LocalModelError("LOCAL_MODEL_FILE_NOT_FOUND") from exc
    manifest = load_model_manifest(manifest_path)
    if _sha256_file(path) != manifest.sha256:
        raise LocalModelError("LOCAL_MODEL_HASH_MISMATCH")
    return manifest


class LlamaCppProvider:
    """Process-isolated llama.cpp provider with GBNF-constrained JSON output."""

    def __init__(
        self,
        executable_path: str | Path,
        model_path: str | Path,
        manifest: ModelManifest,
        configuration: LocalModelConfiguration | None = None,
    ) -> None:
        self.executable_path = Path(executable_path)
        self.model_path = Path(model_path)
        self.manifest = manifest
        self.configuration = configuration or LocalModelConfiguration()
        self._started = False
        self._runtime_version: str | None = None

    def start(self) -> None:
        self.configuration.validate()
        runtime = verify_llama_cpp_runtime(self.executable_path, source="RESOLVED")
        self._runtime_version = runtime.version
        self._run_health_check()
        self._started = True

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "READY" if self._started else "STOPPED",
            "runtime_version": self._runtime_version,
            "structured_output_backend": self.configuration.structured_output_backend.value,
            "inference_policy_version": INFERENCE_POLICY_VERSION,
        }

    def _base_command(self, *, max_tokens: int) -> list[str]:
        return [
            str(self.executable_path),
            "--model",
            str(self.model_path),
            "--ctx-size",
            str(self.configuration.max_context),
            "--threads",
            str(self.configuration.threads),
            "--n-predict",
            str(max_tokens),
            "--temp",
            "0",
            "--reasoning",
            "off",
            "--reasoning-budget",
            "0",
            "--no-display-prompt",
            "--single-turn",
            "--simple-io",
            "--no-show-timings",
            "--color",
            "off",
            "--log-disable",
        ]

    def _structured_arguments(self, request: StructuredInferenceRequest, grammar: str) -> list[str]:
        if self.configuration.structured_output_backend == StructuredOutputBackend.GBNF:
            return ["--grammar", grammar]
        return [
            "--json-schema",
            json.dumps(request.output_schema, sort_keys=True, separators=(",", ":")),
        ]

    def _run_process(self, command: list[str], *, timeout: float) -> str:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalModelError("LOCAL_AI_TIMEOUT") from exc
        except subprocess.CalledProcessError as exc:
            diagnostic = f"{exc.stdout or ''}\n{exc.stderr or ''}".casefold()
            if "failed to initialize samplers" in diagnostic or "grammar" in diagnostic:
                raise LocalModelError("LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED") from exc
            raise LocalModelError("LOCAL_MODEL_INFERENCE_FAILED") from exc
        except OSError as exc:
            raise LocalModelError("LOCAL_MODEL_INFERENCE_FAILED") from exc
        return completed.stdout.strip()

    @staticmethod
    def _parse_json_output(raw: str) -> dict[str, Any]:
        """Extract one JSON object from either clean output or llama-cli framing.

        Current llama.cpp CLI builds write a startup banner to stdout even with
        prompt and timing output disabled.  Accept only that known transport
        prefix; arbitrary model prose before or after JSON remains invalid.
        """
        framed = raw.strip()
        if framed.endswith("Exiting..."):
            framed = framed[: -len("Exiting...")].rstrip()

        decoder = json.JSONDecoder()
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for offset, character in enumerate(framed):
            if character != "{":
                continue
            try:
                value, end = decoder.raw_decode(framed, offset)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and end == len(framed):
                candidates.append((offset, end, value))

        if len(candidates) != 1:
            raise LocalModelError("LOCAL_AI_JSON_PARSE_FAILED")

        start, _end, result = candidates[0]
        prefix = framed[:start].strip()
        if prefix and not all(
            marker in prefix for marker in ("build      :", "model      :", "available commands:")
        ):
            raise LocalModelError("LOCAL_AI_JSON_PARSE_FAILED")
        return result

    def _run_health_check(self) -> None:
        request = StructuredInferenceRequest(
            purpose="HEALTH_CHECK",
            prompt='Return exactly {"status":"READY"}.',
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"const": "READY"}},
            },
        )
        command = self._base_command(max_tokens=16)
        command.extend(self._structured_arguments(request, HEALTH_CHECK_GRAMMAR))
        command.extend(["--prompt", request.prompt])
        raw = self._run_process(command, timeout=self.configuration.inference_timeout_seconds)
        result = self._parse_json_output(raw)
        if (
            not isinstance(result, dict)
            or result.get("status") != "READY"
            or set(result) != {"status"}
        ):
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED")

    def infer_structured(self, request: StructuredInferenceRequest) -> dict[str, Any]:
        if not self._started:
            raise LocalModelError("LOCAL_MODEL_NOT_READY")
        if request.purpose not in self.manifest.purpose:
            raise LocalModelError("LOCAL_MODEL_PURPOSE_DENIED")
        if len(request.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise LocalModelError("LOCAL_MODEL_INPUT_TOO_LARGE")
        grammars = {
            "IMPORT_MAPPING": IMPORT_MAPPING_GBNF,
            "CATEGORY_SUGGESTION": CATEGORY_SUGGESTION_GBNF,
        }
        grammar = grammars.get(request.purpose)
        if grammar is None:
            raise LocalModelError("LOCAL_MODEL_PURPOSE_DENIED")
        command = self._base_command(max_tokens=self.configuration.max_tokens)
        command.extend(self._structured_arguments(request, grammar))
        command.extend(["--prompt", request.prompt])
        raw = self._run_process(command, timeout=self.configuration.inference_timeout_seconds)
        result = self._parse_json_output(raw)
        try:
            Draft202012Validator(request.output_schema, format_checker=FormatChecker()).validate(
                result
            )
        except ValidationError as exc:
            raise LocalModelError("LOCAL_AI_SCHEMA_VALIDATION_FAILED") from exc
        if not isinstance(result, dict):
            raise LocalModelError("LOCAL_AI_SCHEMA_VALIDATION_FAILED")
        if request.semantic_validator is None:
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED")
        try:
            semantically_valid = request.semantic_validator(result)
        except Exception as exc:
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED") from exc
        if semantically_valid is not True:
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED")
        return result

    def model_info(self) -> dict[str, Any]:
        return {
            "model_id": self.manifest.model_id,
            "model_version": self.manifest.model_version,
            "runtime": self.manifest.runtime,
            "runtime_version": self._runtime_version,
            "format": self.manifest.format,
            "purposes": list(self.manifest.purpose),
            "structured_output_backend": self.configuration.structured_output_backend.value,
            "inference_policy_version": INFERENCE_POLICY_VERSION,
        }

    def shutdown(self) -> None:
        self._started = False


class LocalModelService:
    """Non-blocking lifecycle and registration for the optional local model."""

    REGISTRATION_FILE = "registration.json"
    RUNTIME_REGISTRATION_FILE = "runtime_registration.json"

    def __init__(
        self,
        models_root: str | Path,
        *,
        executable_path: str | Path | None = None,
        configuration: LocalModelConfiguration | None = None,
    ) -> None:
        self.models_root = Path(models_root)
        self.executable_path = Path(executable_path) if executable_path else None
        self.configuration = configuration or LocalModelConfiguration()
        self._provider: LocalModelProvider | None = None
        self._runtime: RuntimeArtifact | None = None
        self._runtime_error: str | None = None
        self._status = "DISABLED"
        self._error_code: str | None = None
        self._lock = Lock()

    def start_async(self) -> None:
        try:
            runtime = self._resolve_runtime()
        except LocalModelError as exc:
            runtime = None
            runtime_error = str(exc).split(":", 1)[0]
        else:
            runtime_error = None
        with self._lock:
            self._runtime = runtime
            self._runtime_error = runtime_error
        registration = self.models_root / self.REGISTRATION_FILE
        if not registration.is_file():
            return
        try:
            settings = json.loads(registration.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            with self._lock:
                self._status = "AI_UNAVAILABLE"
                self._error_code = "LOCAL_MODEL_REGISTRATION_FAILED"
            return
        if settings.get("enabled") is not True:
            with self._lock:
                self._status = "DISABLED"
                self._error_code = None
            return
        with self._lock:
            self._status = "LOADING_MODEL"
            self._error_code = None
        Thread(target=self._load_registered, daemon=True, name="finance-local-model").start()

    def _resolve_runtime(self) -> RuntimeArtifact:
        # A present bundled binary is authoritative. An invalid bundle must not
        # silently downgrade to an external executable.
        if self.executable_path is not None and self.executable_path.exists():
            return verify_llama_cpp_runtime(self.executable_path, source="BUNDLED")

        registration_path = self.models_root / self.RUNTIME_REGISTRATION_FILE
        if not registration_path.is_file():
            raise LocalModelError("LOCAL_AI_RUNTIME_NOT_FOUND")
        try:
            registration = json.loads(registration_path.read_text(encoding="utf-8"))
            required = {
                "runtime_path",
                "runtime_id",
                "provider",
                "version",
                "binary_sha256",
                "capabilities",
                "registered_at",
            }
            if not isinstance(registration, dict) or set(registration) != required:
                raise ValueError
            if registration["provider"] != RUNTIME_PROVIDER:
                raise ValueError
            if SHA256.fullmatch(registration["binary_sha256"]) is None:
                raise ValueError
            runtime = verify_llama_cpp_runtime(
                registration["runtime_path"],
                expected_sha256=registration["binary_sha256"],
                source="EXTERNAL",
                registered_at=registration["registered_at"],
            )
            if (
                runtime.runtime_id != registration["runtime_id"]
                or runtime.version != registration["version"]
                or list(runtime.capabilities) != registration["capabilities"]
            ):
                raise LocalModelError("LOCAL_AI_RUNTIME_HASH_MISMATCH")
            return runtime
        except LocalModelError:
            raise
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise LocalModelError("LOCAL_AI_RUNTIME_INCOMPATIBLE") from exc

    def register_runtime(self, runtime_path: str | Path) -> dict[str, Any]:
        runtime = verify_llama_cpp_runtime(runtime_path, source="EXTERNAL")
        registration = {
            **runtime.public(),
            "runtime_path": str(runtime.path),
        }
        registration.pop("source")
        registration.pop("integrity_status")
        try:
            self.models_root.mkdir(parents=True, exist_ok=True)
            self._write_json_registration(self.RUNTIME_REGISTRATION_FILE, registration)
        except OSError as exc:
            raise LocalModelError("LOCAL_AI_RUNTIME_START_FAILED") from exc
        # A valid bundled runtime remains preferred; otherwise activate the new
        # external registration immediately.
        resolved = self._resolve_runtime()
        with self._lock:
            self._runtime = resolved
            self._runtime_error = None
        if (self.models_root / self.REGISTRATION_FILE).is_file():
            self.start_async()
        return {"status": "REGISTERED", **resolved.public()}

    def _load_registered(self) -> None:
        try:
            runtime = self._resolve_runtime()
            registration = json.loads(
                (self.models_root / self.REGISTRATION_FILE).read_text(encoding="utf-8")
            )
            model_path = Path(registration["model_path"])
            manifest_path = Path(registration["manifest_path"])
            manifest = verify_model_artifact(model_path, manifest_path)
            provider = LlamaCppProvider(
                runtime.path,
                model_path,
                manifest,
                self.configuration,
            )
            provider.start()
        except Exception as exc:
            code = str(exc).split(":", 1)[0]
            if not code.startswith("LOCAL_"):
                code = "LOCAL_MODEL_REGISTRATION_FAILED"
            with self._lock:
                self._provider = None
                self._status = "AI_UNAVAILABLE"
                self._error_code = code
            return
        with self._lock:
            self._provider = provider
            self._runtime = runtime
            self._runtime_error = None
            self._status = "READY"
            self._error_code = None

    def register(self, model_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
        manifest = verify_model_artifact(model_path, manifest_path)
        runtime = self._resolve_runtime()
        provider = LlamaCppProvider(
            runtime.path,
            model_path,
            manifest,
            self.configuration,
        )
        provider.start()
        try:
            self.models_root.mkdir(parents=True, exist_ok=True)
            registration = {
                "model_path": str(Path(model_path).resolve(strict=True)),
                "manifest_path": str(Path(manifest_path).resolve(strict=True)),
                "model_sha256": _sha256_file(Path(model_path)),
                "runtime_version": provider.model_info()["runtime_version"],
                "structured_output_backend": provider.model_info()["structured_output_backend"],
                "inference_policy_version": INFERENCE_POLICY_VERSION,
                "enabled": True,
            }
            self._write_registration(registration)
        except (OSError, RuntimeError) as exc:
            provider.shutdown()
            raise LocalModelError("LOCAL_MODEL_REGISTRATION_FAILED") from exc
        with self._lock:
            previous = self._provider
            self._provider = provider
            self._runtime = runtime
            self._runtime_error = None
            self._status = "READY"
            self._error_code = None
        if previous:
            previous.shutdown()
        return {
            "status": "REGISTERED",
            **asdict(manifest),
            "purpose": list(manifest.purpose),
            "runtime_version": provider.model_info()["runtime_version"],
            "structured_output_backend": provider.model_info()["structured_output_backend"],
            "inference_policy_version": INFERENCE_POLICY_VERSION,
        }

    def _write_registration(self, registration: dict[str, Any]) -> None:
        self._write_json_registration(self.REGISTRATION_FILE, registration)

    def _write_json_registration(self, name: str, registration: dict[str, Any]) -> None:
        temporary = self.models_root / f".{name}.tmp"
        temporary.write_text(
            json.dumps(registration, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.models_root / name)

    def enable(self) -> dict[str, Any]:
        registration_path = self.models_root / self.REGISTRATION_FILE
        if not registration_path.is_file():
            raise LocalModelError("LOCAL_MODEL_NOT_REGISTERED")
        try:
            registration = json.loads(registration_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalModelError("LOCAL_MODEL_REGISTRATION_INVALID") from exc
        registration["enabled"] = True
        self._write_registration(registration)
        self.start_async()
        return {"status": "LOADING_MODEL"}

    def disable(self) -> dict[str, Any]:
        registration_path = self.models_root / self.REGISTRATION_FILE
        if registration_path.is_file():
            try:
                registration = json.loads(registration_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LocalModelError("LOCAL_MODEL_REGISTRATION_INVALID") from exc
            registration["enabled"] = False
            self._write_registration(registration)
        self.shutdown()
        return {"status": "DISABLED"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            provider = self._provider
            runtime = self._runtime
            runtime_error = self._runtime_error
            status = self._status
            error_code = self._error_code
        model = provider.model_info() if provider else None
        purposes = set(model["purposes"]) if model else set()
        return {
            "status": status,
            "available": status == "READY",
            "runtime": "llama.cpp",
            "runtime_details": runtime.public()
            if runtime
            else {
                "runtime_id": None,
                "provider": RUNTIME_PROVIDER,
                "version": None,
                "binary_sha256": None,
                "capabilities": [],
                "registered_at": None,
                "source": None,
                "integrity_status": "INVALID" if runtime_error else "MISSING",
                "error_code": runtime_error,
            },
            "model": model,
            "trust_zone": "PRIVATE_LOCAL",
            "processing_location": "LOCAL_DEVICE",
            "network_egress": "DENY",
            "network_ingress": "DENY",
            "import_mapping": "IMPORT_MAPPING" in purposes,
            "category_suggestions": "CATEGORY_SUGGESTION" in purposes,
            "automatic_classification": False,
            "error_code": error_code,
        }

    def infer_structured(self, request: StructuredInferenceRequest) -> dict[str, Any]:
        with self._lock:
            provider = self._provider
        if provider is None:
            raise LocalModelError("LOCAL_MODEL_NOT_READY")
        return provider.infer_structured(request)

    def shutdown(self) -> None:
        with self._lock:
            provider = self._provider
            self._provider = None
            self._status = "DISABLED"
        if provider:
            provider.shutdown()


__all__ = [
    "ALLOWED_PURPOSES",
    "CATEGORY_SUGGESTION_GBNF",
    "HEALTH_CHECK_GRAMMAR",
    "IMPORT_MAPPING_GBNF",
    "INFERENCE_POLICY_VERSION",
    "LlamaCppProvider",
    "LocalModelConfiguration",
    "LocalModelError",
    "LocalModelProvider",
    "LocalModelService",
    "ModelManifest",
    "RuntimeArtifact",
    "StructuredInferenceRequest",
    "StructuredOutputBackend",
    "load_model_manifest",
    "verify_model_artifact",
    "verify_llama_cpp_runtime",
]
