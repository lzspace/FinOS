"""Private stdin/stdout bridge for the native Finance desktop host.

The protocol is newline-delimited JSON. Stdout is reserved exclusively for
responses; diagnostics and financial payloads are never logged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

from .application import ApplicationContractError, FinanceApplicationService
from .crypto import KeychainKeyProvider, StaticKeyProvider
from .store import LocalFinanceStore, StoreInvariantError
from .workspace_lock import (
    inspect_workspace_lock,
    recover_stale_workspace_lock,
)


DESKTOP_CONTRACT_VERSION = "1.3.0"
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class DesktopProtocolError(ValueError):
    """A safe protocol error that never contains finance payload data."""


def _error_code(error: Exception) -> str:
    candidate = str(error).split(":", 1)[0]
    if candidate.startswith(("FINANCE_", "IMPORT_")):
        return candidate
    if isinstance(error, DesktopProtocolError):
        return "DESKTOP_PROTOCOL_INVALID"
    return "DESKTOP_OPERATION_FAILED"


def _response(
    *,
    request_id: str,
    operation: str,
    status: str,
    result: Any = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = {
        "request_id": request_id,
        "operation": operation,
        "contract_version": DESKTOP_CONTRACT_VERSION,
        "status": status,
    }
    if status == "OK":
        response["result"] = result
    else:
        response["error"] = error or {
            "code": "DESKTOP_OPERATION_FAILED",
            "message": "Der lokale Vorgang ist fehlgeschlagen.",
        }
    return response


class DesktopRequestHandler:
    def __init__(self, application: FinanceApplicationService) -> None:
        self.application = application

    def handle(self, request: Any) -> dict[str, Any]:
        request_id = ""
        operation = "invalid"
        try:
            if not isinstance(request, dict):
                raise DesktopProtocolError("DESKTOP_REQUEST_NOT_OBJECT")
            request_id = request.get("request_id", "")
            operation = request.get("operation", "")
            contract_version = request.get("contract_version")
            payload = request.get("payload", {})
            if (
                not isinstance(request_id, str)
                or not request_id
                or not isinstance(operation, str)
                or not operation
                or not isinstance(payload, dict)
            ):
                raise DesktopProtocolError("DESKTOP_REQUEST_INVALID")
            if contract_version != DESKTOP_CONTRACT_VERSION:
                return _response(
                    request_id=request_id,
                    operation=operation,
                    status="ERROR",
                    error={
                        "code": "DESKTOP_CONTRACT_INCOMPATIBLE",
                        "message": "Desktop-Host und Application API sind nicht kompatibel.",
                    },
                )
            if operation == "runtime_status":
                result = self.application.query("GetStartupStatus")
            elif operation in {"query", "command"}:
                name = payload.get("name")
                body = payload.get("payload", {})
                if not isinstance(name, str) or not name or not isinstance(body, dict):
                    raise DesktopProtocolError("DESKTOP_OPERATION_PAYLOAD_INVALID")
                result = (
                    self.application.query(name, body)
                    if operation == "query"
                    else self.application.command(name, body)
                )
            elif operation == "shutdown":
                result = {"shutdown": True}
            else:
                raise DesktopProtocolError("DESKTOP_OPERATION_UNSUPPORTED")
            return _response(
                request_id=request_id,
                operation=operation,
                status="OK",
                result=result,
            )
        except (
            ApplicationContractError,
            DesktopProtocolError,
            StoreInvariantError,
            ValueError,
            OSError,
        ) as error:
            return _response(
                request_id=request_id or "invalid",
                operation=operation or "invalid",
                status="ERROR",
                error={
                    "code": _error_code(error),
                    "message": "Der lokale Vorgang konnte nicht ausgeführt werden.",
                },
            )
        except Exception as error:  # fail closed without exposing payloads
            return _response(
                request_id=request_id or "invalid",
                operation=operation or "invalid",
                status="ERROR",
                error={
                    "code": _error_code(error),
                    "message": "Der lokale Application-Prozess ist fehlgeschlagen.",
                },
            )


def serve(handler: DesktopRequestHandler, source: TextIO, destination: TextIO) -> int:
    for raw_line in source:
        if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
            response = _response(
                request_id="invalid",
                operation="invalid",
                status="ERROR",
                error={
                    "code": "DESKTOP_REQUEST_TOO_LARGE",
                    "message": "Die lokale Anfrage überschreitet das Größenlimit.",
                },
            )
        else:
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError:
                response = _response(
                    request_id="invalid",
                    operation="invalid",
                    status="ERROR",
                    error={
                        "code": "DESKTOP_RESPONSE_INVALID",
                        "message": "Die lokale Anfrage ist kein gültiges JSON.",
                    },
                )
            else:
                response = handler.handle(request)
        destination.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        destination.flush()
        if response.get("result") == {"shutdown": True}:
            return 0
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument(
        "--e2e-test-key-env",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _recover_interrupted_desktop_session(data_dir: Path) -> bool:
    """Recover only a lock whose recorded owner process no longer exists."""
    status = inspect_workspace_lock(data_dir)
    if status["status"] != "STALE":
        return False
    instance_id = status.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        return False
    recovered = recover_stale_workspace_lock(
        data_dir,
        expected_instance_id=instance_id,
    )
    return recovered["recovered"] is True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    _recover_interrupted_desktop_session(data_dir)
    provider = (
        StaticKeyProvider(os.environ["FINANCE_TEST_KEY"].encode())
        if args.e2e_test_key_env
        and os.environ.get("FINANCE_DESKTOP_E2E") == "1"
        and os.environ.get("FINANCE_TEST_KEY")
        else KeychainKeyProvider()
    )
    if isinstance(provider, KeychainKeyProvider):
        provider.get_or_create_key()
    with LocalFinanceStore(data_dir, provider) as store:
        application = FinanceApplicationService(
            store,
            network_egress_disabled=True,
        )
        return serve(DesktopRequestHandler(application), sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DESKTOP_CONTRACT_VERSION",
    "DesktopProtocolError",
    "DesktopRequestHandler",
    "_recover_interrupted_desktop_session",
    "serve",
]
