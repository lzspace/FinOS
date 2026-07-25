"""Central, typed period selection shared by every period-aware query."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .store import LocalFinanceStore

PERIOD_MODES = ("MONTH", "YEAR", "CUSTOM_RANGE")
RESERVED_PERIOD_MODES = ("QUARTER", "ALL_TIME")
DEFAULT_TIMEZONE = "Europe/Berlin"
MAX_CUSTOM_RANGE_DAY_AGGREGATION = 92
MAX_CUSTOM_RANGE_WEEK_AGGREGATION = 183
MAX_CUSTOM_RANGE_TOTAL_DAYS = 1096

GERMAN_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


class PeriodSelectionError(ValueError):
    pass


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def resolve_period(
    mode: str,
    *,
    year: int | None = None,
    month: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    if mode not in PERIOD_MODES:
        raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID")
    if mode == "MONTH":
        if not isinstance(year, int) or not isinstance(month, int) or not 1 <= month <= 12:
            raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID")
        start, end = _month_bounds(year, month)
        display_label = f"{GERMAN_MONTHS[month - 1]} {year}"
        aggregation = "DAY"
    elif mode == "YEAR":
        if not isinstance(year, int):
            raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID")
        start, end = date(year, 1, 1), date(year, 12, 31)
        display_label = str(year)
        aggregation = "MONTH"
    else:
        if not start_date or not end_date:
            raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID")
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        except ValueError as exc:
            raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID") from exc
        if end < start:
            raise PeriodSelectionError("FINANCE_PERIOD_SELECTION_INVALID")
        span_days = (end - start).days + 1
        if span_days > MAX_CUSTOM_RANGE_TOTAL_DAYS:
            raise PeriodSelectionError("FINANCE_PERIOD_RANGE_TOO_LARGE")
        if span_days <= MAX_CUSTOM_RANGE_DAY_AGGREGATION:
            aggregation = "DAY"
        elif span_days <= MAX_CUSTOM_RANGE_WEEK_AGGREGATION:
            aggregation = "WEEK"
        else:
            aggregation = "MONTH"
        display_label = f"{start.isoformat()} – {end.isoformat()}"
    return {
        "mode": mode,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": timezone,
        "display_label": display_label,
        "aggregation": aggregation,
        "comparison_period": None,
    }


def resolve_period_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("period") if isinstance(payload.get("period"), dict) else payload
    mode = source.get("mode", "MONTH")
    return resolve_period(
        mode,
        year=source.get("year"),
        month=source.get("month"),
        start_date=source.get("start_date"),
        end_date=source.get("end_date"),
        timezone=source.get("timezone", DEFAULT_TIMEZONE),
    )


def default_month_period() -> dict[str, Any]:
    today = date.today()
    return resolve_period("MONTH", year=today.year, month=today.month)


def available_periods(store: LocalFinanceStore) -> dict[str, Any]:
    known_dates: list[str] = []
    for event in store.events("TransactionNormalized"):
        known_dates.append(event["payload"]["booking_date"])
    for event in store.events("ImportFileAnalyzed"):
        period_start = event["payload"].get("period_start")
        if period_start:
            known_dates.append(period_start)
    if not known_dates:
        today = date.today().isoformat()
        known_dates = [today]
    earliest, latest = min(known_dates), max(known_dates)
    earliest_d, latest_d = date.fromisoformat(earliest), date.fromisoformat(latest)
    months: list[str] = []
    cursor = date(earliest_d.year, earliest_d.month, 1)
    end_cursor = date(latest_d.year, latest_d.month, 1)
    while cursor <= end_cursor:
        months.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    years = sorted({int(item[:4]) for item in months})
    return {
        "earliest_date": earliest,
        "latest_date": latest,
        "available_months": months,
        "available_years": years,
        "max_custom_range_days": MAX_CUSTOM_RANGE_TOTAL_DAYS,
        "supported_modes": list(PERIOD_MODES),
        "reserved_modes": list(RESERVED_PERIOD_MODES),
    }
