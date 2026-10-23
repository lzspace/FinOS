"""Event-sourced category catalogue with backwards-compatible stable codes."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from .store import LocalFinanceStore, StoreInvariantError

CATEGORY_CODES = (
    "INCOME_SALARY",
    "INCOME_OTHER",
    "HOUSING_RENT",
    "HOUSING_UTILITIES",
    "FOOD_GROCERIES",
    "FOOD_RESTAURANTS",
    "MOBILITY_PUBLIC_TRANSPORT",
    "MOBILITY_FUEL",
    "HEALTH",
    "INSURANCE",
    "LEISURE",
    "SUBSCRIPTIONS",
    "EDUCATION",
    "FEES",
    "TAXES",
    "OTHER_EXPENSE",
    "UNCLASSIFIED",
)

CUSTOM_CATEGORY_PATTERN = re.compile(r"^CUSTOM_[A-Z0-9_]{1,48}$")
CATEGORY_TYPES = ("INCOME", "EXPENSE", "TRANSFER", "INVESTMENT")
CATEGORY_POLICY_VERSION = "categories@1.0.0"

_BUILTIN_NAMES = {
    "INCOME_SALARY": "Gehalt", "INCOME_OTHER": "Sonstige Einnahmen",
    "HOUSING_RENT": "Miete", "HOUSING_UTILITIES": "Wohnen & Nebenkosten",
    "FOOD_GROCERIES": "Lebensmittel", "FOOD_RESTAURANTS": "Restaurant",
    "MOBILITY_PUBLIC_TRANSPORT": "Öffentlicher Verkehr", "MOBILITY_FUEL": "Tanken",
    "HEALTH": "Gesundheit", "INSURANCE": "Versicherungen", "LEISURE": "Freizeit",
    "SUBSCRIPTIONS": "Abonnements", "EDUCATION": "Bildung", "FEES": "Gebühren",
    "TAXES": "Steuern", "OTHER_EXPENSE": "Sonstige Ausgaben",
    "UNCLASSIFIED": "Nicht kategorisiert",
}


def require_category(code: str) -> str:
    if code not in CATEGORY_CODES and not CUSTOM_CATEGORY_PATTERN.fullmatch(code):
        raise ValueError("FINANCE_CATEGORY_UNKNOWN")
    return code


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _event(kind: str, code: str, version: int, command_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "event_id": _id("evt"), "event_type": kind,
        "aggregate_type": "Category", "aggregate_id": code,
        "aggregate_version": version, "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": command_id, "causation_id": command_id, "payload": payload,
    }


def _append(store: LocalFinanceStore, command_name: str, code: str, event: dict[str, Any]) -> int:
    return len(store.append_events(
        {"command_id": event["correlation_id"], "command_type": command_name,
         "idempotency_key": f"{command_name}:{code}:{event['aggregate_version']}"},
        [event],
    ))


def _custom_code(display_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", display_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Z0-9]+", "_", ascii_name.upper()).strip("_")[:48]
    if not slug:
        raise StoreInvariantError("FINANCE_CATEGORY_NAME_INVALID")
    return f"CUSTOM_{slug}"


def categories(store: LocalFinanceStore) -> list[dict[str, Any]]:
    catalogue: dict[str, dict[str, Any]] = {
        code: {"category_code": code, "display_name": _BUILTIN_NAMES[code],
               "category_type": "INCOME" if code.startswith("INCOME_") else "EXPENSE",
               "status": "ACTIVE", "system_category": True,
               "parent_category_code": None, "color": None, "icon": None, "legacy": False}
        for code in CATEGORY_CODES
    }
    events = store.events()
    legacy_codes = {
        str(event["payload"].get("category_code", ""))
        for event in events
        if CUSTOM_CATEGORY_PATTERN.fullmatch(str(event["payload"].get("category_code", "")))
    }
    for code in sorted(legacy_codes - catalogue.keys()):
        catalogue[code] = {
            "category_code": code,
            "display_name": code.removeprefix("CUSTOM_").replace("_", " ").title(),
            "category_type": "EXPENSE", "status": "ACTIVE", "system_category": False,
            "parent_category_code": None, "color": None, "icon": None, "legacy": True,
        }
    for event in events:
        payload = event["payload"]
        code = str(payload.get("category_code", ""))
        if event["aggregate_type"] != "Category":
            continue
        if event["event_type"] == "CategoryCreated":
            catalogue[code] = {**payload, "legacy": False}
        elif code in catalogue and event["event_type"] == "CategoryRenamed":
            catalogue[code]["display_name"] = payload["display_name"]
        elif code in catalogue and event["event_type"] == "CategoryArchived":
            catalogue[code]["status"] = "ARCHIVED"
        elif code in catalogue and event["event_type"] == "CategoryRestored":
            catalogue[code]["status"] = "ACTIVE"
    return list(catalogue.values())


def create_category(
    store: LocalFinanceStore, *, display_name: str, category_type: str,
    parent_category_code: str | None = None, color: str | None = None,
    icon: str | None = None, category_code: str | None = None,
) -> dict[str, Any]:
    name = display_name.strip()
    if not name or len(name) > 80:
        raise StoreInvariantError("FINANCE_CATEGORY_NAME_INVALID")
    if category_type not in CATEGORY_TYPES:
        raise StoreInvariantError("FINANCE_CATEGORY_TYPE_INVALID")
    code = category_code or _custom_code(name)
    if not CUSTOM_CATEGORY_PATTERN.fullmatch(code):
        raise StoreInvariantError("FINANCE_CATEGORY_CODE_INVALID")
    existing = {item["category_code"]: item for item in categories(store)}
    if code in existing:
        raise StoreInvariantError("FINANCE_CATEGORY_ALREADY_EXISTS")
    if parent_category_code:
        parent = existing.get(parent_category_code)
        if not parent or parent["status"] != "ACTIVE":
            raise StoreInvariantError("FINANCE_CATEGORY_PARENT_INVALID")
    payload = {
        "category_code": code, "display_name": name, "category_type": category_type,
        "status": "ACTIVE", "system_category": False,
        "parent_category_code": parent_category_code, "color": color, "icon": icon,
        "policy_version": CATEGORY_POLICY_VERSION,
    }
    command_id = _id("cmd")
    _append(store, "CreateCategory", code, _event("CategoryCreated", code, 1, command_id, payload))
    return {**payload, "legacy": False}


def rename_category(store: LocalFinanceStore, category_code: str, display_name: str) -> int:
    current = next((item for item in categories(store) if item["category_code"] == category_code), None)
    name = display_name.strip()
    if not current:
        raise StoreInvariantError("FINANCE_CATEGORY_NOT_FOUND")
    if current["system_category"]:
        raise StoreInvariantError("FINANCE_CATEGORY_SYSTEM_IMMUTABLE")
    if not name or len(name) > 80:
        raise StoreInvariantError("FINANCE_CATEGORY_NAME_INVALID")
    command_id = _id("cmd")
    version = store.next_aggregate_version("Category", category_code)
    return _append(store, "RenameCategory", category_code, _event(
        "CategoryRenamed", category_code, version, command_id,
        {"category_code": category_code, "display_name": name, "policy_version": CATEGORY_POLICY_VERSION},
    ))


def set_category_archived(store: LocalFinanceStore, category_code: str, archived: bool) -> int:
    current = next((item for item in categories(store) if item["category_code"] == category_code), None)
    if not current:
        raise StoreInvariantError("FINANCE_CATEGORY_NOT_FOUND")
    if current["system_category"]:
        raise StoreInvariantError("FINANCE_CATEGORY_SYSTEM_IMMUTABLE")
    target = "ARCHIVED" if archived else "ACTIVE"
    if current["status"] == target:
        return 0
    command_name = "ArchiveCategory" if archived else "RestoreCategory"
    event_name = "CategoryArchived" if archived else "CategoryRestored"
    command_id = _id("cmd")
    version = store.next_aggregate_version("Category", category_code)
    return _append(store, command_name, category_code, _event(
        event_name, category_code, version, command_id,
        {"category_code": category_code, "status": target, "policy_version": CATEGORY_POLICY_VERSION},
    ))
