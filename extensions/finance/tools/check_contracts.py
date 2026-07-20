#!/usr/bin/env python3
"""Dependency-free structural checks for the Finance JSON contracts."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


FINANCE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = FINANCE_ROOT / "schemas"
SCHEMA_FILES = tuple(sorted(SCHEMA_ROOT.glob("*.schema.json")))
EXPECTED_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer:
        return current
    if not pointer.startswith("/"):
        raise KeyError(f"invalid JSON pointer: {pointer}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _walk_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                refs.append(child)
            refs.extend(_walk_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_walk_refs(child))
    return refs


def _resolve_ref_with_path(
    source_path: Path, documents: dict[Path, Any], ref: str
) -> tuple[Path, Any]:
    file_part, separator, fragment = ref.partition("#")
    target_path = source_path if not file_part else (source_path.parent / file_part).resolve()
    if target_path not in documents:
        raise KeyError(f"missing referenced schema {target_path}")
    pointer = fragment if separator else ""
    return target_path, _json_pointer(documents[target_path], pointer)


def _resolve_ref(source_path: Path, documents: dict[Path, Any], ref: str) -> Any:
    return _resolve_ref_with_path(source_path, documents, ref)[1]


def _is_instance_of(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    schema_path: Path,
    documents: dict[Path, Any],
    instance_path: str,
) -> list[str]:
    """Validate the JSON Schema keywords used by this contract package."""

    errors: list[str] = []
    if "$ref" in schema:
        target_path, target = _resolve_ref_with_path(schema_path, documents, schema["$ref"])
        return _validate_value(value, target, target_path, documents, instance_path)

    for child in schema.get("allOf", []):
        errors.extend(_validate_value(value, child, schema_path, documents, instance_path))

    any_of = schema.get("anyOf")
    if any_of:
        branch_errors = [
            _validate_value(value, child, schema_path, documents, instance_path)
            for child in any_of
        ]
        if all(branch for branch in branch_errors):
            errors.append(f"{instance_path}: does not satisfy anyOf")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{instance_path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{instance_path}: value {value!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type and not _is_instance_of(value, expected_type):
        return [f"{instance_path}: expected {expected_type}, got {type(value).__name__}"]

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{instance_path}: missing required property {key!r}")
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{instance_path}: too few properties")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child_value in value.items():
            child_path = f"{instance_path}.{key}"
            if key in properties:
                errors.extend(
                    _validate_value(child_value, properties[key], schema_path, documents, child_path)
                )
            elif additional is False:
                errors.append(f"{child_path}: additional property is forbidden")
            elif isinstance(additional, dict):
                errors.extend(
                    _validate_value(child_value, additional, schema_path, documents, child_path)
                )

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{instance_path}: too few items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{instance_path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_value(item, item_schema, schema_path, documents, f"{instance_path}[{index}]")
                )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{instance_path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{instance_path}: string is too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{instance_path}: does not match {schema['pattern']!r}")
        try:
            if schema.get("format") == "date":
                date.fromisoformat(value)
            elif schema.get("format") == "date-time":
                datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{instance_path}: invalid {schema['format']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{instance_path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{instance_path}: value is above maximum")

    return errors


def _validate_catalog_example(
    example: dict[str, Any],
    catalog: dict[str, Any],
    catalog_path: Path,
    documents: dict[Path, Any],
    discriminator: str,
    base_name: str,
) -> list[str]:
    variant_name = example.get(discriminator)
    defs = catalog["$defs"]
    if variant_name not in defs:
        return [f"unknown {discriminator} {variant_name!r}"]

    base = defs[base_name]
    variant_overlay = defs[variant_name]["allOf"][1]
    merged_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(dict.fromkeys(base.get("required", []) + variant_overlay.get("required", []))),
        "properties": {**base.get("properties", {}), **variant_overlay.get("properties", {})},
    }
    return _validate_value(example, merged_schema, catalog_path, documents, "$")


def _check_discriminated_catalog(document: dict[str, Any], discriminator: str, type_def: str) -> list[str]:
    errors: list[str] = []
    defs = document.get("$defs", {})
    declared = set(defs.get(type_def, {}).get("enum", []))
    catalog_refs = document.get("oneOf", [])
    catalog_names = {
        entry["$ref"].rsplit("/", 1)[-1]
        for entry in catalog_refs
        if isinstance(entry, dict) and isinstance(entry.get("$ref"), str)
    }
    if declared != catalog_names:
        errors.append(
            f"{type_def} and oneOf catalog differ: "
            f"missing={sorted(declared - catalog_names)}, extra={sorted(catalog_names - declared)}"
        )

    for name in sorted(catalog_names):
        variant = defs.get(name)
        if not isinstance(variant, dict):
            errors.append(f"missing $defs/{name}")
            continue
        try:
            const_value = variant["allOf"][1]["properties"][discriminator]["const"]
            payload_ref = variant["allOf"][1]["properties"]["payload"]["$ref"]
        except (KeyError, IndexError, TypeError):
            errors.append(f"{name} is not a concrete discriminated envelope")
            continue
        if const_value != name:
            errors.append(f"{name} uses mismatching discriminator {const_value!r}")
        payload_name = payload_ref.rsplit("/", 1)[-1]
        if payload_name not in defs:
            errors.append(f"{name} references missing payload {payload_name}")
    return errors


def check_contracts() -> list[str]:
    errors: list[str] = []
    documents: dict[Path, Any] = {}

    for path in SCHEMA_FILES:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: cannot parse JSON: {exc}")
            continue
        documents[path.resolve()] = document
        if document.get("$schema") != EXPECTED_DRAFT:
            errors.append(f"{path.name}: expected JSON Schema Draft 2020-12")
        if not document.get("$id", "").startswith("https://agent-os.local/finance/"):
            errors.append(f"{path.name}: $id must use the non-routable local contract namespace")

    for path, document in documents.items():
        for ref in _walk_refs(document):
            if ref.startswith(("http://", "https://")):
                errors.append(f"{path.name}: remote runtime $ref is forbidden: {ref}")
                continue
            try:
                _resolve_ref(path, documents, ref)
            except (KeyError, IndexError, TypeError) as exc:
                errors.append(f"{path.name}: unresolved $ref {ref!r}: {exc}")

    commands = documents.get((SCHEMA_ROOT / "commands.schema.json").resolve())
    events = documents.get((SCHEMA_ROOT / "events.schema.json").resolve())
    if commands:
        errors.extend(_check_discriminated_catalog(commands, "command_type", "CommandType"))
    if events:
        errors.extend(_check_discriminated_catalog(events, "event_type", "EventType"))

    examples_root = FINANCE_ROOT / "examples"
    for example_path in sorted(examples_root.glob("*.json")):
        try:
            example = json.loads(example_path.read_text(encoding="utf-8"))
            if example_path.name.endswith(".command.json") and commands:
                example_errors = _validate_catalog_example(
                    example,
                    commands,
                    (SCHEMA_ROOT / "commands.schema.json").resolve(),
                    documents,
                    "command_type",
                    "CommandBase",
                )
            elif example_path.name.endswith(".event.json") and events:
                example_errors = _validate_catalog_example(
                    example,
                    events,
                    (SCHEMA_ROOT / "events.schema.json").resolve(),
                    documents,
                    "event_type",
                    "EventBase",
                )
            else:
                example_errors = ["example filename must end in .command.json or .event.json"]
            errors.extend(f"{example_path.name}: {error}" for error in example_errors)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            errors.append(f"{example_path.name}: cannot validate example: {exc}")

    return errors


def main() -> int:
    errors = check_contracts()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    example_count = len(tuple((FINANCE_ROOT / "examples").glob("*.json")))
    print(
        f"OK: {len(SCHEMA_FILES)} Finance schemas and {example_count} examples parsed; "
        "all references, catalogs and example envelopes are consistent."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
