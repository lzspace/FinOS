"""Deterministic local import for German multi-account bank CSV exports."""

from __future__ import annotations

import csv
from calendar import monthrange
import hashlib
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .accounts import account_accepts_transaction, accounts, create_account
from .storage_policy import validate_runtime_path
from .store import LocalFinanceStore, StoreInvariantError


PROFILE_ID = "GermanMultiAccountCsvV1"
PROFILE_VERSION = "1.0.0"
# Include parser behavior in the analysis identity. A corrected parser must be
# allowed to create a fresh analysis for an already-seen source file.
PARSER_VERSION = "GermanMultiAccountCsvV1@1.0.1"
SECTION_TITLES = {
    "Umsätze Girokonto": "CHECKING",
    "Umsätze Tagesgeld PLUS-Konto": "SAVINGS",
    "Umsätze Depot": "BROKERAGE",
}
SECTION_ORDER = ("CHECKING", "SAVINGS", "BROKERAGE")
ACCOUNT_COLUMNS = (
    "Buchungstag",
    "Wertstellung (Valuta)",
    "Vorgang",
    "Buchungstext",
    "Umsatz in EUR",
)
BROKERAGE_COLUMNS = (
    "Buchungstag",
    "Geschäftstag",
    "Stück / Nom.",
    "Bezeichnung",
    "WKN",
    "Währung",
    "Ausführungskurs",
    "Umsatz in EUR",
)
EMPTY_MARKER = "Keine Umsätze vorhanden."
BALANCE_SOURCES = {
    "BANK_STATEMENT",
    "BANK_EXPORT",
    "MANUAL_ENTRY",
    "PREVIOUS_SYSTEM",
    "CALCULATED",
}
MAPPING_ACTIONS = {"USE_EXISTING_ACCOUNT", "CREATE_ACCOUNT", "SKIP_SECTION"}
IMPORT_MODES = {"VALIDATE_ONLY", "IMPORT_NEW", "FORCE_REIMPORT"}
FUNDING_WINDOW_DAYS = 5


class MultiAccountImportError(ValueError):
    """Safe import error whose message never contains source finance data."""


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    kind: str,
    aggregate_type: str,
    aggregate_id: str,
    version: int,
    command_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "event_id": _id("evt"),
        "event_type": kind,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
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
) -> int:
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": events[0]["correlation_id"],
                "command_type": command_type,
                "idempotency_key": f"{command_type}:{idempotency_key}",
            },
            events,
        )
    )


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252"), "cp1252"
        except UnicodeDecodeError as exc:
            raise MultiAccountImportError("IMPORT_ENCODING_INVALID") from exc


def _parse_german_number(value: str, quantum: Decimal) -> Decimal:
    compact = value.strip().replace(" ", "")
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,8})?", compact):
        raise MultiAccountImportError("IMPORT_DECIMAL_FORMAT_INVALID")
    try:
        parsed = Decimal(compact.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise MultiAccountImportError("IMPORT_DECIMAL_FORMAT_INVALID") from exc
    if not parsed.is_finite():
        raise MultiAccountImportError("IMPORT_DECIMAL_FORMAT_INVALID")
    return parsed.quantize(quantum)


def parse_german_decimal(value: str) -> Decimal:
    return _parse_german_number(value, Decimal("0.01"))


def parse_german_quantity(value: str) -> Decimal:
    return _parse_german_number(value, Decimal("0.00000001"))


def _parse_date(value: str) -> str:
    normalized = value.strip()
    for format_string in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(normalized, format_string).date().isoformat()
        except ValueError:
            continue
    raise MultiAccountImportError("IMPORT_DATE_FORMAT_INVALID")


def _rows(text: str) -> list[list[str]]:
    try:
        return [[cell.strip() for cell in row] for row in csv.reader(text.splitlines(), delimiter=";")]
    except csv.Error as exc:
        raise MultiAccountImportError("IMPORT_PROFILE_MISMATCH") from exc


def _section_period(rows: Iterable[list[str]]) -> tuple[str | None, str | None]:
    values: list[date] = []
    pattern = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{2})(?!\d)")
    for row in rows:
        for match in pattern.findall(" ".join(row)):
            try:
                values.append(datetime.strptime(match, "%d.%m.%y").date())
            except ValueError:
                continue
    return (
        min(values).isoformat() if values else None,
        max(values).isoformat() if values else None,
    )


def _bank_identifier(text: str) -> str | None:
    values: set[str] = set()
    for row in _rows(text):
        if len(row) >= 2 and row[0].casefold() in {
            "bank",
            "bankkennung",
            "institut",
            "bank identifier",
        }:
            value = row[1].strip()
            if value:
                values.add(value)
    if len(values) > 1:
        raise MultiAccountImportError("IMPORT_MULTIPLE_BANKS_NOT_SUPPORTED")
    return next(iter(values), None)


def _account_reference(rows: list[list[str]]) -> str | None:
    for row in rows:
        if len(row) >= 2 and row[0].casefold() in {
            "kontoreferenz",
            "kontonummer",
            "iban",
            "depotnummer",
        }:
            value = row[1].strip()
            if value:
                return value
    return None


def _declared_month(text: str) -> str | None:
    candidates: set[str] = set()
    month_pattern = re.compile(r"(?<!\d)(0[1-9]|1[0-2])[./-](20\d{2})(?!\d)")
    iso_month_pattern = re.compile(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])(?!-?\d)")
    date_pattern = re.compile(
        r"(?<!\d)(\d{2})[.](\d{2})[.](\d{2}|20\d{2})(?!\d)"
    )
    labels = {
        "berichtsmonat",
        "monat",
        "reporting month",
        "zeitraum",
        "berichtszeitraum",
        "period",
    }
    for row in _rows(text):
        # Some bank exports put the period in the second column of a section
        # heading, e.g. "Umsätze Girokonto;Zeitraum: 01.08.2024 - 31.08.2024".
        # Look for a period label in every cell, not just the first one.
        label_index = next(
            (
                index
                for index, cell in enumerate(row)
                if cell.casefold().split(":", 1)[0].strip() in labels
            ),
            None,
        )
        if label_index is None:
            continue
        value = " ".join(row[label_index:])
        for month, year in month_pattern.findall(value):
            candidates.add(f"{year}-{month}")
        for year, month in iso_month_pattern.findall(value):
            candidates.add(f"{year}-{month}")
        for _, month, year in date_pattern.findall(value):
            full_year = f"20{year}" if len(year) == 2 else year
            candidates.add(f"{full_year}-{month}")
    if len(candidates) > 1:
        raise MultiAccountImportError("IMPORT_PERIOD_MISMATCH")
    return next(iter(candidates), None)


def _monthly_period(
    sections: list[dict[str, Any]], text: str
) -> tuple[str, str]:
    months: set[str] = set()
    for section in sections:
        for record in section["records"]:
            booking = _parse_date(record["Buchungstag"])
            months.add(booking[:7])
    declared = _declared_month(text)
    if declared:
        months.add(declared)
    if not months:
        raise MultiAccountImportError("IMPORT_PERIOD_REQUIRED")
    if len(months) != 1:
        raise MultiAccountImportError("IMPORT_PERIOD_MISMATCH")
    year, month = (int(part) for part in next(iter(months)).split("-"))
    return date(year, month, 1).isoformat(), date(
        year, month, monthrange(year, month)[1]
    ).isoformat()


def _parse_sections(text: str, analysis_id: str) -> list[dict[str, Any]]:
    rows = _rows(text)
    starts: list[tuple[int, str, str, str | None]] = []
    for index, row in enumerate(rows):
        first = row[0].strip() if row else ""
        if first in SECTION_TITLES:
            section_type = SECTION_TITLES[first]
            starts.append((index, first, section_type, section_type))
        elif first.startswith("Umsätze "):
            starts.append((index, first, "UNKNOWN", None))
    if not starts:
        raise MultiAccountImportError("IMPORT_PROFILE_MISMATCH")
    recognized_order = [SECTION_ORDER.index(item[2]) for item in starts if item[2] != "UNKNOWN"]
    if recognized_order != sorted(recognized_order):
        raise MultiAccountImportError("IMPORT_SECTION_ORDER_INVALID")
    sections: list[dict[str, Any]] = []
    for position, (start, title, section_type, account_type) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(rows)
        content = rows[start + 1 : end]
        expected = BROKERAGE_COLUMNS if section_type == "BROKERAGE" else ACCOUNT_COLUMNS
        header_index = next(
            (i for i, row in enumerate(content) if tuple(row[: len(expected)]) == expected),
            None,
        )
        empty = any(EMPTY_MARKER in row for row in content)
        records: list[dict[str, str]] = []
        detected: list[str] = []
        warnings: list[str] = []
        if header_index is not None:
            detected = content[header_index][: len(expected)]
            for row in content[header_index + 1 :]:
                if not any(row) or EMPTY_MARKER in row:
                    continue
                if len(row) < len(expected):
                    warnings.append("IMPORT_ROW_COLUMN_COUNT_INVALID")
                    continue
                records.append(dict(zip(expected, row[: len(expected)], strict=True)))
        elif not empty and section_type != "UNKNOWN":
            warnings.append("IMPORT_REQUIRED_COLUMNS_MISSING")
        if section_type == "UNKNOWN":
            warnings.append("IMPORT_UNKNOWN_SECTION")
        period_start, period_end = _section_period(content)
        account_reference = _account_reference(content[: header_index or len(content)])
        canonical_content = repr(content).encode("utf-8")
        content_hash = hashlib.sha256(canonical_content).hexdigest()
        section_hash = hashlib.sha256(f"{analysis_id}:{position}:{title}".encode()).hexdigest()[:16]
        sections.append(
            {
                "section_id": f"section_{section_hash}",
                "section_type": section_type,
                "original_title": title,
                "account_reference": account_reference,
                "period_start": period_start,
                "period_end": period_end,
                "record_count": len(records),
                "empty": empty and not records,
                "detected_columns": detected,
                "proposed_account_type": account_type,
                "import_supported": section_type != "UNKNOWN" and (empty or header_index is not None),
                "warnings": sorted(set(warnings)),
                "content_hash": content_hash,
                "mapped_account_id": None,
                "import_status": "ANALYZED",
                "section_index": position,
                "records": records,
            }
        )
    by_type: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        if section["section_type"] != "UNKNOWN":
            by_type.setdefault(section["section_type"], []).append(section)
    if any(
        len(group) > 1
        and (
            any(not item["account_reference"] for item in group)
            or len({item["account_reference"] for item in group}) != len(group)
        )
        for group in by_type.values()
    ):
        raise MultiAccountImportError("IMPORT_SECTION_AMBIGUOUS")
    return sections


def analyses(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["aggregate_id"]: event["payload"]
        for event in store.events("ImportFileAnalyzed")
    }


def _binding_identity(
    bank_identifier: str,
    section_type: str,
    original_title: str,
    account_reference: str | None,
) -> str:
    raw = "\x1f".join(
        (bank_identifier, section_type, original_title, account_reference or "")
    )
    return "binding_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def section_bindings(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events("ImportSectionBindingConfirmed"):
        result[event["aggregate_id"]] = event["payload"]
    return result


def analyze_import_file(
    store: LocalFinanceStore,
    source_file_path: str | Path,
    requested_profile: str = PROFILE_ID,
    confirmed_bank_identifier: str | None = None,
) -> dict[str, Any]:
    if requested_profile != PROFILE_ID:
        raise MultiAccountImportError("IMPORT_PROFILE_MISMATCH")
    path = validate_runtime_path(
        source_file_path,
        repository_roots=store.repository_roots,
        known_network_roots=store.known_network_roots,
    )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MultiAccountImportError("FINANCE_IMPORT_UNREADABLE") from exc
    file_hash = hashlib.sha256(raw).hexdigest()
    export_id = f"export_{file_hash}"
    existing = analyses(store).get(export_id)
    if existing:
        return get_import_analysis(store, export_id) or existing
    text, encoding = _decode(raw)
    detected_bank = _bank_identifier(text)
    if detected_bank and confirmed_bank_identifier and detected_bank != confirmed_bank_identifier:
        raise MultiAccountImportError("IMPORT_BANK_IDENTIFIER_MISMATCH")
    bank_identifier = detected_bank or (confirmed_bank_identifier or "").strip()
    if not bank_identifier or len(bank_identifier) > 128:
        raise MultiAccountImportError("IMPORT_BANK_IDENTIFIER_REQUIRED")
    parsed = _parse_sections(text, export_id)
    period_start, period_end = _monthly_period(parsed, text)
    bindings = section_bindings(store)
    active_accounts = accounts(store)
    for section in parsed:
        section["period_start"] = period_start
        section["period_end"] = period_end
        if section["section_type"] == "UNKNOWN":
            continue
        binding_id = _binding_identity(
            bank_identifier,
            section["section_type"],
            section["original_title"],
            section["account_reference"],
        )
        binding = bindings.get(binding_id)
        bound_account = active_accounts.get(binding["account_id"], {}) if binding else {}
        if (
            binding
            and bound_account.get("status") == "ACTIVE"
            and bound_account.get("account_type") == section["proposed_account_type"]
        ):
            section["mapped_account_id"] = binding["account_id"]
    public_sections = [{key: value for key, value in item.items() if key != "records"} for item in parsed]
    payload = {
        "export_id": export_id,
        "analysis_id": export_id,
        "bank_identifier": bank_identifier,
        "detected_profile": PROFILE_ID,
        "import_profile": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "encoding": encoding,
        "delimiter": ";",
        "period_start": period_start,
        "period_end": period_end,
        "report_month": period_start[:7],
        "sections": public_sections,
        "warnings": sorted({warning for item in parsed for warning in item["warnings"]}),
        "file_hash": file_hash,
        "source_file_hash": file_hash,
        "file_size": len(raw),
        "status": "ANALYZED",
    }
    command_id = _id("cmd")
    _append(
        store,
        "AnalyzeImportFile",
        f"{file_hash}:{PARSER_VERSION}",
        [_event("ImportFileAnalyzed", "BankMonthlyExport", export_id, 1, command_id, payload)],
    )
    if not store.has_import_content_hash(raw):
        store.store_import_file(
            export_id, path, raw, PARSER_VERSION, status="ANALYZED"
        )
    return payload


def get_import_analysis(store: LocalFinanceStore, analysis_id: str) -> dict[str, Any] | None:
    base = analyses(store).get(analysis_id)
    if not base:
        return None
    mappings = section_mappings(store, analysis_id)
    runs = {
        item["section_id"]: item
        for item in imported_section_runs(store).values()
        if item["export_id"] == analysis_id
    }
    sections = []
    for section in base["sections"]:
        mapping = mappings.get(section["section_id"])
        run = runs.get(section["section_id"])
        sections.append(
            {
                **section,
                "mapped_account_id": (
                    mapping["account_id"] if mapping else section["mapped_account_id"]
                ),
                "import_status": run["status"] if run else section["import_status"],
                "section_result": run,
            }
        )
    batch_status = next(
        (
            event["payload"].get("status")
            for event in reversed(store.events("ImportBatchCompleted"))
            if any(
                run["import_batch_id"] == event["payload"]["import_batch_id"]
                for run in runs.values()
            )
        ),
        None,
    )
    return {**base, "sections": sections, "status": batch_status or base["status"]}


def get_bank_monthly_export(store: LocalFinanceStore, export_id: str) -> dict[str, Any] | None:
    return get_import_analysis(store, export_id)


def list_import_sections(store: LocalFinanceStore, analysis_id: str) -> list[dict[str, Any]]:
    analysis = get_import_analysis(store, analysis_id)
    if not analysis:
        raise MultiAccountImportError("IMPORT_ANALYSIS_NOT_FOUND")
    mappings = section_mappings(store, analysis_id)
    return [
        {**section, "mapping": mappings.get(section["section_id"])}
        for section in analysis["sections"]
    ]


def _load_parsed(store: LocalFinanceStore, analysis_id: str) -> list[dict[str, Any]]:
    analysis = get_import_analysis(store, analysis_id)
    if not analysis:
        raise MultiAccountImportError("IMPORT_ANALYSIS_NOT_FOUND")
    raw = store.load_import_content(analysis["file_hash"])
    if hashlib.sha256(raw).hexdigest() != analysis["file_hash"]:
        raise MultiAccountImportError("FINANCE_IMPORT_CONTENT_HASH_MISMATCH")
    text, _ = _decode(raw)
    return _parse_sections(text, analysis_id)


def section_mappings(store: LocalFinanceStore, analysis_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"] in {"ImportSectionMapped", "ImportSectionSkipped"}:
            payload = event["payload"]
            if payload["analysis_id"] == analysis_id:
                result[payload["section_id"]] = payload
    return result


def map_import_sections(
    store: LocalFinanceStore,
    analysis_id: str,
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    analysis = get_import_analysis(store, analysis_id)
    if not analysis:
        raise MultiAccountImportError("IMPORT_ANALYSIS_NOT_FOUND")
    sections = {item["section_id"]: item for item in analysis["sections"]}
    if not mappings or len({item.get("section_id") for item in mappings}) != len(mappings):
        raise MultiAccountImportError("IMPORT_SECTION_MAPPING_INVALID")
    current = section_mappings(store, analysis_id)
    proposed = {**current}
    prepared: list[dict[str, Any]] = []
    for item in mappings:
        section_id = item.get("section_id")
        action = item.get("action", "USE_EXISTING_ACCOUNT")
        if section_id not in sections or action not in MAPPING_ACTIONS:
            raise MultiAccountImportError("IMPORT_SECTION_MAPPING_INVALID")
        section = sections[section_id]
        account_id = item.get("account_id")
        if action == "SKIP_SECTION":
            account_id = None
        elif action == "CREATE_ACCOUNT":
            if section["proposed_account_type"] is None:
                raise MultiAccountImportError("IMPORT_ACCOUNT_TYPE_MISMATCH")
            account_id = create_account(
                store,
                account_id=account_id,
                display_name=item.get("display_name", section["original_title"]),
                account_type=section["proposed_account_type"],
                institution=item.get("institution", ""),
                currency="EUR",
                opened_at=item.get("opened_at") or analysis.get("period_start"),
            )
        else:
            account = accounts(store).get(str(account_id))
            if not account:
                raise MultiAccountImportError("FINANCE_ACCOUNT_NOT_FOUND")
            if account["status"] != "ACTIVE":
                raise MultiAccountImportError("FINANCE_IMPORT_ACCOUNT_CLOSED")
            if account["account_type"] != section["proposed_account_type"]:
                raise MultiAccountImportError("IMPORT_ACCOUNT_TYPE_MISMATCH")
        payload = {
            "analysis_id": analysis_id,
            "export_id": analysis_id,
            "bank_identifier": analysis["bank_identifier"],
            "section_id": section_id,
            "section_type": section["section_type"],
            "original_title": section["original_title"],
            "account_reference": section["account_reference"],
            "account_id": account_id,
            "action": action,
            "status": "SKIPPED" if action == "SKIP_SECTION" else "MAPPED",
            "mapped_at": _now(),
        }
        proposed[section_id] = payload
        prepared.append(payload)
    account_ids = [item["account_id"] for item in proposed.values() if item["account_id"]]
    if len(account_ids) != len(set(account_ids)):
        raise MultiAccountImportError("IMPORT_ACCOUNT_MAPPING_CONFLICT")
    command_id = _id("cmd")
    events = [
        _event(
            "ImportSectionSkipped" if payload["action"] == "SKIP_SECTION" else "ImportSectionMapped",
            "ImportSectionMapping",
            f"{analysis_id}_{payload['section_id']}",
            store.next_aggregate_version(
                "ImportSectionMapping", f"{analysis_id}_{payload['section_id']}"
            ),
            command_id,
            payload,
        )
        for payload in prepared
    ]
    known_bindings = section_bindings(store)
    for payload in prepared:
        if not payload["account_id"]:
            continue
        binding_id = _binding_identity(
            payload["bank_identifier"],
            payload["section_type"],
            payload["original_title"],
            payload["account_reference"],
        )
        current_binding = known_bindings.get(binding_id)
        if current_binding and current_binding["account_id"] == payload["account_id"]:
            continue
        events.append(
            _event(
                "ImportSectionBindingConfirmed",
                "ImportSectionBinding",
                binding_id,
                store.next_aggregate_version("ImportSectionBinding", binding_id),
                command_id,
                {
                    "binding_id": binding_id,
                    "bank_identifier": payload["bank_identifier"],
                    "section_type": payload["section_type"],
                    "original_title": payload["original_title"],
                    "account_reference": payload["account_reference"],
                    "account_id": payload["account_id"],
                    "confirmed_at": _now(),
                },
            )
        )
    digest = hashlib.sha256(repr(sorted((x["section_id"], x["action"], x["account_id"]) for x in prepared)).encode()).hexdigest()
    _append(store, "MapImportSections", f"{analysis_id}:{digest}", events)
    return prepared


def _decimal(value: str, code: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise StoreInvariantError(code) from exc
    if not parsed.is_finite():
        raise StoreInvariantError(code)
    return parsed


def _record_balance(
    store: LocalFinanceStore,
    *,
    kind: str,
    account_id: str,
    balance_date: str,
    booked_balance: str,
    available_balance: str | None,
    currency: str,
    source: str,
    confirmation: bool,
    comment: str | None = None,
) -> str:
    account = accounts(store).get(account_id)
    if not account:
        raise StoreInvariantError("FINANCE_ACCOUNT_NOT_FOUND")
    date.fromisoformat(balance_date)
    booked = _decimal(booked_balance, "FINANCE_BALANCE_INVALID")
    available = _decimal(available_balance, "FINANCE_BALANCE_INVALID") if available_balance is not None else None
    if currency != account["currency"] or source not in BALANCE_SOURCES:
        raise StoreInvariantError("FINANCE_BALANCE_INVALID")
    if not isinstance(confirmation, bool) or (source == "CALCULATED" and confirmation):
        raise StoreInvariantError("FINANCE_BALANCE_CONFIRMATION_INVALID")
    aggregate_type = "OpeningBalance" if kind == "OpeningBalanceRecorded" else "ClosingBalance"
    aggregate_id = f"{aggregate_type.lower()}_{account_id}"
    command_id = _id("cmd")
    payload = {
        "balance_id": aggregate_id,
        "account_id": account_id,
        "balance_date": balance_date,
        "booked_balance": str(booked),
        "available_balance": str(available) if available is not None else None,
        "currency": currency,
        "source": source,
        "confirmation": confirmation,
        "comment": comment,
        "recorded_at": _now(),
    }
    digest = hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest()
    _append(
        store,
        kind.removesuffix("Recorded").replace("Balance", "Balance"),
        digest,
        [_event(kind, aggregate_type, aggregate_id, store.next_aggregate_version(aggregate_type, aggregate_id), command_id, payload)],
    )
    return aggregate_id


def record_opening_balance(store: LocalFinanceStore, **payload: Any) -> str:
    return _record_balance(store, kind="OpeningBalanceRecorded", **payload)


def record_closing_balance(store: LocalFinanceStore, **payload: Any) -> str:
    payload.pop("comment", None)
    return _record_balance(store, kind="ClosingBalanceRecorded", comment=None, **payload)


def _latest_balance(store: LocalFinanceStore, event_type: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events(event_type):
        result[event["payload"]["account_id"]] = event["payload"]
    return result


def opening_balances(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return _latest_balance(store, "OpeningBalanceRecorded")


def closing_balances(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return _latest_balance(store, "ClosingBalanceRecorded")


def record_opening_security_position(
    store: LocalFinanceStore,
    *,
    account_id: str,
    valuation_date: str,
    security_identifier_type: str,
    security_identifier: str,
    security_name: str,
    quantity: str,
    valuation_price: str,
    price_currency: str,
    market_value: str,
    valuation_source: str,
    confirmation: bool,
) -> str:
    account = accounts(store).get(account_id)
    if not account or account["account_type"] != "BROKERAGE":
        raise StoreInvariantError("IMPORT_ACCOUNT_TYPE_MISMATCH")
    if security_identifier_type not in {"WKN", "ISIN", "OTHER"} or not security_identifier.strip():
        raise StoreInvariantError("FINANCE_SECURITY_IDENTIFIER_INVALID")
    date.fromisoformat(valuation_date)
    values = [_decimal(value, "FINANCE_SECURITY_POSITION_INVALID") for value in (quantity, valuation_price, market_value)]
    if any(value < 0 for value in values) or not confirmation:
        raise StoreInvariantError("FINANCE_SECURITY_POSITION_INVALID")
    position_id = "position_" + hashlib.sha256(f"{account_id}:{security_identifier}".encode()).hexdigest()[:24]
    command_id = _id("cmd")
    payload = {
        "position_id": position_id,
        "account_id": account_id,
        "valuation_date": valuation_date,
        "security_identifier_type": security_identifier_type,
        "security_identifier": security_identifier,
        "security_name": security_name,
        "quantity": str(values[0]),
        "valuation_price": str(values[1]),
        "price_currency": price_currency,
        "market_value": str(values[2]),
        "valuation_source": valuation_source,
        "confirmation": True,
    }
    _append(
        store,
        "RecordOpeningSecurityPosition",
        hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest(),
        [_event("OpeningSecurityPositionRecorded", "SecurityPosition", position_id, store.next_aggregate_version("SecurityPosition", position_id), command_id, payload)],
    )
    return position_id


def opening_security_positions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["aggregate_type"] != "SecurityPosition":
            continue
        if event["event_type"] == "OpeningSecurityPositionRecorded":
            result[event["aggregate_id"]] = event["payload"]
        elif (
            event["event_type"] == "SecurityPositionSnapshotCorrected"
            and event["aggregate_id"] in result
        ):
            result[event["aggregate_id"]] = event["payload"]["position"]
    return result


def confirm_empty_opening_security_positions(
    store: LocalFinanceStore, *, account_id: str, valuation_date: str
) -> int:
    account = accounts(store).get(account_id)
    if not account or account["account_type"] != "BROKERAGE":
        raise StoreInvariantError("IMPORT_ACCOUNT_TYPE_MISMATCH")
    date.fromisoformat(valuation_date)
    aggregate_id = f"empty_opening_positions_{account_id}"
    command_id = _id("cmd")
    payload = {
        "account_id": account_id,
        "valuation_date": valuation_date,
        "confirmation": True,
        "confirmed_at": _now(),
    }
    return _append(
        store,
        "ConfirmEmptyOpeningSecurityPositions",
        f"{account_id}:{valuation_date}",
        [
            _event(
                "EmptyOpeningSecurityPositionsConfirmed",
                "SecurityPositionOpening",
                aggregate_id,
                store.next_aggregate_version("SecurityPositionOpening", aggregate_id),
                command_id,
                payload,
            )
        ],
    )


def empty_opening_security_position_confirmations(
    store: LocalFinanceStore,
) -> dict[str, dict[str, Any]]:
    return {
        event["payload"]["account_id"]: event["payload"]
        for event in store.events("EmptyOpeningSecurityPositionsConfirmed")
    }


def record_closing_security_position(
    store: LocalFinanceStore,
    *,
    account_id: str,
    valuation_date: str,
    security_identifier_type: str,
    security_identifier: str,
    security_name: str,
    quantity: str,
    confirmation: bool,
) -> str:
    account = accounts(store).get(account_id)
    if not account or account["account_type"] != "BROKERAGE":
        raise StoreInvariantError("IMPORT_ACCOUNT_TYPE_MISMATCH")
    date.fromisoformat(valuation_date)
    parsed_quantity = _decimal(quantity, "FINANCE_SECURITY_POSITION_INVALID")
    if parsed_quantity < 0 or not confirmation:
        raise StoreInvariantError("FINANCE_SECURITY_POSITION_INVALID")
    position_id = "closing_position_" + hashlib.sha256(
        f"{account_id}:{security_identifier}".encode()
    ).hexdigest()[:24]
    command_id = _id("cmd")
    payload = {
        "position_id": position_id,
        "account_id": account_id,
        "valuation_date": valuation_date,
        "security_identifier_type": security_identifier_type,
        "security_identifier": security_identifier,
        "security_name": security_name,
        "quantity": str(parsed_quantity),
        "confirmation": True,
    }
    _append(
        store,
        "RecordClosingSecurityPosition",
        hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest(),
        [
            _event(
                "ClosingSecurityPositionRecorded",
                "ClosingSecurityPosition",
                position_id,
                store.next_aggregate_version("ClosingSecurityPosition", position_id),
                command_id,
                payload,
            )
        ],
    )
    return position_id


def closing_security_positions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["aggregate_type"] != "ClosingSecurityPosition":
            continue
        if event["event_type"] == "ClosingSecurityPositionRecorded":
            result[event["aggregate_id"]] = event["payload"]
        elif (
            event["event_type"] == "SecurityPositionSnapshotCorrected"
            and event["aggregate_id"] in result
        ):
            result[event["aggregate_id"]] = event["payload"]["position"]
    return result


def correct_security_position_snapshot(
    store: LocalFinanceStore,
    *,
    snapshot_id: str,
    quantity: str,
    reason: str,
) -> int:
    """Correct a reported opening or closing quantity without rewriting history."""

    parsed_quantity = _decimal(quantity, "FINANCE_SECURITY_POSITION_INVALID")
    if parsed_quantity < 0 or not reason.strip():
        raise StoreInvariantError("FINANCE_SECURITY_POSITION_INVALID")
    aggregate_type: str | None = None
    current: dict[str, Any] | None = None
    for candidate_type, projection in (
        ("SecurityPosition", opening_security_positions(store)),
        ("ClosingSecurityPosition", closing_security_positions(store)),
    ):
        if snapshot_id in projection:
            aggregate_type = candidate_type
            current = projection[snapshot_id]
            break
    if not aggregate_type or not current:
        raise StoreInvariantError("FINANCE_SECURITY_POSITION_NOT_FOUND")
    position = {**current, "quantity": str(parsed_quantity)}
    command_id = _id("cmd")
    payload = {
        "snapshot_id": snapshot_id,
        "corrected_snapshot_id": snapshot_id,
        "previous_quantity": current["quantity"],
        "quantity": str(parsed_quantity),
        "reason": reason.strip(),
        "corrected_at": _now(),
        "position": position,
    }
    return _append(
        store,
        "CorrectSecurityPositionSnapshot",
        hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest(),
        [
            _event(
                "SecurityPositionSnapshotCorrected",
                aggregate_type,
                snapshot_id,
                store.next_aggregate_version(aggregate_type, snapshot_id),
                command_id,
                payload,
            )
        ],
    )


def security_positions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result = opening_security_positions(store)
    for transaction in security_transactions(store).values():
        position_id = "position_" + hashlib.sha256(
            f"{transaction['account_id']}:{transaction['security_identifier']}".encode()
        ).hexdigest()[:24]
        current = result.get(position_id)
        existing_quantity = Decimal(current["quantity"]) if current else Decimal("0")
        transaction_quantity = abs(Decimal(transaction["quantity"]))
        signed_quantity = (
            transaction_quantity
            if transaction["transaction_type"] == "INVESTMENT_PURCHASE"
            else -transaction_quantity
            if transaction["transaction_type"] == "INVESTMENT_SALE"
            else Decimal("0")
        )
        result[position_id] = {
            "position_id": position_id,
            "account_id": transaction["account_id"],
            "valuation_date": transaction["booking_date"],
            "security_identifier_type": transaction["security_identifier_type"],
            "security_identifier": transaction["security_identifier"],
            "security_name": transaction["security_name"],
            "quantity": str(existing_quantity + signed_quantity),
            "valuation_price": None,
            "price_currency": transaction["price_currency"],
            "market_value": None,
            "valuation_source": "UNVALUED_AFTER_TRANSACTION",
            "confirmation": True,
        }
    return result


def security_transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {event["aggregate_id"]: event["payload"] for event in store.events("SecurityTransactionNormalized")}


def _account_record(record: dict[str, str]) -> dict[str, str]:
    amount = parse_german_decimal(record["Umsatz in EUR"])
    raw_type = record["Vorgang"].strip()
    folded = raw_type.casefold()
    if "entgelt" in folded:
        transaction_type = "FEE"
    elif "wertpapier" in folded:
        transaction_type = "INVESTMENT_PURCHASE_FUNDING"
    elif "übertrag" in folded or "überweisung" in folded:
        transaction_type = "TRANSFER_OR_PAYMENT_REVIEW"
    else:
        transaction_type = "INCOME" if amount >= 0 else "EXPENSE"
    return {
        "booking_date": _parse_date(record["Buchungstag"]),
        "value_date": _parse_date(record["Wertstellung (Valuta)"]),
        "amount": str(amount),
        "currency": "EUR",
        "counterparty": "",
        "description": record["Buchungstext"].strip(),
        "raw_transaction_type": raw_type,
        "transaction_type": transaction_type,
    }


def _security_record(record: dict[str, str]) -> dict[str, str]:
    quantity = parse_german_quantity(record["Stück / Nom."])
    execution = parse_german_decimal(record["Ausführungskurs"])
    settlement = parse_german_decimal(record["Umsatz in EUR"])
    transaction_type = "INVESTMENT_PURCHASE" if settlement < 0 else "INVESTMENT_SALE" if settlement > 0 else "UNKNOWN_SECURITY_TRANSACTION"
    return {
        "booking_date": _parse_date(record["Buchungstag"]),
        "trade_date": _parse_date(record["Geschäftstag"]),
        "security_name": record["Bezeichnung"].strip(),
        "security_identifier_type": "WKN",
        "security_identifier": record["WKN"].strip(),
        "quantity": str(quantity),
        "execution_price": str(execution),
        "price_currency": record["Währung"].strip() or "EUR",
        "settlement_amount": str(settlement),
        "settlement_currency": "EUR",
        "transaction_type": transaction_type,
    }


def _possible_investment_funding_matches(
    account_rows: list[tuple[dict[str, Any], dict[str, str], str]],
    security_rows: list[tuple[dict[str, Any], dict[str, str], str]],
) -> int:
    count = 0
    for _, cash, _ in account_rows:
        if cash["transaction_type"] != "INVESTMENT_PURCHASE_FUNDING":
            continue
        for _, security, _ in security_rows:
            if security["transaction_type"] != "INVESTMENT_PURCHASE":
                continue
            if abs(Decimal(cash["amount"])) != abs(
                Decimal(security["settlement_amount"])
            ):
                continue
            if abs(
                (
                    date.fromisoformat(cash["booking_date"])
                    - date.fromisoformat(security["trade_date"])
                ).days
            ) > FUNDING_WINDOW_DAYS:
                continue
            if (
                security["security_identifier"].casefold()
                not in cash["description"].casefold()
            ):
                continue
            if cash["currency"] != security["settlement_currency"]:
                continue
            count += 1
    return count


def get_import_section_preview(
    store: LocalFinanceStore, analysis_id: str, section_id: str
) -> dict[str, Any]:
    analysis = get_import_analysis(store, analysis_id)
    section = next((item for item in _load_parsed(store, analysis_id) if item["section_id"] == section_id), None)
    if not section:
        raise MultiAccountImportError("IMPORT_SECTION_NOT_FOUND")
    records = [
        _security_record(record)
        if section["section_type"] == "BROKERAGE"
        else _account_record(record)
        for record in section["records"]
    ]
    preview = records[:25]
    booking_dates = sorted(
        item.get("booking_date") or item.get("trade_date")
        for item in records
        if item.get("booking_date") or item.get("trade_date")
    )
    amount_key = (
        "settlement_amount" if section["section_type"] == "BROKERAGE" else "amount"
    )
    amount_sum = sum(
        (Decimal(item[amount_key]) for item in records), Decimal("0")
    )
    content_keys = [
        tuple(sorted((key, str(value)) for key, value in item.items()))
        for item in records
    ]
    duplicate_count = len(content_keys) - len(set(content_keys))
    mappings = section_mappings(store, analysis_id)
    return {
        **{key: value for key, value in section.items() if key != "records"},
        "period_start": analysis["period_start"] if analysis else section["period_start"],
        "period_end": analysis["period_end"] if analysis else section["period_end"],
        "mapped_account_id": mappings.get(section_id, {}).get("account_id"),
        "preview": preview,
        "first_records": records[:5],
        "last_records": records[-5:],
        "first_booking_date": booking_dates[0] if booking_dates else None,
        "last_booking_date": booking_dates[-1] if booking_dates else None,
        "amount_sum": str(amount_sum),
        "duplicate_candidate_count": duplicate_count,
        "overlap_candidate_count": 0,
        "initial_value": None,
        "reported_end_value": None,
        "security_count": len(
            {item["security_identifier"] for item in records}
        )
        if section["section_type"] == "BROKERAGE"
        else 0,
        "preview_truncated": len(section["records"]) > 25,
    }


def initial_balance_requirements(store: LocalFinanceStore, analysis_id: str) -> list[dict[str, Any]]:
    analysis = get_import_analysis(store, analysis_id)
    if not analysis:
        raise MultiAccountImportError("IMPORT_ANALYSIS_NOT_FOUND")
    mapped = section_mappings(store, analysis_id)
    openings = opening_balances(store)
    positions = opening_security_positions(store)
    empty_position_confirmations = empty_opening_security_position_confirmations(store)
    normalized_accounts = {event["payload"]["account_id"] for event in store.events("TransactionNormalized")}
    requirements: list[dict[str, Any]] = []
    for section in analysis["sections"]:
        mapping = mapped.get(section["section_id"])
        if not mapping or not mapping["account_id"]:
            continue
        account_id = mapping["account_id"]
        first_import = account_id not in normalized_accounts
        if section["section_type"] in {"CHECKING", "SAVINGS"}:
            opening = openings.get(account_id)
            satisfied = not first_import or bool(
                opening
                and opening["confirmation"]
                and opening["balance_date"] < analysis["period_start"]
            )
            requirements.append({"account_id": account_id, "section_id": section["section_id"], "requirement_type": "OPENING_BALANCE", "required": first_import, "satisfied": satisfied, "record": opening})
        elif section["section_type"] == "BROKERAGE":
            existing = [value for value in positions.values() if value["account_id"] == account_id]
            first_security_import = not any(
                item["account_id"] == account_id
                for item in security_transactions(store).values()
            )
            satisfied = (
                not first_security_import
                or any(
                    item["valuation_date"] < analysis["period_start"]
                    for item in existing
                )
                or bool(
                    empty_position_confirmations.get(account_id)
                    and empty_position_confirmations[account_id]["valuation_date"]
                    < analysis["period_start"]
                )
            )
            requirements.append({"account_id": account_id, "section_id": section["section_id"], "requirement_type": "OPENING_SECURITY_POSITIONS", "required": first_security_import, "satisfied": satisfied, "records": existing, "empty_confirmation": empty_position_confirmations.get(account_id)})
    return requirements


def import_mapped_sections(
    store: LocalFinanceStore,
    analysis_id: str,
    *,
    parser_profile: str = PROFILE_ID,
    parser_version: str = PROFILE_VERSION,
    import_mode: str = "IMPORT_NEW",
) -> dict[str, Any]:
    if parser_profile != PROFILE_ID or parser_version != PROFILE_VERSION:
        raise MultiAccountImportError("IMPORT_PROFILE_MISMATCH")
    if import_mode not in IMPORT_MODES:
        raise MultiAccountImportError("IMPORT_MODE_INVALID")
    analysis = get_import_analysis(store, analysis_id)
    if not analysis:
        raise MultiAccountImportError("IMPORT_ANALYSIS_NOT_FOUND")
    sections = _load_parsed(store, analysis_id)
    for section in sections:
        section["period_start"] = analysis["period_start"]
        section["period_end"] = analysis["period_end"]
    mappings = section_mappings(store, analysis_id)
    if any(section["section_id"] not in mappings for section in sections):
        raise MultiAccountImportError("IMPORT_SECTION_MAPPING_MISSING")
    if any(section["section_type"] == "UNKNOWN" and mappings[section["section_id"]]["action"] != "SKIP_SECTION" for section in sections):
        raise MultiAccountImportError("IMPORT_UNKNOWN_SECTION")

    account_rows: list[tuple[dict[str, Any], dict[str, str], str]] = []
    security_rows: list[tuple[dict[str, Any], dict[str, str], str]] = []
    section_results: list[dict[str, Any]] = []
    requirements = {
        item["section_id"]: item
        for item in initial_balance_requirements(store, analysis_id)
    }
    prior_runs = list(imported_section_runs(store).values())
    reused_section_ids: set[str] = set()
    for section in sections:
        mapping = mappings[section["section_id"]]
        warnings = list(section["warnings"])
        base_result = {
            "section_id": section["section_id"],
            "section_type": section["section_type"],
            "account_id": mapping["account_id"],
            "period_start": analysis["period_start"],
            "period_end": analysis["period_end"],
            "content_hash": section["content_hash"],
            "record_count": 0,
            "warnings": warnings,
        }
        if mapping["action"] == "SKIP_SECTION":
            section_results.append({**base_result, "status": "SKIPPED"})
            continue
        account_id = mapping["account_id"]
        overlapping = [
            run
            for run in prior_runs
            if run["account_id"] == account_id
            and run["period_start"] == analysis["period_start"]
            and run["period_end"] == analysis["period_end"]
            and run["section_type"] == section["section_type"]
            and run["status"] in {"IMPORTED", "EMPTY_COMPLETED"}
        ]
        if import_mode != "FORCE_REIMPORT" and overlapping:
            same_export = next(
                (run for run in overlapping if run["export_id"] == analysis_id),
                None,
            )
            if same_export:
                reused_section_ids.add(section["section_id"])
                section_results.append(
                    {
                        **base_result,
                        "status": same_export["status"],
                        "record_count": same_export["record_count"],
                        "warnings": list(same_export["warnings"]),
                    }
                )
                continue
            code = (
                "IMPORT_SECTION_DUPLICATE"
                if any(run["content_hash"] == section["content_hash"] for run in overlapping)
                else "IMPORT_PERIOD_OVERLAP"
            )
            section_results.append(
                {**base_result, "status": "REVIEW_REQUIRED", "warnings": warnings + [code]}
            )
            continue
        requirement = requirements.get(section["section_id"])
        if requirement and requirement["required"] and not requirement["satisfied"]:
            section_results.append(
                {
                    **base_result,
                    "status": "REVIEW_REQUIRED",
                    "warnings": warnings + ["IMPORT_OPENING_BALANCE_REQUIRED"],
                }
            )
            continue
        if not section["import_supported"]:
            section_results.append(
                {
                    **base_result,
                    "status": "FAILED",
                    "warnings": warnings + ["IMPORT_PROFILE_MISMATCH"],
                }
            )
            continue
        if section["empty"]:
            section_results.append({**base_result, "status": "EMPTY_COMPLETED"})
            continue
        parsed_rows: list[dict[str, str]] = []
        try:
            for record in section["records"]:
                parsed = (
                    _security_record(record)
                    if section["section_type"] == "BROKERAGE"
                    else _account_record(record)
                )
                if not account_accepts_transaction(store, account_id, parsed["booking_date"]):
                    raise MultiAccountImportError("FINANCE_IMPORT_ACCOUNT_CLOSED")
                if not analysis["period_start"] <= parsed["booking_date"] <= analysis["period_end"]:
                    raise MultiAccountImportError("IMPORT_PERIOD_MISMATCH")
                parsed_rows.append(parsed)
        except MultiAccountImportError as exc:
            section_results.append(
                {
                    **base_result,
                    "status": "FAILED",
                    "warnings": warnings + [str(exc)],
                }
            )
            continue
        target = security_rows if section["section_type"] == "BROKERAGE" else account_rows
        target.extend((section, parsed, account_id) for parsed in parsed_rows)
        section_results.append(
            {
                **base_result,
                "status": "IMPORTED",
                "record_count": len(parsed_rows),
            }
        )
    newly_completed = bool(account_rows or security_rows) or any(
        item["status"] == "EMPTY_COMPLETED"
        and item["section_id"] not in reused_section_ids
        for item in section_results
    )
    if reused_section_ids and not newly_completed and all(
        item["status"] in {"IMPORTED", "EMPTY_COMPLETED", "SKIPPED"}
        for item in section_results
    ):
        section_results = [
            {
                **item,
                "status": (
                    "REVIEW_REQUIRED"
                    if item["section_id"] in reused_section_ids
                    else item["status"]
                ),
                "warnings": (
                    item["warnings"] + ["IMPORT_SECTION_DUPLICATE"]
                    if item["section_id"] in reused_section_ids
                    else item["warnings"]
                ),
            }
            for item in section_results
        ]
    statuses = {item["status"] for item in section_results}
    successful = bool(statuses & {"IMPORTED", "EMPTY_COMPLETED"})
    if "REVIEW_REQUIRED" in statuses:
        overall_status = "REVIEW_REQUIRED"
    elif "FAILED" in statuses and successful:
        overall_status = "PARTIALLY_COMPLETED"
    elif "FAILED" in statuses:
        overall_status = "FAILED"
    else:
        overall_status = "COMPLETED"
    preview = {
        "export_id": analysis_id,
        "import_batch_id": None,
        "section_results": section_results,
        "raw_transaction_count": len(account_rows),
        "normalized_transaction_count": len(account_rows),
        "security_transaction_count": len(security_rows),
        "possible_relation_match_count": _possible_investment_funding_matches(
            account_rows, security_rows
        ),
        "empty_section_count": sum(item["status"] == "EMPTY_COMPLETED" for item in section_results),
        "warnings": sorted(
            set(analysis["warnings"]).union(
                warning for result in section_results for warning in result["warnings"]
            )
        ),
        "status": "VALIDATED" if import_mode == "VALIDATE_ONLY" else overall_status,
    }
    if import_mode == "VALIDATE_ONLY":
        return preview

    suffix = analysis["file_hash"] if import_mode == "IMPORT_NEW" else f"{analysis['file_hash']}_{uuid.uuid4().hex}"
    batch_id = f"imp_multi_{suffix}"
    existing = [event for event in store.events("ImportBatchStarted") if event["aggregate_id"] == batch_id]
    if existing:
        if newly_completed:
            batch_id = f"{batch_id}_retry_{uuid.uuid4().hex}"
        else:
            return {**preview, "import_batch_id": batch_id}
    command_id = _id("cmd")
    events: list[dict[str, Any]] = [
        _event("ImportBatchStarted", "ImportBatch", batch_id, 1, command_id, {"import_batch_id": batch_id, "file_hash": analysis["file_hash"], "parser_version": PROFILE_VERSION})
    ]
    batch_version = 2
    for index, (section, row, account_id) in enumerate(account_rows, start=1):
        raw_fields = {key: row[key] for key in ("booking_date", "value_date", "amount", "currency", "counterparty", "description")}
        content_hash = "sha256:" + hashlib.sha256(repr(sorted(raw_fields.items())).encode()).hexdigest()
        raw_event = _event("RawTransactionImported", "ImportBatch", batch_id, batch_version, command_id, {"import_batch_id": batch_id, "export_id": analysis_id, "section_id": section["section_id"], "section_content_hash": section["content_hash"], "source_record_index": index, "account_id": account_id, "raw_fields": raw_fields, "content_hash": content_hash})
        events.append(raw_event)
        batch_version += 1
        transaction_id = "txn_" + hashlib.sha256(f"{batch_id}:{section['section_id']}:{index}:{content_hash}".encode()).hexdigest()[:32]
        events.append(_event("TransactionNormalized", "Transaction", transaction_id, 1, command_id, {"transaction_id": transaction_id, "raw_transaction_event_id": raw_event["event_id"], "import_batch_id": batch_id, "export_id": analysis_id, "section_id": section["section_id"], "account_id": account_id, "booking_date": row["booking_date"], "value_date": row["value_date"], "amount": row["amount"], "currency": row["currency"], "direction": "CREDIT" if Decimal(row["amount"]) >= 0 else "DEBIT", "counterparty": row["counterparty"], "normalized_description": row["description"], "raw_transaction_type": row["raw_transaction_type"], "transaction_type": row["transaction_type"], "normalization_policy_version": PROFILE_VERSION}))
    for index, (section, row, account_id) in enumerate(security_rows, start=1):
        transaction_id = "sec_txn_" + hashlib.sha256(f"{batch_id}:{section['section_id']}:{index}:{repr(sorted(row.items()))}".encode()).hexdigest()[:32]
        events.append(_event("SecurityTransactionNormalized", "SecurityTransaction", transaction_id, 1, command_id, {"transaction_id": transaction_id, "import_batch_id": batch_id, "section_id": section["section_id"], "account_id": account_id, **row, "normalization_policy_version": PROFILE_VERSION}))
    for result in section_results:
        if result["section_id"] in reused_section_ids:
            continue
        run_id = f"{batch_id}_{result['section_id']}"
        events.append(
            _event(
                "ImportSectionCompleted",
                "ImportSectionRun",
                run_id,
                1,
                command_id,
                {
                    "section_run_id": run_id,
                    "import_batch_id": batch_id,
                    "export_id": analysis_id,
                    **result,
                },
            )
        )
        if result["status"] == "EMPTY_COMPLETED":
            events.append(_event("EmptyImportSectionProcessed", "ImportSection", run_id, 1, command_id, {"import_batch_id": batch_id, "analysis_id": analysis_id, "export_id": analysis_id, "section_id": result["section_id"], "account_id": result["account_id"], "status": "EMPTY_COMPLETED"}))
    events.append(_event("ImportBatchCompleted", "ImportBatch", batch_id, batch_version, command_id, {"import_batch_id": batch_id, "record_count": len(account_rows), "status": overall_status, "section_count": len(section_results)}))
    _append(store, "ImportMappedSections", batch_id, events)
    store.update_import_status(analysis["file_hash"], overall_status)
    return {**preview, "import_batch_id": batch_id}


def imported_section_runs(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["aggregate_id"]: event["payload"]
        for event in store.events("ImportSectionCompleted")
    }


def _latest_relations(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"].startswith("InvestmentFundingRelation"):
            result[event["aggregate_id"]] = event
    return result


def investment_funding_relations(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {key: event["payload"] for key, event in _latest_relations(store).items()}


def detect_investment_funding_relations(store: LocalFinanceStore) -> int:
    raw_by_id = {event["event_id"]: event["payload"] for event in store.events("RawTransactionImported")}
    funding = [event["payload"] for event in store.events("TransactionNormalized") if event["payload"].get("transaction_type") == "INVESTMENT_PURCHASE_FUNDING" and event["payload"]["direction"] == "DEBIT"]
    purchases = [item for item in security_transactions(store).values() if item["transaction_type"] == "INVESTMENT_PURCHASE"]
    latest = _latest_relations(store)
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for cash in funding:
        raw = raw_by_id.get(cash["raw_transaction_event_id"], {})
        for security in purchases:
            same_batch = raw.get("import_batch_id") == security["import_batch_id"]
            amount_matches = abs(Decimal(cash["amount"])) == abs(Decimal(security["settlement_amount"]))
            date_matches = abs((date.fromisoformat(cash["booking_date"]) - date.fromisoformat(security["trade_date"])).days) <= FUNDING_WINDOW_DAYS
            identifier_matches = security["security_identifier"].casefold() in cash["normalized_description"].casefold()
            currency_matches = cash["currency"] == security["settlement_currency"]
            if not all((same_batch, amount_matches, date_matches, identifier_matches, currency_matches)):
                continue
            relation_id = "invfund_" + hashlib.sha256(f"{cash['transaction_id']}:{security['transaction_id']}".encode()).hexdigest()[:24]
            if relation_id in latest:
                continue
            payload = {"relation_id": relation_id, "cash_transaction_id": cash["transaction_id"], "security_transaction_id": security["transaction_id"], "amount": str(abs(Decimal(cash["amount"]))), "currency": cash["currency"], "status": "PROPOSED", "matching_policy_version": "investment-funding@1.0.0"}
            events.append(_event("InvestmentFundingRelationProposed", "InvestmentFundingRelation", relation_id, 1, command_id, payload))
    marker = store.events()[-1]["sequence_number"] if store.events() else 0
    return _append(store, "DetectInvestmentFundingRelations", str(marker), events)


def _decide_funding(store: LocalFinanceStore, relation_id: str, status: str) -> int:
    event = _latest_relations(store).get(relation_id)
    if not event:
        raise StoreInvariantError("FINANCE_INVESTMENT_RELATION_NOT_FOUND")
    if event["payload"]["status"] == status:
        return 0
    if event["payload"]["status"] == "CONFIRMED" and status != "BROKEN":
        raise StoreInvariantError("FINANCE_CONFIRMED_RELATION_PROTECTED")
    kind = {"CONFIRMED": "InvestmentFundingRelationConfirmed", "REJECTED": "InvestmentFundingRelationRejected", "BROKEN": "InvestmentFundingRelationBroken"}[status]
    command_id = _id("cmd")
    payload = {**event["payload"], "status": status, "decided_at": _now()}
    next_version = store.next_aggregate_version("InvestmentFundingRelation", relation_id)
    return _append(store, kind, f"{relation_id}:{status}:{next_version}", [_event(kind, "InvestmentFundingRelation", relation_id, next_version, command_id, payload)])


def confirm_investment_funding_relation(store: LocalFinanceStore, relation_id: str) -> int:
    return _decide_funding(store, relation_id, "CONFIRMED")


def reject_investment_funding_relation(store: LocalFinanceStore, relation_id: str) -> int:
    return _decide_funding(store, relation_id, "REJECTED")


def break_investment_funding_relation(store: LocalFinanceStore, relation_id: str) -> int:
    return _decide_funding(store, relation_id, "BROKEN")


def reconcile_imported_period_balance(
    store: LocalFinanceStore,
    *,
    account_id: str,
    period_start: str,
    period_end: str,
    section_id: str | None = None,
) -> dict[str, Any]:
    from .reconciliation import reconciled_transactions

    start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    if start > end or account_id not in accounts(store):
        raise StoreInvariantError("FINANCE_BALANCE_RECONCILIATION_INVALID")
    candidates = [
        run
        for run in imported_section_runs(store).values()
        if run["account_id"] == account_id
        and run["period_start"] == period_start
        and run["period_end"] == period_end
        and run["section_type"] in {"CHECKING", "SAVINGS"}
        and run["status"] in {"IMPORTED", "EMPTY_COMPLETED"}
        and (section_id is None or run["section_id"] == section_id)
    ]
    if len(candidates) != 1:
        raise StoreInvariantError("FINANCE_IMPORT_SECTION_RECONCILIATION_AMBIGUOUS")
    section_run = candidates[0]
    section_id = section_run["section_id"]
    opening = opening_balances(store).get(account_id)
    closing = closing_balances(store).get(account_id)
    status = "MATCHED"
    calculated: Decimal | None = None
    difference: Decimal | None = None
    relevant_count = 0
    if not opening or not opening["confirmation"] or date.fromisoformat(opening["balance_date"]) >= start:
        status = "MISSING_OPENING_BALANCE"
    else:
        calculated = Decimal(opening["booked_balance"])
        reconciliation = reconciled_transactions(store)
        for event in store.events("TransactionNormalized"):
            item = event["payload"]
            booked = date.fromisoformat(item["booking_date"])
            if item["account_id"] != account_id or not start <= booked <= end:
                continue
            if item.get("section_id") != section_id:
                continue
            if reconciliation[item["transaction_id"]]["duplicate_status"] == "CONFIRMED":
                continue
            calculated += Decimal(item["amount"])
            relevant_count += 1
        if not closing or not closing["confirmation"] or date.fromisoformat(closing["balance_date"]) != end:
            status = "NO_REPORTED_CLOSING_BALANCE"
        else:
            difference = Decimal(closing["booked_balance"]) - calculated
            status = "MATCHED" if difference == 0 else "DIFFERENCE"
    reconciliation_id = "import_balance_" + hashlib.sha256(f"{account_id}:{section_id}:{period_start}:{period_end}".encode()).hexdigest()[:24]
    payload = {"reconciliation_id": reconciliation_id, "export_id": section_run["export_id"], "section_id": section_id, "section_type": section_run["section_type"], "account_id": account_id, "period_start": period_start, "period_end": period_end, "opening_balance": opening["booked_balance"] if opening else None, "calculated_closing_balance": str(calculated) if calculated is not None else None, "reported_closing_balance": closing["booked_balance"] if closing else None, "balance_difference": str(difference) if difference is not None else None, "relevant_transaction_count": relevant_count, "status": status, "reconciled_at": _now()}
    command_id = _id("cmd")
    marker = store.events()[-1]["sequence_number"] if store.events() else 0
    _append(store, "ReconcileImportedPeriodBalance", f"{reconciliation_id}:{marker}", [_event("ImportedPeriodBalanceReconciled", "ImportedPeriodBalance", reconciliation_id, store.next_aggregate_version("ImportedPeriodBalance", reconciliation_id), command_id, payload)])
    return payload


def imported_period_reconciliations(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events("ImportedPeriodBalanceReconciled"):
        result[event["aggregate_id"]] = event["payload"]
    return result


def document_balance_difference(
    store: LocalFinanceStore,
    *,
    reconciliation_id: str,
    explanation: str,
) -> int:
    reconciliation = imported_period_reconciliations(store).get(reconciliation_id)
    if (
        not reconciliation
        or reconciliation["status"] != "DIFFERENCE"
        or not explanation.strip()
    ):
        raise StoreInvariantError("FINANCE_BALANCE_DIFFERENCE_INVALID")
    command_id = _id("cmd")
    payload = {
        "documentation_id": _id("balance_difference"),
        "reconciliation_id": reconciliation_id,
        "export_id": reconciliation["export_id"],
        "section_id": reconciliation["section_id"],
        "account_id": reconciliation["account_id"],
        "period_start": reconciliation["period_start"],
        "period_end": reconciliation["period_end"],
        "balance_difference": reconciliation["balance_difference"],
        "explanation": explanation.strip(),
        "documented_at": _now(),
    }
    aggregate_id = payload["documentation_id"]
    return _append(
        store,
        "DocumentBalanceDifference",
        hashlib.sha256(
            f"{reconciliation_id}:{explanation.strip()}".encode()
        ).hexdigest(),
        [
            _event(
                "BalanceDifferenceDocumented",
                "BalanceDifferenceDocumentation",
                aggregate_id,
                1,
                command_id,
                payload,
            )
        ],
    )


def balance_difference_documentations(
    store: LocalFinanceStore,
) -> dict[str, dict[str, Any]]:
    return {
        event["aggregate_id"]: event["payload"]
        for event in store.events("BalanceDifferenceDocumented")
    }


def reconcile_imported_security_positions(
    store: LocalFinanceStore,
    *,
    account_id: str,
    period_start: str,
    period_end: str,
    section_id: str | None = None,
) -> dict[str, Any]:
    start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    candidates = [
        run
        for run in imported_section_runs(store).values()
        if run["account_id"] == account_id
        and run["period_start"] == period_start
        and run["period_end"] == period_end
        and run["section_type"] == "BROKERAGE"
        and run["status"] in {"IMPORTED", "EMPTY_COMPLETED"}
        and (section_id is None or run["section_id"] == section_id)
    ]
    if start > end or len(candidates) != 1:
        raise StoreInvariantError("FINANCE_IMPORT_SECTION_RECONCILIATION_AMBIGUOUS")
    section_run = candidates[0]
    section_id = section_run["section_id"]
    opening = {
        item["security_identifier"]: Decimal(item["quantity"])
        for item in opening_security_positions(store).values()
        if item["account_id"] == account_id
        and date.fromisoformat(item["valuation_date"]) < start
    }
    empty_confirmation = empty_opening_security_position_confirmations(store).get(
        account_id
    )
    status = "MATCHED"
    calculated = dict(opening)
    unknown_transactions = 0
    if not opening and not (
        empty_confirmation
        and date.fromisoformat(empty_confirmation["valuation_date"]) < start
    ):
        status = "MISSING_OPENING_POSITIONS"
    else:
        for item in security_transactions(store).values():
            if (
                item["account_id"] != account_id
                or item["section_id"] != section_id
                or not start <= date.fromisoformat(item["booking_date"]) <= end
            ):
                continue
            identifier = item["security_identifier"]
            quantity = abs(Decimal(item["quantity"]))
            if item["transaction_type"] == "INVESTMENT_PURCHASE":
                calculated[identifier] = calculated.get(identifier, Decimal("0")) + quantity
            elif item["transaction_type"] == "INVESTMENT_SALE":
                calculated[identifier] = calculated.get(identifier, Decimal("0")) - quantity
            else:
                unknown_transactions += 1
        reported = {
            item["security_identifier"]: Decimal(item["quantity"])
            for item in closing_security_positions(store).values()
            if item["account_id"] == account_id
            and date.fromisoformat(item["valuation_date"]) == end
        }
        if unknown_transactions:
            status = "REVIEW_REQUIRED"
        elif not reported:
            status = "NO_REPORTED_CLOSING_POSITIONS"
        elif reported != calculated:
            status = "DIFFERENCE"
        else:
            status = "MATCHED"
    reported_values = {
        item["security_identifier"]: item["quantity"]
        for item in closing_security_positions(store).values()
        if item["account_id"] == account_id
        and date.fromisoformat(item["valuation_date"]) == end
    }
    identifiers = sorted(set(calculated) | set(reported_values))
    differences = {
        identifier: str(
            Decimal(reported_values.get(identifier, "0"))
            - calculated.get(identifier, Decimal("0"))
        )
        for identifier in identifiers
    }
    reconciliation_id = "security_balance_" + hashlib.sha256(
        f"{account_id}:{section_id}:{period_start}:{period_end}".encode()
    ).hexdigest()[:24]
    payload = {
        "reconciliation_id": reconciliation_id,
        "export_id": section_run["export_id"],
        "section_id": section_id,
        "section_type": "BROKERAGE",
        "account_id": account_id,
        "period_start": period_start,
        "period_end": period_end,
        "calculated_positions": {
            key: str(value) for key, value in sorted(calculated.items())
        },
        "reported_positions": dict(sorted(reported_values.items())),
        "position_differences": differences,
        "unknown_transaction_count": unknown_transactions,
        "status": status,
        "reconciled_at": _now(),
    }
    command_id = _id("cmd")
    marker = store.events()[-1]["sequence_number"] if store.events() else 0
    _append(
        store,
        "ReconcileImportedSecurityPositions",
        f"{reconciliation_id}:{marker}",
        [
            _event(
                "ImportedSecurityPositionsReconciled",
                "ImportedSecurityPositions",
                reconciliation_id,
                store.next_aggregate_version(
                    "ImportedSecurityPositions", reconciliation_id
                ),
                command_id,
                payload,
            )
        ],
    )
    return payload


def imported_security_position_reconciliations(
    store: LocalFinanceStore,
) -> dict[str, dict[str, Any]]:
    return {
        event["aggregate_id"]: event["payload"]
        for event in store.events("ImportedSecurityPositionsReconciled")
    }


def reconcile_imported_period_positions(
    store: LocalFinanceStore, **payload: Any
) -> dict[str, Any]:
    """UI-facing command name retained as an explicit domain alias."""

    return reconcile_imported_security_positions(store, **payload)


__all__ = [
    "PROFILE_ID",
    "PROFILE_VERSION",
    "MultiAccountImportError",
    "analyze_import_file",
    "break_investment_funding_relation",
    "balance_difference_documentations",
    "closing_balances",
    "closing_security_positions",
    "confirm_empty_opening_security_positions",
    "confirm_investment_funding_relation",
    "correct_security_position_snapshot",
    "detect_investment_funding_relations",
    "document_balance_difference",
    "get_import_analysis",
    "get_bank_monthly_export",
    "get_import_section_preview",
    "import_mapped_sections",
    "imported_period_reconciliations",
    "imported_section_runs",
    "imported_security_position_reconciliations",
    "initial_balance_requirements",
    "investment_funding_relations",
    "list_import_sections",
    "map_import_sections",
    "opening_balances",
    "opening_security_positions",
    "parse_german_decimal",
    "parse_german_quantity",
    "reconcile_imported_period_balance",
    "reconcile_imported_period_positions",
    "reconcile_imported_security_positions",
    "record_closing_balance",
    "record_closing_security_position",
    "record_opening_balance",
    "record_opening_security_position",
    "reject_investment_funding_relation",
    "security_positions",
    "security_transactions",
    "section_bindings",
]
