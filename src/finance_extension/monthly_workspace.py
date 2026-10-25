"""Deterministic month-level workspace status derived from existing projections."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from .accounts import accounts
from .import_ui import import_history
from .review_inbox import REVIEW_TYPES, review_inbox
from .store import LocalFinanceStore


SUCCESSFUL_SECTION_STATUSES = {"IMPORTED", "EMPTY_COMPLETED", "SKIPPED"}


def _shift_month(month: str, offset: int) -> str:
    year, number = (int(value) for value in month.split("-"))
    index = year * 12 + number - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _months_between(start: str, end: str) -> list[str]:
    result: list[str] = []
    cursor = start
    while cursor <= end and len(result) < 240:
        result.append(cursor)
        cursor = _shift_month(cursor, 1)
    return result


def _account_active_in_month(account: dict[str, Any], month: str) -> bool:
    opened_at = str(account.get("opened_at") or "0001-01-01")[:7]
    closed_at = str(account.get("closed_at") or "9999-12-31")[:7]
    return opened_at <= month <= closed_at


def monthly_workspace_status(
    store: LocalFinanceStore, *, as_of: str | None = None
) -> dict[str, Any]:
    """Build month status without persisting a second source of truth."""

    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    current_month = f"{as_of_date.year:04d}-{as_of_date.month:02d}"
    account_rows = accounts(store)
    history = import_history(store)
    reviews = review_inbox(store)

    completed_by_month: dict[str, set[str]] = defaultdict(set)
    first_import_by_account: dict[str, str] = {}
    import_evidence_months: set[str] = set()
    for event in store.events("ImportSectionCompleted"):
        payload = event["payload"]
        month = str(payload["period_start"])[:7]
        import_evidence_months.add(month)
        account_id = payload.get("account_id")
        if account_id:
            account_key = str(account_id)
            previous = first_import_by_account.get(account_key)
            first_import_by_account[account_key] = min(previous, month) if previous else month
            if payload.get("status") in SUCCESSFUL_SECTION_STATUSES:
                completed_by_month[month].add(account_key)

    transaction_accounts_by_month: dict[str, set[str]] = defaultdict(set)
    for event in store.events("TransactionNormalized"):
        payload = event["payload"]
        month = str(payload["booking_date"])[:7]
        account_id = str(payload["account_id"])
        transaction_accounts_by_month[month].add(account_id)
        import_evidence_months.add(month)
        previous = first_import_by_account.get(account_id)
        first_import_by_account[account_id] = min(previous, month) if previous else month

    history_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        month = str(row["report_month"])
        history_by_month[month].append(row)
        import_evidence_months.add(month)

    review_items_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unscoped_reviews = 0
    for item in reviews["items"]:
        period = item.get("period")
        if period:
            review_items_by_month[str(period)].append(item)
        else:
            unscoped_reviews += 1

    if not import_evidence_months:
        return {
            "as_of": as_of_date.isoformat(),
            "current_month": current_month,
            "earliest_month": None,
            "latest_data_month": None,
            "latest_complete_month": None,
            "unscoped_review_count": reviews["total_open"],
            "months": [],
        }

    earliest_month = min(import_evidence_months)
    last_month = max(current_month, max(import_evidence_months))
    months: list[dict[str, Any]] = []
    latest_data_month: str | None = None
    latest_complete_month: str | None = None

    for month in _months_between(earliest_month, last_month):
        expected = {
            account_id
            for account_id, first_month in first_import_by_account.items()
            if first_month <= month
            and _account_active_in_month(account_rows.get(account_id, {}), month)
        }
        completed = completed_by_month.get(month, set())
        present = completed | transaction_accounts_by_month.get(month, set())
        month_history = history_by_month.get(month, [])
        has_partial_import = any(
            row.get("status") not in {"COMPLETED"}
            or int(row.get("completed_section_count", 0)) < int(row.get("section_count", 0))
            for row in month_history
        )
        if month > current_month:
            import_status = "NOT_EXPECTED"
        elif not expected and month not in import_evidence_months:
            import_status = "NOT_EXPECTED"
        elif expected and expected.issubset(completed) and not has_partial_import:
            import_status = "COMPLETE"
        elif expected and expected.issubset(present) and not month_history:
            import_status = "LEGACY_DATA"
        elif present or month_history:
            import_status = "PARTIAL"
        else:
            import_status = "MISSING"

        month_reviews = review_items_by_month.get(month, [])
        counts = Counter(str(item["review_type"]) for item in month_reviews)
        if import_status == "NOT_EXPECTED":
            status = "OUTSIDE"
        elif import_status in {"MISSING", "PARTIAL"}:
            status = "MISSING"
        elif month_reviews or import_status == "LEGACY_DATA":
            status = "REVIEW"
        else:
            status = "COMPLETE"

        if import_status in {"COMPLETE", "LEGACY_DATA"}:
            latest_data_month = month
        if status == "COMPLETE":
            latest_complete_month = month

        primary_action = None
        if status == "MISSING":
            primary_action = {"page": "imports", "view": "month", "entity_id": month}
        elif month_reviews:
            primary_action = month_reviews[0]["action_target"]

        months.append(
            {
                "month": month,
                "status": status,
                "import_status": import_status,
                "expected_account_count": len(expected),
                "completed_account_count": len(completed & expected),
                "expected_account_ids": sorted(expected),
                "completed_account_ids": sorted(completed & expected),
                "open_review_count": len(month_reviews),
                "review_counts": {
                    review_type: counts[review_type] for review_type in REVIEW_TYPES
                },
                "primary_action": primary_action,
            }
        )

    return {
        "as_of": as_of_date.isoformat(),
        "current_month": current_month,
        "earliest_month": earliest_month,
        "latest_data_month": latest_data_month,
        "latest_complete_month": latest_complete_month,
        "unscoped_review_count": unscoped_reviews,
        "months": months,
    }


__all__ = ["monthly_workspace_status"]
