"""Offline-oriented, reproducible and signed release builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from . import __version__
from .contract_compatibility import (
    create_contract_catalog,
    reject_undeclared_breaking_change,
)
from .release_security import (
    create_integrity_manifest,
    load_private_key,
    public_key_pem,
    sha256_file,
    sign_file,
)


class ReleaseBuildError(RuntimeError):
    pass


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "extensions" / "finance" / "schemas"
UI_ROOT = ROOT / "ui" / "dist"
EMBEDDED_INTEGRITY = Path(__file__).with_name("release_integrity.json")
SOURCE_DATE_EPOCH = "1767225600"  # 2026-01-01T00:00:00Z
CONTRACT_VERSION = "1.3.0"
PREVIOUS_CONTRACT_VERSION = "1.2.0"
PREVIOUS_CONTRACT_CATALOG = ROOT / "extensions" / "finance" / "contracts" / "catalog-1.2.0.json"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def prepare_embedded_integrity() -> dict[str, Any]:
    if not UI_ROOT.is_dir() or not any(UI_ROOT.rglob("*")):
        raise ReleaseBuildError("FINANCE_RELEASE_UI_BUNDLE_MISSING")
    manifest = create_integrity_manifest(
        SCHEMA_ROOT, UI_ROOT, application_version=__version__
    )
    _write_json(EMBEDDED_INTEGRITY, manifest)
    return manifest


def _build_once(destination: Path) -> Path:
    environment = {**os.environ, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH, "PYTHONHASHSEED": "0"}
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(destination)],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseBuildError("FINANCE_RELEASE_WHEEL_COUNT_INVALID")
    return wheels[0]


def build_reproducible_wheel(destination: str | Path) -> dict[str, Any]:
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    prepare_embedded_integrity()
    with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
        first = _build_once(Path(first_dir))
        second = _build_once(Path(second_dir))
        first_hash = sha256_file(first)
        second_hash = sha256_file(second)
        if first_hash != second_hash:
            raise ReleaseBuildError("FINANCE_RELEASE_NOT_REPRODUCIBLE")
        final = output / first.name
        shutil.copyfile(first, final)
    return {"path": str(final), "sha256": first_hash, "reproducible": True, "builds": 2}


def _components() -> list[dict[str, Any]]:
    lock = ROOT / "requirements-runtime.lock"
    packages = [
        line.split("==", 1)[0]
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    return [
        {"type": "library", "name": name, "version": package_version(name)}
        for name in packages
    ]


def _write_sboms(output: Path) -> tuple[Path, Path]:
    components = _components()
    cyclone = output / "cyclonedx-sbom.json"
    spdx = output / "spdx-sbom.json"
    _write_json(
        cyclone,
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {"component": {"type": "application", "name": "agent-os-finance", "version": __version__}},
            "components": components,
        },
    )
    _write_json(
        spdx,
        {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"agent-os-finance-{__version__}",
            "documentNamespace": f"https://agent-os.local/spdx/agent-os-finance-{__version__}",
            "creationInfo": {"created": "2026-01-01T00:00:00Z", "creators": ["Tool: finance-release-1.2.0"]},
            "packages": [
                {
                    "SPDXID": f"SPDXRef-Package-{item['name']}",
                    "name": item["name"],
                    "versionInfo": item["version"],
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                }
                for item in components
            ],
        },
    )
    return cyclone, spdx


def create_release(
    destination: str | Path,
    signing_key: str | Path,
    *,
    test_summary: dict[str, int] | None = None,
    offline_verified: bool = False,
    acceptance_report: str | Path | None = None,
    critical_findings: int = 0,
    high_findings: int = 0,
) -> dict[str, Any]:
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ReleaseBuildError("FINANCE_RELEASE_DIRECTORY_NOT_EMPTY")
    wheel = build_reproducible_wheel(output)
    acceptance: dict[str, Any] | None = None
    acceptance_path: Path | None = None
    if acceptance_report is not None:
        source_report = Path(acceptance_report)
        try:
            acceptance = json.loads(source_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseBuildError("FINANCE_ACCEPTANCE_REPORT_INVALID") from exc
        if (
            acceptance.get("status") != "PASSED"
            or acceptance.get("source") != "INSTALLED_WHEEL"
            or acceptance.get("wheel_sha256") != wheel["sha256"]
        ):
            raise ReleaseBuildError("FINANCE_ACCEPTANCE_REPORT_INVALID")
        acceptance_path = output / "acceptance-report.json"
        _write_json(acceptance_path, acceptance)
    if offline_verified and acceptance is None:
        raise ReleaseBuildError("FINANCE_ACCEPTANCE_REPORT_REQUIRED")
    integrity = json.loads(EMBEDDED_INTEGRITY.read_text(encoding="utf-8"))
    catalog_path = output / "contract-catalog.json"
    current_catalog = create_contract_catalog(SCHEMA_ROOT)
    if not PREVIOUS_CONTRACT_CATALOG.exists():
        raise ReleaseBuildError("FINANCE_PREVIOUS_CONTRACT_CATALOG_MISSING")
    previous_catalog = json.loads(PREVIOUS_CONTRACT_CATALOG.read_text(encoding="utf-8"))
    contract_change = reject_undeclared_breaking_change(
        previous_catalog,
        current_catalog,
        previous_version=PREVIOUS_CONTRACT_VERSION,
        current_version=CONTRACT_VERSION,
    )
    _write_json(catalog_path, current_catalog)
    ui_catalog_path = output / "ui-bundle.json"
    _write_json(ui_catalog_path, integrity["ui_bundle"])
    cyclone, spdx = _write_sboms(output)
    key = load_private_key(signing_key)
    public_pem = public_key_pem(key)
    public_path = output / "release-public-key.pem"
    public_path.write_bytes(public_pem)
    fingerprint = hashlib.sha256(public_pem).hexdigest()
    manifest_path = output / "release-manifest.json"
    manifest = {
        "format_version": 1,
        "product": "agent-os-finance",
        "version": __version__,
        "application_version": __version__,
        "wheel_sha256": wheel["sha256"],
        "ui_bundle_sha256": integrity["ui_bundle"]["sha256"],
        "schema_catalog_sha256": integrity["schemas"]["sha256"],
        "supported_store_versions": [1, 2, 3],
        "minimum_source_version": "0.2.0",
        "offline_verified": offline_verified,
        "network_dependencies": [],
        "artifact": {"name": Path(wheel["path"]).name, "sha256": wheel["sha256"]},
        "reproducible_build": {"verified": True, "independent_build_count": 2, "source_date_epoch": SOURCE_DATE_EPOCH},
        "supported_versions": {"application_minimum": "0.2.0", "store_schema": [1, 2, 3], "archive_format": [1], "contract": CONTRACT_VERSION},
        "support_boundaries": {
            "platforms": ["macOS-arm64", "macOS-x86_64", "Linux-x86_64"],
            "python": ["3.11", "3.12", "3.13", "3.14"],
            "keychains": ["macOS-Keychain", "SecretService-compatible"],
            "import_profiles": ["GenericFinanceCsvV1", "GermanMultiAccountCsvV1"],
            "migration_sources": ["0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0", "0.8.0", "0.9.0", "1.0.0"],
            "maximum_configured_archive_bytes": 536870912,
            "maximum_tested_archive_bytes": 524288,
            "maximum_tested_csv_rows": 10000,
        },
        "build_environment": {"python": platform.python_version(), "platform": platform.system(), "network_required": False},
        "integrity": integrity,
        "sbom": {
            cyclone.name: sha256_file(cyclone),
            spdx.name: sha256_file(spdx),
        },
        "contract_catalog_sha256": sha256_file(catalog_path),
        "test_summary": test_summary or {"python": 0, "frontend": 0, "schemas": 0},
        "acceptance": (
            {
                "status": acceptance["status"],
                "source": acceptance["source"],
                "projection_sha256": acceptance["projection_sha256"],
                "report_sha256": sha256_file(acceptance_path),
            }
            if acceptance is not None and acceptance_path is not None
            else None
        ),
        "security_review": {
            "critical_findings": critical_findings,
            "high_findings": high_findings,
            "release_blocked": critical_findings > 0 or high_findings > 0,
        },
        "contract_change": contract_change,
        "signing": {"algorithm": "Ed25519", "public_key_sha256": fingerprint},
    }
    _write_json(manifest_path, manifest)
    signed = [
        Path(wheel["path"]), manifest_path, catalog_path, ui_catalog_path, cyclone, spdx
    ]
    if acceptance_path is not None:
        signed.append(acceptance_path)
    manifest_signature = sign_file(
        manifest_path, key, output / "release-manifest.sig"
    )
    signatures = [
        sign_file(path, key)
        for path in signed
        if path != manifest_path
    ]
    signatures.append(manifest_signature)
    checksums_path = output / "checksums.sha256"
    checksummed = [*signed, public_path]
    checksums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in sorted(checksummed)),
        encoding="ascii",
    )
    signatures.append(sign_file(checksums_path, key))
    return {
        "release_manifest": str(manifest_path),
        "wheel": wheel,
        "signed_artifacts": [str(path) for path in signed],
        "signatures": [str(path) for path in signatures],
        "public_key": str(public_path),
        "checksums": str(checksums_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--signing-key", required=True)
    parser.add_argument("--python-tests", type=int, required=True)
    parser.add_argument("--frontend-tests", type=int, required=True)
    parser.add_argument("--schemas", type=int, required=True)
    parser.add_argument("--offline-verified", action="store_true")
    parser.add_argument("--acceptance-report")
    parser.add_argument("--critical-findings", type=int, required=True)
    parser.add_argument("--high-findings", type=int, required=True)
    args = parser.parse_args(argv)
    summary = {
        "python": args.python_tests,
        "frontend": args.frontend_tests,
        "schemas": args.schemas,
    }
    print(
        json.dumps(
            create_release(
                args.output,
                args.signing_key,
                test_summary=summary,
                offline_verified=args.offline_verified,
                acceptance_report=args.acceptance_report,
                critical_findings=args.critical_findings,
                high_findings=args.high_findings,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
