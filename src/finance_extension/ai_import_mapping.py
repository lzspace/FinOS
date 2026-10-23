"""Deterministic import fingerprints and user-confirmed local-AI mappings."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .local_model import (
    INFERENCE_POLICY_VERSION,
    LocalModelError,
    LocalModelService,
    StructuredInferenceRequest,
)
from .storage_policy import validate_runtime_path
from .store import LocalFinanceStore


FINGERPRINT_ALGORITHM_VERSION = "1.0.0"
MAPPING_SCHEMA_VERSION = "1.0.0"
TARGET_SCHEMA_VERSION = "CheckingTransactionImportV1"
PARSER_VERSION = "ai-mapping@1.0.0"
ALLOWED_TARGET_FIELDS = frozenset(
    {
        "booking_date",
        "value_date",
        "transaction_type_raw",
        "counterparty",
        "description",
        "amount",
        "currency",
    }
)
REQUIRED_TARGET_FIELDS = frozenset({"booking_date", "amount", "currency"})
ALLOWED_TRANSFORMATIONS = frozenset(
    {
        "PARSE_DATE",
        "PARSE_DECIMAL",
        "NORMALIZE_CURRENCY",
        "TRIM",
        "CONCATENATE",
        "CONSTANT",
        "SIGN_NORMALIZATION",
    }
)
ALLOWED_COMPATIBILITY_RULES = frozenset(
    {"ALLOW_ADDITIONAL_COLUMNS", "ALLOW_SECTION_TITLE_CHANGE"}
)
DELIMITERS = (";", ",", "\t", "|")
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SAMPLE_ROWS = 5


class ImportMappingError(ValueError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    command_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "occurred_at": _now(),
        "correlation_id": command_id,
        "causation_id": command_id,
        "payload": payload,
    }


def _append(
    store: LocalFinanceStore,
    command_type: str,
    idempotency_key: str,
    events: list[dict[str, Any]],
) -> list[int]:
    return store.append_events(
        {
            "command_id": events[0]["correlation_id"],
            "command_type": command_type,
            "idempotency_key": f"{command_type}:{idempotency_key}",
        },
        events,
    )


def _decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding), "utf-8" if encoding == "utf-8-sig" else encoding
        except UnicodeDecodeError:
            continue
    raise ImportMappingError("FINANCE_IMPORT_ENCODING_UNSUPPORTED")


def _delimiter(lines: list[str]) -> str:
    candidates = {
        delimiter: sum(line.count(delimiter) for line in lines[:20]) for delimiter in DELIMITERS
    }
    selected = max(DELIMITERS, key=lambda item: (candidates[item], -DELIMITERS.index(item)))
    if candidates[selected] == 0:
        raise ImportMappingError("IMPORT_MAPPING_STRUCTURE_UNRECOGNIZED")
    return selected


def _normalized(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _date_shape(value: str) -> str | None:
    stripped = value.strip()
    for pattern, shape in (
        (r"\d{2}\.\d{2}\.\d{2}", "DD.MM.YY"),
        (r"\d{2}\.\d{2}\.\d{4}", "DD.MM.YYYY"),
        (r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD"),
    ):
        if re.fullmatch(pattern, stripped):
            return shape
    return None


def _decimal_shape(value: str) -> str | None:
    stripped = value.strip().replace(" ", "")
    if re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{1,8}", stripped):
        return "DE"
    if re.fullmatch(r"[+-]?\d+(?:\.\d{1,8})?", stripped):
        return "EN"
    return None


def _inspect(raw: bytes) -> dict[str, Any]:
    text, encoding = _decode(raw)
    lines = [line for line in text.splitlines() if line.strip()]
    delimiter = _delimiter(lines)
    parsed = list(csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"'))
    candidates = [
        (index, row)
        for index, row in enumerate(parsed[:50])
        if len(row) >= 2 and sum(bool(cell.strip()) for cell in row) >= 2
    ]
    if not candidates:
        raise ImportMappingError("IMPORT_MAPPING_STRUCTURE_UNRECOGNIZED")
    header_index, header = max(candidates, key=lambda item: (len(item[1]), -item[0]))
    columns = [cell.strip() for cell in header]
    if any(not column for column in columns) or len(columns) != len(set(columns)):
        raise ImportMappingError("IMPORT_MAPPING_COLUMNS_AMBIGUOUS")
    title = "Import"
    for previous in reversed(parsed[:header_index]):
        values = [cell.strip() for cell in previous if cell.strip()]
        if len(values) == 1:
            title = values[0]
            break
    rows: list[dict[str, str]] = []
    for row in parsed[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(columns):
            continue
        rows.append(dict(zip(columns, (cell.strip() for cell in row), strict=True)))
    if not rows:
        raise ImportMappingError("IMPORT_MAPPING_NO_DATA_ROWS")
    date_shapes = sorted(
        {shape for row in rows[:20] for value in row.values() if (shape := _date_shape(value))}
    )
    decimal_shapes = sorted(
        {
            shape
            for row in rows[:20]
            for value in row.values()
            if (shape := _decimal_shape(value))
        }
    )
    return {
        "encoding": encoding,
        "delimiter": delimiter,
        "quote_character": '"',
        "section_title": title,
        "columns": columns,
        "rows": rows,
        "date_shapes": date_shapes,
        "decimal_shapes": decimal_shapes,
    }


def build_import_format_fingerprint(structure: dict[str, Any]) -> dict[str, Any]:
    canonical_value = {
        "algorithm_version": FINGERPRINT_ALGORITHM_VERSION,
        "encoding": structure["encoding"],
        "delimiter": structure["delimiter"],
        "quote_character": structure["quote_character"],
        "section_structure": ["TABULAR"],
        "normalized_section_titles": [_normalized(structure["section_title"])],
        "normalized_column_names": [_normalized(item) for item in structure["columns"]],
        "column_order": list(range(len(structure["columns"]))),
        "column_count": len(structure["columns"]),
        "inferred_date_shapes": structure["date_shapes"],
        "inferred_decimal_shapes": structure["decimal_shapes"],
        "static_markers": [],
    }
    canonical = json.dumps(canonical_value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "fingerprint_id": f"fingerprint_{digest[:32]}",
        **canonical_value,
        "canonical_representation": canonical,
        "sha256": digest,
    }


def _redact(column: str, value: str) -> str:
    normalized_column = _normalized(column)
    if re.search(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", value, re.IGNORECASE):
        return "<IBAN>"
    if any(
        marker in normalized_column
        for marker in (
            "name",
            "empfänger",
            "auftraggeber",
            "text",
            "beschreibung",
            "verwendung",
        )
    ):
        return "<PERSON>" if "name" in normalized_column else "<redacted-sample>"
    if "konto" in normalized_column:
        return "<ACCOUNT>"
    if re.fullmatch(r"\d{6,}", value.replace(" ", "")):
        return "<REFERENCE>"
    if len(value) > 48 and not _date_shape(value) and not _decimal_shape(value):
        return "<redacted-sample>"
    return value


def _sample_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    candidates = [
        rows[0],
        *[row for row in rows if any(value.startswith("-") for value in row.values())],
        *[row for row in rows if any(value.startswith("+") for value in row.values())],
        *[row for row in rows if any(not value for value in row.values())],
    ]
    for row in candidates:
        if row not in selected:
            selected.append(row)
        if len(selected) == MAX_SAMPLE_ROWS:
            break
    return [{key: _redact(key, value) for key, value in row.items()} for row in selected]


MAPPING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "section_type",
        "field_mappings",
        "transformations",
        "constants",
        "ignored_columns",
        "confidence",
        "warnings",
    ],
    "properties": {
        "section_type": {"enum": ["CHECKING", "SAVINGS", "BROKERAGE", "UNKNOWN"]},
        "field_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_column", "target_field", "confidence"],
                "properties": {
                    "source_column": {"type": "string"},
                    "target_field": {"enum": sorted(ALLOWED_TARGET_FIELDS)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "transformations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_field", "type", "parameters"],
                "properties": {
                    "target_field": {"enum": sorted(ALLOWED_TARGET_FIELDS)},
                    "type": {"enum": sorted(ALLOWED_TRANSFORMATIONS)},
                    "parameters": {"type": "object"},
                },
            },
        },
        "constants": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"currency": {"type": "string", "pattern": "^[A-Z]{3}$"}},
        },
        "ignored_columns": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def _parse_date(value: str, format_name: str) -> str:
    from datetime import datetime as parsed_datetime

    formats = {"DD.MM.YY": "%d.%m.%y", "DD.MM.YYYY": "%d.%m.%Y", "YYYY-MM-DD": "%Y-%m-%d"}
    if format_name not in formats:
        raise ValueError
    return parsed_datetime.strptime(value.strip(), formats[format_name]).date().isoformat()


def _parse_decimal(value: str, parameters: dict[str, Any]) -> str:
    decimal_separator = parameters.get("decimal_separator", ".")
    thousands_separator = parameters.get("thousands_separator", "")
    if decimal_separator not in {".", ","} or thousands_separator not in {"", ".", ",", " "}:
        raise ValueError
    if decimal_separator == thousands_separator:
        raise ValueError
    normalized = value.strip().replace(thousands_separator, "") if thousands_separator else value.strip()
    normalized = normalized.replace(decimal_separator, ".")
    parsed = Decimal(normalized)
    if not parsed.is_finite():
        raise ValueError
    return str(parsed)


def validate_import_mapping(
    mapping: dict[str, Any],
    *,
    columns: list[str],
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    errors: list[str] = []
    schema_errors = sorted(
        Draft202012Validator(MAPPING_OUTPUT_SCHEMA).iter_errors(mapping),
        key=lambda error: list(error.absolute_path),
    )
    if schema_errors:
        errors.append("IMPORT_MAPPING_SCHEMA_INVALID")
    field_mappings = mapping.get("field_mappings", [])
    transformations = mapping.get("transformations", [])
    constants = mapping.get("constants", {})
    ignored = mapping.get("ignored_columns", [])
    if not isinstance(field_mappings, list) or not isinstance(transformations, list):
        field_mappings = []
        transformations = []
    if not isinstance(constants, dict):
        constants = {}
    if not isinstance(ignored, list):
        ignored = []
    sources = [item.get("source_column") for item in field_mappings if isinstance(item, dict)]
    targets = [item.get("target_field") for item in field_mappings if isinstance(item, dict)]
    if any(source not in columns for source in sources):
        errors.append("IMPORT_MAPPING_SOURCE_COLUMN_UNKNOWN")
    if any(target not in ALLOWED_TARGET_FIELDS for target in targets):
        errors.append("IMPORT_MAPPING_TARGET_FIELD_UNKNOWN")
    if len(targets) != len(set(targets)):
        errors.append("IMPORT_MAPPING_TARGET_FIELD_DUPLICATE")
    if len(sources) != len(set(sources)):
        errors.append("IMPORT_MAPPING_SOURCE_COLUMN_DUPLICATE")
    if set(targets) & set(constants):
        errors.append("IMPORT_MAPPING_TARGET_FIELD_CONFLICT")
    provided = set(targets) | set(constants)
    if not REQUIRED_TARGET_FIELDS.issubset(provided):
        errors.append("IMPORT_MAPPING_REQUIRED_FIELD_MISSING")
    if any(column not in columns for column in ignored):
        errors.append("IMPORT_MAPPING_IGNORED_COLUMN_UNKNOWN")
    transform_by_target: dict[str, dict[str, Any]] = {}
    for transformation in transformations:
        if not isinstance(transformation, dict) or transformation.get("type") not in ALLOWED_TRANSFORMATIONS:
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        target = transformation.get("target_field")
        if target not in ALLOWED_TARGET_FIELDS or target in transform_by_target:
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        parameters = transformation.get("parameters", {})
        transform_type = transformation["type"]
        if not isinstance(parameters, dict):
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        if transform_type == "PARSE_DATE" and parameters.get("format") not in {
            "DD.MM.YY", "DD.MM.YYYY", "YYYY-MM-DD"
        }:
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        if transform_type == "SIGN_NORMALIZATION" and parameters.get("multiplier") not in {-1, 1}:
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        if transform_type == "CONCATENATE":
            concat_sources = parameters.get("source_columns")
            if (
                not isinstance(concat_sources, list)
                or not concat_sources
                or any(item not in columns for item in concat_sources)
            ):
                errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
                continue
        if transform_type == "CONSTANT" and "value" not in parameters:
            errors.append("IMPORT_MAPPING_TRANSFORMATION_INVALID")
            continue
        transform_by_target[target] = transformation
    counters = {
        "rows_total": len(rows),
        "rows_valid": 0,
        "rows_invalid": 0,
        "date_parse_errors": 0,
        "decimal_parse_errors": 0,
        "currency_conflicts": 0,
        "missing_required_values": 0,
    }
    if not errors:
        for row in rows:
            valid = True
            output = dict(constants)
            for item in field_mappings:
                source, target = item["source_column"], item["target_field"]
                value = row[source]
                transformation = transform_by_target.get(target)
                try:
                    if transformation and transformation["type"] == "PARSE_DATE":
                        value = _parse_date(value, transformation.get("parameters", {}).get("format", ""))
                    elif transformation and transformation["type"] == "PARSE_DECIMAL":
                        value = _parse_decimal(value, transformation.get("parameters", {}))
                    elif transformation and transformation["type"] == "TRIM":
                        value = value.strip()
                    elif transformation and transformation["type"] == "NORMALIZE_CURRENCY":
                        value = value.strip().upper()
                    elif transformation and transformation["type"] == "SIGN_NORMALIZATION":
                        value = str(Decimal(value.replace(",", ".")) * transformation["parameters"]["multiplier"])
                    elif transformation and transformation["type"] == "CONSTANT":
                        value = str(transformation["parameters"]["value"])
                    elif transformation and transformation["type"] == "CONCATENATE":
                        separator = str(transformation["parameters"].get("separator", " "))
                        value = separator.join(row[column] for column in transformation["parameters"]["source_columns"])
                except (ValueError, InvalidOperation):
                    valid = False
                    if target in {"booking_date", "value_date"}:
                        counters["date_parse_errors"] += 1
                    elif target == "amount":
                        counters["decimal_parse_errors"] += 1
                output[target] = value
            if any(not str(output.get(field, "")).strip() for field in REQUIRED_TARGET_FIELDS):
                counters["missing_required_values"] += 1
                valid = False
            if output.get("currency") and not re.fullmatch(r"[A-Z]{3}", str(output["currency"])):
                counters["currency_conflicts"] += 1
                valid = False
            counters["rows_valid" if valid else "rows_invalid"] += 1
    else:
        counters["rows_invalid"] = len(rows)
    warnings = list(mapping.get("warnings", [])) if isinstance(mapping.get("warnings", []), list) else []
    status = "INVALID" if errors or counters["rows_invalid"] else "VALID_WITH_WARNINGS" if warnings else "VALID"
    canonical = json.dumps({"mapping": mapping, "result": counters}, sort_keys=True, separators=(",", ":"))
    return {
        "validation_result_id": f"mapping_validation_{hashlib.sha256(canonical.encode()).hexdigest()[:32]}",
        "status": status,
        **counters,
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "validated_at": _now(),
    }


def _profiles(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    profiles = {event["aggregate_id"]: event["payload"] for event in store.events("ImportProfileCreated")}
    for event in store.events("ImportProfileSuperseded"):
        if event["payload"]["profile_id"] in profiles:
            profiles[event["payload"]["profile_id"]]["status"] = "SUPERSEDED"
    return profiles


def _proposals(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {event["aggregate_id"]: event["payload"] for event in store.events("ImportMappingProposed")}


def _fingerprints(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["aggregate_id"]: event["payload"]
        for event in store.events("ImportFormatFingerprintCreated")
    }


def _format_change(
    previous: dict[str, Any], current: dict[str, Any], profile: dict[str, Any]
) -> tuple[list[str], str]:
    changes: list[str] = []
    old_columns = previous["normalized_column_names"]
    new_columns = current["normalized_column_names"]
    old_set, new_set = set(old_columns), set(new_columns)
    removed, added = old_set - new_set, new_set - old_set
    if removed:
        changes.append("COLUMN_REMOVED")
    if added:
        changes.append("COLUMN_ADDED")
    if not removed and not added and old_columns != new_columns:
        changes.append("COLUMN_REORDERED")
    if removed and added and len(removed) == len(added):
        changes.append("COLUMN_RENAMED")
    if previous["inferred_date_shapes"] != current["inferred_date_shapes"]:
        changes.append("DATE_FORMAT_CHANGED")
    if previous["inferred_decimal_shapes"] != current["inferred_decimal_shapes"]:
        changes.append("DECIMAL_FORMAT_CHANGED")
    if previous["normalized_section_titles"] != current["normalized_section_titles"]:
        changes.append("SECTION_TITLE_CHANGED")
    if previous["encoding"] != current["encoding"]:
        changes.append("ENCODING_CHANGED")
    if previous["delimiter"] != current["delimiter"]:
        changes.append("DELIMITER_CHANGED")
    if not changes:
        changes.append("UNKNOWN_STRUCTURAL_CHANGE")
    required_sources = {
        _normalized(item["source_column"])
        for item in profile.get("mappings", [])
        if item.get("target_field") in REQUIRED_TARGET_FIELDS
    }
    severity = "BREAKING" if removed & required_sources else "REVIEW_REQUIRED"
    return changes, severity


def _manual_mapping() -> dict[str, Any]:
    return {
        "section_type": "CHECKING",
        "field_mappings": [],
        "transformations": [],
        "constants": {},
        "ignored_columns": [],
        "confidence": 0,
        "warnings": ["LOCAL_AI_UNAVAILABLE"],
    }


def _proposal(
    *,
    mapping: dict[str, Any],
    validation: dict[str, Any],
    analysis_id: str,
    content_hash: str,
    fingerprint_id: str,
    section_id: str,
    bank_identifier: str,
    model_id: str,
    model_version: str,
    runtime_version: str = "not-applicable",
    structured_output_backend: str = "MANUAL",
) -> dict[str, Any]:
    return {
        "proposal_id": f"mapping_proposal_{uuid.uuid4().hex}",
        "analysis_id": analysis_id,
        "content_hash": content_hash,
        "fingerprint_id": fingerprint_id,
        "section_id": section_id,
        "bank_identifier": bank_identifier,
        "model_id": model_id,
        "model_version": model_version,
        "runtime_version": runtime_version,
        "structured_output_backend": structured_output_backend,
        "inference_policy_version": INFERENCE_POLICY_VERSION,
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        **mapping,
        "validation": validation,
        "created_at": _now(),
    }


def _proposal_events(
    proposal: dict[str, Any], command_id: str
) -> list[dict[str, Any]]:
    proposal_id = proposal["proposal_id"]
    return [
        _event("ImportMappingProposed", "ImportMappingProposal", proposal_id, 1, command_id, proposal),
        _event(
            "ImportMappingValidationCompleted",
            "ImportMappingProposal",
            proposal_id,
            2,
            command_id,
            {"proposal_id": proposal_id, **proposal["validation"]},
        ),
    ]


def analyze_unknown_import_format(
    store: LocalFinanceStore,
    local_model: LocalModelService,
    source_file_path: str | Path,
    *,
    bank_identifier: str,
    regenerate: bool = False,
) -> dict[str, Any]:
    path = validate_runtime_path(
        source_file_path,
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ImportMappingError("FINANCE_IMPORT_FILE_SIZE_INVALID")
    structure = _inspect(raw)
    fingerprint = build_import_format_fingerprint(structure)
    content_hash = hashlib.sha256(raw).hexdigest()
    analysis_id = f"unknown_analysis_{content_hash}"
    section_id = f"unknown_section_{fingerprint['sha256'][:16]}"
    active_profiles = {
        profile_id: profile
        for profile_id, profile in _profiles(store).items()
        if profile.get("status") == "ACTIVE"
    }
    exact_matches = [
        profile
        for profile in active_profiles.values()
        if profile["fingerprint_id"] == fingerprint["fingerprint_id"]
        and profile["bank_identifier"] == bank_identifier
    ]
    exact = exact_matches[0] if len(exact_matches) == 1 else None
    command_id = _id("cmd")
    fingerprint_version = store.next_aggregate_version(
        "ImportFormatFingerprint", fingerprint["fingerprint_id"]
    )
    events = [
        _event(
            "ImportFormatFingerprintCreated",
            "ImportFormatFingerprint",
            fingerprint["fingerprint_id"],
            fingerprint_version,
            command_id,
            {**fingerprint, "analysis_id": analysis_id, "section_id": section_id},
        )
    ]
    if not store.has_import_content_hash(raw):
        store.store_import_file(analysis_id, path, raw, PARSER_VERSION, status="ANALYZED")
    if len(exact_matches) > 1:
        _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:ambiguous", events)
        return {
            "status": "AMBIGUOUS",
            "analysis_id": analysis_id,
            "fingerprint": fingerprint,
            "matching_profiles": exact_matches,
            "manual_mapping_available": True,
            "ai_used": False,
            "error_code": "IMPORT_FINGERPRINT_AMBIGUOUS",
        }
    if exact:
        events.append(
            _event(
                "ImportProfileReused",
                "ImportProfile",
                exact["profile_id"],
                store.next_aggregate_version("ImportProfile", exact["profile_id"]),
                command_id,
                {
                    "profile_id": exact["profile_id"],
                    "fingerprint_id": fingerprint["fingerprint_id"],
                    "analysis_id": analysis_id,
                    "reused_at": _now(),
                    "ai_used": False,
                },
            )
        )
        _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:known", events)
        return {"status": "KNOWN_EXACT", "analysis_id": analysis_id, "fingerprint": fingerprint, "profile": exact, "ai_used": False}
    decided_proposals = {
        event["aggregate_id"]
        for event in store.events("ImportMappingConfirmed")
        + store.events("ImportMappingRejected")
    }
    existing_proposal = next(
        (
            proposal
            for proposal in reversed(list(_proposals(store).values()))
            if proposal["content_hash"] == content_hash
            and proposal["bank_identifier"] == bank_identifier
            and proposal["proposal_id"] not in decided_proposals
        ),
        None,
    )
    if existing_proposal and not regenerate:
        existing_status = get_import_format_status(store, analysis_id)
        return {
            "status": (
                "MANUAL_MAPPING_REQUIRED"
                if existing_proposal["model_id"] == "manual"
                else "PROPOSAL_READY"
            ),
            "analysis_id": analysis_id,
            "format_status": (existing_status or {}).get("status", "UNKNOWN"),
            "section_id": section_id,
            "fingerprint": fingerprint,
            "source_structure": {
                key: structure[key]
                for key in ("encoding", "delimiter", "section_title", "columns")
            },
            "allowed_target_fields": sorted(ALLOWED_TARGET_FIELDS),
            "proposal": existing_proposal,
            "ai_used": existing_proposal["model_id"] != "manual",
            "reused_pending_proposal": True,
        }
    similar = [
        profile
        for profile in active_profiles.values()
        if profile["bank_identifier"] == bank_identifier and profile["supported_section_type"] == "CHECKING"
    ]
    known_fingerprints = _fingerprints(store)
    for profile in similar:
        previous = known_fingerprints.get(profile["fingerprint_id"])
        if not previous:
            continue
        profile_changes, _ = _format_change(previous, fingerprint, profile)
        allowed_changes: set[str] = set()
        rules = set(profile.get("compatibility_rules", []))
        if "ALLOW_ADDITIONAL_COLUMNS" in rules:
            allowed_changes.add("COLUMN_ADDED")
        if "ALLOW_SECTION_TITLE_CHANGE" in rules:
            allowed_changes.add("SECTION_TITLE_CHANGED")
        stored_mapping = {
            "section_type": profile["supported_section_type"],
            "field_mappings": profile["mappings"],
            "transformations": profile["transformations"],
            "constants": profile["constants"],
            "ignored_columns": profile["ignored_columns"],
            "confidence": 1,
            "warnings": [],
        }
        compatibility_validation = validate_import_mapping(
            stored_mapping, columns=structure["columns"], rows=structure["rows"]
        )
        if (
            set(profile_changes).issubset(allowed_changes)
            and compatibility_validation["status"] == "VALID"
        ):
            events.extend(
                [
                    _event(
                        "ImportFormatChangeDetected",
                        "ImportFormatFingerprint",
                        fingerprint["fingerprint_id"],
                        fingerprint_version + 1,
                        command_id,
                        {
                            "analysis_id": analysis_id,
                            "fingerprint_id": fingerprint["fingerprint_id"],
                            "bank_identifier": bank_identifier,
                            "section_id": section_id,
                            "status": "FORMAT_CHANGED",
                            "severity": "COMPATIBLE",
                            "change_types": profile_changes,
                            "previous_profile_ids": [profile["profile_id"]],
                            "detected_at": _now(),
                        },
                    ),
                    _event(
                        "ImportProfileReused",
                        "ImportProfile",
                        profile["profile_id"],
                        store.next_aggregate_version("ImportProfile", profile["profile_id"]),
                        command_id,
                        {
                            "profile_id": profile["profile_id"],
                            "fingerprint_id": fingerprint["fingerprint_id"],
                            "analysis_id": analysis_id,
                            "reused_at": _now(),
                            "ai_used": False,
                        },
                    ),
                ]
            )
            _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:compatible", events)
            return {
                "status": "KNOWN_COMPATIBLE",
                "analysis_id": analysis_id,
                "fingerprint": fingerprint,
                "profile": profile,
                "format_changes": profile_changes,
                "validation": compatibility_validation,
                "ai_used": False,
            }
    detection = "FORMAT_CHANGED" if similar else "UNKNOWN"
    change_types: list[str] = []
    severity: str | None = None
    if similar:
        for profile in similar:
            previous = known_fingerprints.get(profile["fingerprint_id"])
            if previous:
                profile_changes, profile_severity = _format_change(previous, fingerprint, profile)
                change_types.extend(profile_changes)
                if profile_severity == "BREAKING":
                    severity = "BREAKING"
        severity = severity or "REVIEW_REQUIRED"
    events.append(
        _event(
            "UnknownImportFormatDetected" if not similar else "ImportFormatChangeDetected",
            "ImportFormatFingerprint",
            fingerprint["fingerprint_id"],
            fingerprint_version + 1,
            command_id,
            {
                "analysis_id": analysis_id,
                "fingerprint_id": fingerprint["fingerprint_id"],
                "bank_identifier": bank_identifier,
                "section_id": section_id,
                "status": detection,
                "severity": severity,
                "change_types": sorted(set(change_types)),
                "previous_profile_ids": [profile["profile_id"] for profile in similar],
                "detected_at": _now(),
            },
        )
    )
    model_status = local_model.status()
    if not model_status["available"] or not model_status["import_mapping"]:
        mapping = _manual_mapping()
        validation = validate_import_mapping(
            mapping, columns=structure["columns"], rows=structure["rows"]
        )
        proposal = _proposal(
            mapping=mapping,
            validation=validation,
            analysis_id=analysis_id,
            content_hash=content_hash,
            fingerprint_id=fingerprint["fingerprint_id"],
            section_id=section_id,
            bank_identifier=bank_identifier,
            model_id="manual",
            model_version="0.0.0",
        )
        events.extend(_proposal_events(proposal, command_id))
        _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:manual", events)
        return {
            "status": "MANUAL_MAPPING_REQUIRED",
            "format_status": detection,
            "analysis_id": analysis_id,
            "section_id": section_id,
            "fingerprint": fingerprint,
            "source_structure": {key: structure[key] for key in ("encoding", "delimiter", "section_title", "columns")},
            "allowed_target_fields": sorted(ALLOWED_TARGET_FIELDS),
            "manual_mapping_available": True,
            "ai_error_code": "LOCAL_AI_UNAVAILABLE",
            "proposal": proposal,
        }
    request_body = {
        "purpose": "IMPORT_MAPPING",
        "target_schema": TARGET_SCHEMA_VERSION,
        "source_structure": {key: structure[key] for key in ("encoding", "delimiter", "section_title", "columns")},
        "sample_rows": _sample_rows(structure["rows"]),
        "allowed_target_fields": sorted(ALLOWED_TARGET_FIELDS),
    }
    semantic_result: dict[str, Any] = {}

    def semantic_validator(candidate: dict[str, Any]) -> bool:
        semantic_result.update(
            validate_import_mapping(
                candidate, columns=structure["columns"], rows=structure["rows"]
            )
        )
        return semantic_result["status"] != "INVALID"

    try:
        mapping = local_model.infer_structured(
            StructuredInferenceRequest(
                purpose="IMPORT_MAPPING",
                prompt=json.dumps(request_body, ensure_ascii=False, sort_keys=True),
                output_schema=MAPPING_OUTPUT_SCHEMA,
                semantic_validator=semantic_validator,
            )
        )
        validation = semantic_result or validate_import_mapping(
            mapping, columns=structure["columns"], rows=structure["rows"]
        )
        if validation["status"] == "INVALID":
            raise LocalModelError("LOCAL_AI_SEMANTIC_VALIDATION_FAILED")
    except LocalModelError as exc:
        mapping = _manual_mapping()
        validation = validate_import_mapping(
            mapping, columns=structure["columns"], rows=structure["rows"]
        )
        proposal = _proposal(
            mapping=mapping,
            validation=validation,
            analysis_id=analysis_id,
            content_hash=content_hash,
            fingerprint_id=fingerprint["fingerprint_id"],
            section_id=section_id,
            bank_identifier=bank_identifier,
            model_id="manual",
            model_version="0.0.0",
        )
        events.extend(_proposal_events(proposal, command_id))
        _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:ai-failed", events)
        return {
            "status": "MANUAL_MAPPING_REQUIRED",
            "format_status": detection,
            "analysis_id": analysis_id,
            "section_id": section_id,
            "fingerprint": fingerprint,
            "source_structure": request_body["source_structure"],
            "allowed_target_fields": sorted(ALLOWED_TARGET_FIELDS),
            "manual_mapping_available": True,
            "ai_error_code": str(exc),
            "proposal": proposal,
        }
    model = model_status["model"]
    proposal = _proposal(
        mapping=mapping,
        validation=validation,
        analysis_id=analysis_id,
        content_hash=content_hash,
        fingerprint_id=fingerprint["fingerprint_id"],
        section_id=section_id,
        bank_identifier=bank_identifier,
        model_id=model["model_id"],
        model_version=model["model_version"],
        runtime_version=str(model.get("runtime_version", "unknown")),
        structured_output_backend=str(
            model.get("structured_output_backend", "GBNF")
        ),
    )
    events.extend(_proposal_events(proposal, command_id))
    _append(store, "AnalyzeUnknownImportFormat", f"{content_hash}:{proposal['proposal_id']}", events)
    return {
        "status": "PROPOSAL_READY",
        "format_status": detection,
        "analysis_id": analysis_id,
        "section_id": section_id,
        "fingerprint": fingerprint,
        "source_structure": request_body["source_structure"],
        "allowed_target_fields": sorted(ALLOWED_TARGET_FIELDS),
        "proposal": proposal,
        "ai_used": True,
    }


def validate_mapping_for_proposal(
    store: LocalFinanceStore,
    proposal_id: str,
    mapping: dict[str, Any],
    *,
    persist: bool = False,
) -> dict[str, Any]:
    proposal = _proposals(store).get(proposal_id)
    if not proposal:
        raise ImportMappingError("IMPORT_MAPPING_PROPOSAL_NOT_FOUND")
    structure = _inspect(store.load_import_content(proposal["content_hash"]))
    validation = validate_import_mapping(
        mapping, columns=structure["columns"], rows=structure["rows"]
    )
    if persist:
        command_id = _id("cmd")
        event = _event(
            "ImportMappingValidationCompleted",
            "ImportMappingProposal",
            proposal_id,
            store.next_aggregate_version("ImportMappingProposal", proposal_id),
            command_id,
            {"proposal_id": proposal_id, **validation},
        )
        _append(
            store,
            "ValidateImportMapping",
            f"{proposal_id}:{validation['validation_result_id']}",
            [event],
        )
    return validation


def confirm_import_mapping(
    store: LocalFinanceStore,
    proposal_id: str,
    final_mapping: dict[str, Any],
    *,
    validation_result_id: str,
    confirmation_comment: str | None = None,
    compatibility_rules: list[str] | None = None,
) -> dict[str, Any]:
    proposal = _proposals(store).get(proposal_id)
    if not proposal:
        raise ImportMappingError("IMPORT_MAPPING_PROPOSAL_NOT_FOUND")
    if any(
        event["aggregate_id"] == proposal_id
        for event in store.events("ImportMappingRejected")
        + store.events("ImportMappingConfirmed")
    ):
        raise ImportMappingError("IMPORT_PROFILE_CONFLICT")
    validation = validate_mapping_for_proposal(store, proposal_id, final_mapping)
    if validation["validation_result_id"] != validation_result_id:
        raise ImportMappingError("IMPORT_MAPPING_VALIDATION_STALE")
    if validation["status"] not in {"VALID", "VALID_WITH_WARNINGS"}:
        raise ImportMappingError("IMPORT_MAPPING_SEMANTIC_INVALID")
    if validation["status"] == "VALID_WITH_WARNINGS" and not (confirmation_comment or "").strip():
        raise ImportMappingError("IMPORT_MAPPING_WARNING_CONFIRMATION_REQUIRED")
    compatibility_rules = compatibility_rules or []
    if any(rule not in ALLOWED_COMPATIBILITY_RULES for rule in compatibility_rules):
        raise ImportMappingError("IMPORT_PROFILE_COMPATIBILITY_RULE_INVALID")
    profiles = _profiles(store)
    family = [
        profile
        for profile in profiles.values()
        if profile["bank_identifier"] == proposal["bank_identifier"]
        and profile["supported_section_type"] == proposal["section_type"]
    ]
    related = [profile for profile in family if profile.get("status") == "ACTIVE"]
    version = f"1.{len(family)}.0"
    profile_id = f"user_profile_{uuid.uuid4().hex}"
    proposed_mapping = {
        key: proposal[key]
        for key in ("section_type", "field_mappings", "transformations", "constants", "ignored_columns", "confidence", "warnings")
    }
    diff = {
        "changed": final_mapping != proposed_mapping,
        "proposal": proposed_mapping,
        "final": final_mapping,
    }
    profile = {
        "profile_id": profile_id,
        "profile_version": version,
        "name": f"{proposal['bank_identifier']} – {proposal['section_type'].title()}",
        "source": "USER_MANUAL" if proposal["model_id"] == "manual" else "USER_CONFIRMED_AI",
        "bank_identifier": proposal["bank_identifier"],
        "fingerprint_id": proposal["fingerprint_id"],
        "fingerprint_algorithm_version": FINGERPRINT_ALGORITHM_VERSION,
        "supported_section_type": proposal["section_type"],
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        "mappings": final_mapping["field_mappings"],
        "transformations": final_mapping["transformations"],
        "constants": final_mapping["constants"],
        "ignored_columns": final_mapping["ignored_columns"],
        "compatibility_rules": compatibility_rules,
        "created_from_proposal_id": proposal_id,
        "created_by": "USER",
        "model_id": proposal["model_id"],
        "model_version": proposal["model_version"],
        "runtime_version": proposal.get("runtime_version", "unknown"),
        "structured_output_backend": proposal.get(
            "structured_output_backend", "LEGACY_JSON_SCHEMA"
        ),
        "inference_policy_version": proposal["inference_policy_version"],
        "status": "ACTIVE",
        "created_at": _now(),
    }
    command_id = _id("cmd")
    events = [
        _event(
            "ImportMappingConfirmed",
            "ImportMappingProposal",
            proposal_id,
            store.next_aggregate_version("ImportMappingProposal", proposal_id),
            command_id,
            {
                "proposal_id": proposal_id,
                "fingerprint_id": proposal["fingerprint_id"],
                "final_mapping": final_mapping,
                "validation": validation,
                "proposal_modified": diff["changed"],
                "mapping_diff": diff,
                "validation_result_id": validation_result_id,
                "confirmation_comment": confirmation_comment,
                "model_id": proposal["model_id"],
                "model_version": proposal["model_version"],
                "runtime_version": proposal.get("runtime_version", "unknown"),
                "structured_output_backend": proposal.get(
                    "structured_output_backend", "LEGACY_JSON_SCHEMA"
                ),
                "inference_policy_version": proposal["inference_policy_version"],
                "confirmed_at": _now(),
            },
        ),
        _event("ImportProfileCreated", "ImportProfile", profile_id, 1, command_id, profile),
    ]
    for previous in related:
        events.append(
            _event(
                "ImportProfileSuperseded",
                "ImportProfile",
                previous["profile_id"],
                store.next_aggregate_version("ImportProfile", previous["profile_id"]),
                command_id,
                {
                    "profile_id": previous["profile_id"],
                    "superseded_by_profile_id": profile_id,
                    "superseded_at": _now(),
                },
            )
        )
    _append(store, "ConfirmImportMapping", proposal_id, events)
    return {"status": "CONFIRMED", "proposal_id": proposal_id, "validation": validation, "profile": profile}


def reject_import_mapping(store: LocalFinanceStore, proposal_id: str, reason: str | None = None) -> dict[str, Any]:
    proposal = _proposals(store).get(proposal_id)
    if not proposal:
        raise ImportMappingError("IMPORT_MAPPING_PROPOSAL_NOT_FOUND")
    if any(
        event["aggregate_id"] == proposal_id
        for event in store.events("ImportMappingRejected")
        + store.events("ImportMappingConfirmed")
    ):
        raise ImportMappingError("IMPORT_PROFILE_CONFLICT")
    command_id = _id("cmd")
    event = _event(
        "ImportMappingRejected",
        "ImportMappingProposal",
        proposal_id,
        store.next_aggregate_version("ImportMappingProposal", proposal_id),
        command_id,
        {"proposal_id": proposal_id, "reason": reason, "rejected_at": _now(), "rejected_by": "USER"},
    )
    _append(store, "RejectImportMapping", proposal_id, [event])
    return {"status": "REJECTED", "proposal_id": proposal_id}


def get_import_mapping_proposal(store: LocalFinanceStore, proposal_id: str) -> dict[str, Any] | None:
    return _proposals(store).get(proposal_id)


def list_import_profiles(store: LocalFinanceStore) -> list[dict[str, Any]]:
    return sorted(_profiles(store).values(), key=lambda item: (item["name"], item["profile_version"]))


def get_import_profile(store: LocalFinanceStore, profile_id: str) -> dict[str, Any] | None:
    return _profiles(store).get(profile_id)


def get_import_profile_history(store: LocalFinanceStore, profile_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in store.events()
        if event["aggregate_type"] == "ImportProfile" and event["aggregate_id"] == profile_id
    ]


def get_import_format_status(store: LocalFinanceStore, analysis_id: str) -> dict[str, Any] | None:
    fingerprint_event = next(
        (
            event
            for event in reversed(store.events("ImportFormatFingerprintCreated"))
            if event["payload"]["analysis_id"] == analysis_id
        ),
        None,
    )
    if not fingerprint_event:
        return None
    fingerprint_id = fingerprint_event["payload"]["fingerprint_id"]
    detection = next(
        (
            event["payload"]
            for event in reversed(store.events())
            if event["event_type"]
            in {"UnknownImportFormatDetected", "ImportFormatChangeDetected"}
            and event["payload"]["analysis_id"] == analysis_id
        ),
        None,
    )
    profile = next(
        (
            item
            for item in _profiles(store).values()
            if item["fingerprint_id"] == fingerprint_id and item.get("status") == "ACTIVE"
        ),
        None,
    )
    reused = next(
        (
            event["payload"]
            for event in reversed(store.events("ImportProfileReused"))
            if event["payload"]["analysis_id"] == analysis_id
        ),
        None,
    )
    return {
        "analysis_id": analysis_id,
        "fingerprint": fingerprint_event["payload"],
        "status": (
            "KNOWN_EXACT"
            if profile
            else "KNOWN_COMPATIBLE"
            if reused and (detection or {}).get("severity") == "COMPATIBLE"
            else (detection or {}).get("status", "UNKNOWN")
        ),
        "profile": profile or (_profiles(store).get(reused["profile_id"]) if reused else None),
        "format_change": detection,
    }


__all__ = [
    "ALLOWED_TARGET_FIELDS",
    "FINGERPRINT_ALGORITHM_VERSION",
    "ImportMappingError",
    "MAPPING_OUTPUT_SCHEMA",
    "MAPPING_SCHEMA_VERSION",
    "analyze_unknown_import_format",
    "build_import_format_fingerprint",
    "confirm_import_mapping",
    "get_import_mapping_proposal",
    "get_import_format_status",
    "get_import_profile",
    "get_import_profile_history",
    "list_import_profiles",
    "reject_import_mapping",
    "validate_import_mapping",
    "validate_mapping_for_proposal",
]
