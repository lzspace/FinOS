"""Semantic-version classification for JSON Schema contract catalogs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ContractCompatibilityError(ValueError):
    pass


_RANK = {"PATCH": 0, "MINOR": 1, "MAJOR": 2}


@dataclass(frozen=True)
class ContractChange:
    level: str
    path: str
    reason: str


def create_contract_catalog(schema_root: str | Path) -> dict[str, Any]:
    root = Path(schema_root)
    schemas: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for path in sorted(root.glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        schemas[path.name] = document
        hashes[path.name] = hashlib.sha256(canonical).hexdigest()
    return {"catalog_format": 1, "schemas": schemas, "sha256": hashes}


def _compare_schema(old: Any, new: Any, path: str, changes: list[ContractChange]) -> None:
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            changes.append(ContractChange("MAJOR", path, "schema constraint changed"))
        return
    if old.get("type") != new.get("type"):
        changes.append(ContractChange("MAJOR", f"{path}/type", "type changed"))
    if "const" in old and old.get("const") != new.get("const"):
        changes.append(ContractChange("MAJOR", f"{path}/const", "constant changed"))
    old_enum = set(old.get("enum", []))
    new_enum = set(new.get("enum", []))
    if old_enum - new_enum:
        changes.append(ContractChange("MAJOR", f"{path}/enum", "enum values removed"))
    if new_enum - old_enum:
        changes.append(ContractChange("MINOR", f"{path}/enum", "enum values added"))
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    if new_required - old_required:
        changes.append(ContractChange("MAJOR", f"{path}/required", "required fields added"))
    if old_required - new_required:
        changes.append(ContractChange("MINOR", f"{path}/required", "required fields relaxed"))
    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    if isinstance(old_properties, dict) and isinstance(new_properties, dict):
        for name in sorted(set(old_properties) - set(new_properties)):
            changes.append(ContractChange("MAJOR", f"{path}/properties/{name}", "field removed"))
        for name in sorted(set(new_properties) - set(old_properties)):
            level = "MAJOR" if name in new_required else "MINOR"
            changes.append(ContractChange(level, f"{path}/properties/{name}", "field added"))
        for name in sorted(set(old_properties) & set(new_properties)):
            _compare_schema(
                old_properties[name], new_properties[name], f"{path}/properties/{name}", changes
            )
    if old.get("additionalProperties", True) is True and new.get("additionalProperties", True) is False:
        changes.append(
            ContractChange("MAJOR", f"{path}/additionalProperties", "unknown fields forbidden")
        )
    for keyword in ("minimum", "exclusiveMinimum", "minLength", "minItems"):
        if keyword in new and new.get(keyword, 0) > old.get(keyword, 0):
            changes.append(ContractChange("MAJOR", f"{path}/{keyword}", "constraint tightened"))
    for keyword in ("maximum", "exclusiveMaximum", "maxLength", "maxItems"):
        if keyword in new and keyword in old and new[keyword] < old[keyword]:
            changes.append(ContractChange("MAJOR", f"{path}/{keyword}", "constraint tightened"))


def compare_contract_catalogs(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    changes: list[ContractChange] = []
    old_schemas = old.get("schemas", {})
    new_schemas = new.get("schemas", {})
    for name in sorted(set(old_schemas) - set(new_schemas)):
        changes.append(ContractChange("MAJOR", name, "schema removed"))
    for name in sorted(set(new_schemas) - set(old_schemas)):
        changes.append(ContractChange("MINOR", name, "schema added"))
    for name in sorted(set(old_schemas) & set(new_schemas)):
        _compare_schema(old_schemas[name], new_schemas[name], name, changes)
    level = max((item.level for item in changes), key=_RANK.get, default="PATCH")
    return {
        "classification": level,
        "compatible": level != "MAJOR",
        "changes": [item.__dict__ for item in changes],
    }


def classify_contract_change(old: dict[str, Any], new: dict[str, Any]) -> str:
    return str(compare_contract_catalogs(old, new)["classification"])


def _declared_change(previous: str, current: str) -> str:
    try:
        before = tuple(int(part) for part in previous.split("."))
        after = tuple(int(part) for part in current.split("."))
    except ValueError as exc:
        raise ContractCompatibilityError("FINANCE_CONTRACT_VERSION_INVALID") from exc
    if len(before) != 3 or len(after) != 3 or after < before:
        raise ContractCompatibilityError("FINANCE_CONTRACT_VERSION_INVALID")
    if after == before:
        return "PATCH"
    if after[0] > before[0]:
        return "MAJOR"
    if after[1] > before[1]:
        return "MINOR"
    return "PATCH"


def reject_undeclared_breaking_change(
    old: dict[str, Any], new: dict[str, Any], *, previous_version: str, current_version: str
) -> dict[str, Any]:
    comparison = compare_contract_catalogs(old, new)
    declared = _declared_change(previous_version, current_version)
    if _RANK[comparison["classification"]] > _RANK[declared]:
        raise ContractCompatibilityError("FINANCE_CONTRACT_BREAKING_CHANGE_UNDECLARED")
    return {**comparison, "declared_change": declared}


CompareContractCatalogs = compare_contract_catalogs
ClassifyContractChange = classify_contract_change
RejectUndeclaredBreakingChange = reject_undeclared_breaking_change


__all__ = [
    "ClassifyContractChange",
    "CompareContractCatalogs",
    "ContractCompatibilityError",
    "RejectUndeclaredBreakingChange",
    "classify_contract_change",
    "compare_contract_catalogs",
    "create_contract_catalog",
    "reject_undeclared_breaking_change",
]
