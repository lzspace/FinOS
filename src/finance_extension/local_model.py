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
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker


ALLOWED_PURPOSES = frozenset({"IMPORT_MAPPING", "CATEGORY_SUGGESTION"})
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 128 * 1024


class LocalModelError(ValueError):
    """Safe error containing a stable code and no model input or output."""


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    model_version: str
    runtime: str
    format: str
    sha256: str
    purpose: tuple[str, ...]


@dataclass(frozen=True)
class LocalModelConfiguration:
    max_context: int = 4096
    max_tokens: int = 512
    threads: int = 4
    memory_limit_mb: int = 4096
    inference_timeout_seconds: float = 30.0

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


@dataclass(frozen=True)
class StructuredInferenceRequest:
    purpose: str
    prompt: str
    output_schema: dict[str, Any]


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


def load_model_manifest(path: str | Path) -> ModelManifest:
    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "model_id",
        "model_version",
        "runtime",
        "format",
        "sha256",
        "purpose",
    }:
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    purposes = raw["purpose"]
    if (
        not isinstance(raw["model_id"], str)
        or MODEL_ID.fullmatch(raw["model_id"]) is None
        or not isinstance(raw["model_version"], str)
        or SEMVER.fullmatch(raw["model_version"]) is None
        or raw["runtime"] != "llama.cpp"
        or raw["format"] != "GGUF"
        or not isinstance(raw["sha256"], str)
        or SHA256.fullmatch(raw["sha256"]) is None
        or not isinstance(purposes, list)
        or not purposes
        or len(purposes) != len(set(purposes))
        or any(item not in ALLOWED_PURPOSES for item in purposes)
    ):
        raise LocalModelError("LOCAL_MODEL_MANIFEST_INVALID")
    return ModelManifest(
        model_id=raw["model_id"],
        model_version=raw["model_version"],
        runtime=raw["runtime"],
        format=raw["format"],
        sha256=raw["sha256"],
        purpose=tuple(purposes),
    )


def verify_model_artifact(model_path: str | Path, manifest_path: str | Path) -> ModelManifest:
    path = Path(model_path)
    if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".gguf":
        raise LocalModelError("LOCAL_MODEL_FILE_INVALID")
    manifest = load_model_manifest(manifest_path)
    if _sha256_file(path) != manifest.sha256:
        raise LocalModelError("LOCAL_MODEL_INTEGRITY_FAILED")
    return manifest


class LlamaCppProvider:
    """Process-isolated llama.cpp CLI provider with JSON-schema output."""

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

    def start(self) -> None:
        self.configuration.validate()
        if (
            self.executable_path.is_symlink()
            or not self.executable_path.is_file()
            or not os.access(self.executable_path, os.X_OK)
        ):
            raise LocalModelError("LOCAL_MODEL_RUNTIME_UNAVAILABLE")
        try:
            subprocess.run(
                [str(self.executable_path), "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LocalModelError("LOCAL_MODEL_RUNTIME_UNAVAILABLE") from exc
        self._started = True

    def health_check(self) -> dict[str, Any]:
        return {"status": "READY" if self._started else "STOPPED"}

    def infer_structured(self, request: StructuredInferenceRequest) -> dict[str, Any]:
        if not self._started:
            raise LocalModelError("LOCAL_MODEL_NOT_READY")
        if request.purpose not in self.manifest.purpose:
            raise LocalModelError("LOCAL_MODEL_PURPOSE_DENIED")
        if len(request.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise LocalModelError("LOCAL_MODEL_INPUT_TOO_LARGE")
        command = [
            str(self.executable_path),
            "--model",
            str(self.model_path),
            "--ctx-size",
            str(self.configuration.max_context),
            "--threads",
            str(self.configuration.threads),
            "--n-predict",
            str(self.configuration.max_tokens),
            "--temp",
            "0",
            "--no-display-prompt",
            "--json-schema",
            json.dumps(request.output_schema, sort_keys=True, separators=(",", ":")),
            "--prompt",
            request.prompt,
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.configuration.inference_timeout_seconds,
            )
            result = json.loads(completed.stdout)
            Draft202012Validator(
                request.output_schema, format_checker=FormatChecker()
            ).validate(result)
        except subprocess.TimeoutExpired as exc:
            raise LocalModelError("LOCAL_AI_TIMEOUT") from exc
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalModelError("LOCAL_MODEL_INFERENCE_FAILED") from exc
        except Exception as exc:
            raise LocalModelError("LOCAL_MODEL_OUTPUT_INVALID") from exc
        if not isinstance(result, dict):
            raise LocalModelError("LOCAL_MODEL_OUTPUT_INVALID")
        return result

    def model_info(self) -> dict[str, Any]:
        return {
            "model_id": self.manifest.model_id,
            "model_version": self.manifest.model_version,
            "runtime": self.manifest.runtime,
            "format": self.manifest.format,
            "purposes": list(self.manifest.purpose),
        }

    def shutdown(self) -> None:
        self._started = False


class LocalModelService:
    """Non-blocking lifecycle and registration for the optional local model."""

    REGISTRATION_FILE = "registration.json"

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
        self._status = "DISABLED"
        self._error_code: str | None = None
        self._lock = Lock()

    def start_async(self) -> None:
        registration = self.models_root / self.REGISTRATION_FILE
        if not registration.is_file():
            return
        try:
            settings = json.loads(registration.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            with self._lock:
                self._status = "AI_UNAVAILABLE"
                self._error_code = "LOCAL_MODEL_REGISTRATION_INVALID"
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

    def _load_registered(self) -> None:
        try:
            registration = json.loads(
                (self.models_root / self.REGISTRATION_FILE).read_text(encoding="utf-8")
            )
            model_path = Path(registration["model_path"])
            manifest_path = Path(registration["manifest_path"])
            manifest = verify_model_artifact(model_path, manifest_path)
            if self.executable_path is None:
                raise LocalModelError("LOCAL_MODEL_RUNTIME_UNAVAILABLE")
            provider = LlamaCppProvider(
                self.executable_path,
                model_path,
                manifest,
                self.configuration,
            )
            provider.start()
        except Exception as exc:
            code = str(exc).split(":", 1)[0]
            if not code.startswith("LOCAL_"):
                code = "LOCAL_MODEL_REGISTRATION_INVALID"
            with self._lock:
                self._provider = None
                self._status = "AI_UNAVAILABLE"
                self._error_code = code
            return
        with self._lock:
            self._provider = provider
            self._status = "READY"
            self._error_code = None

    def register(self, model_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
        manifest = verify_model_artifact(model_path, manifest_path)
        self.models_root.mkdir(parents=True, exist_ok=True)
        registration = {
            "model_path": str(Path(model_path).resolve(strict=True)),
            "manifest_path": str(Path(manifest_path).resolve(strict=True)),
            "enabled": True,
        }
        self._write_registration(registration)
        self.start_async()
        return {"status": "REGISTERED", **asdict(manifest), "purpose": list(manifest.purpose)}

    def _write_registration(self, registration: dict[str, Any]) -> None:
        temporary = self.models_root / f".{self.REGISTRATION_FILE}.tmp"
        temporary.write_text(
            json.dumps(registration, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.models_root / self.REGISTRATION_FILE)

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
            status = self._status
            error_code = self._error_code
        model = provider.model_info() if provider else None
        purposes = set(model["purposes"]) if model else set()
        return {
            "status": status,
            "available": status == "READY",
            "runtime": "llama.cpp",
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
    "LlamaCppProvider",
    "LocalModelConfiguration",
    "LocalModelError",
    "LocalModelProvider",
    "LocalModelService",
    "ModelManifest",
    "StructuredInferenceRequest",
    "load_model_manifest",
    "verify_model_artifact",
]
