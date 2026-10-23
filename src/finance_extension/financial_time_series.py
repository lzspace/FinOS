"""Rebuildable, reconciliation-aware cashflow time series."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .reconciliation import reconciled_transactions
from .store import LocalFinanceStore


def _latest_refunds(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"].startswith("RefundRelation"):
            latest[event["aggregate_id"]] = event
    return latest


def _bucket_ranges(period: dict[str, Any]) -> list[tuple[date, date]]:
    start = date.fromisoformat(period["start_date"])
    end = date.fromisoformat(period["end_date"])
    aggregation = period["aggregation"]
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if aggregation == "DAY":
            bucket_end = cursor
            next_cursor = cursor + timedelta(days=1)
        elif aggregation == "WEEK":
            bucket_end = min(cursor + timedelta(days=6), end)
            next_cursor = bucket_end + timedelta(days=1)
        else:
            if cursor.month == 12:
                next_month = date(cursor.year + 1, 1, 1)
            else:
                next_month = date(cursor.year, cursor.month + 1, 1)
            bucket_end = min(next_month - timedelta(days=1), end)
            next_cursor = next_month
        ranges.append((cursor, bucket_end))
        cursor = next_cursor
    return ranges


def financial_time_series(
    store: LocalFinanceStore,
    period: dict[str, Any],
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Return cashflow buckets without inventing zeroes for uncovered intervals."""

    start = period["start_date"]
    end = period["end_date"]
    transactions = {
        event["payload"]["transaction_id"]: event["payload"]
        for event in store.events("TransactionNormalized")
    }
    reconciled = reconciled_transactions(store)
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "income": Decimal("0"),
            "expenses": Decimal("0"),
            "transaction_count": 0,
        }
    )

    for transaction_id, transaction in transactions.items():
        if account_id is not None and transaction["account_id"] != account_id:
            continue
        booking_date = transaction["booking_date"]
        if not start <= booking_date <= end:
            continue
        values = daily[booking_date]
        values["transaction_count"] += 1
        view = reconciled[transaction_id]
        if (
            view["duplicate_status"] == "CONFIRMED"
            or view["transfer_status"] == "CONFIRMED"
            or view["investment_funding_status"] == "CONFIRMED"
            or view["refund_status"] == "REFUND"
        ):
            continue
        amount = Decimal(transaction["amount"])
        values["income"] += max(amount, Decimal("0"))
        values["expenses"] += max(-amount, Decimal("0"))

    for event in _latest_refunds(store).values():
        if event["payload"].get("status") != "CONFIRMED":
            continue
        refund = transactions.get(event["payload"]["refund_transaction_id"])
        if (
            refund
            and (account_id is None or refund["account_id"] == account_id)
            and start <= refund["booking_date"] <= end
        ):
            daily[refund["booking_date"]]["expenses"] -= Decimal(
                event["payload"]["confirmed_amount"]
            )

    points: list[dict[str, Any]] = []
    total_income = total_expenses = Decimal("0")
    covered_buckets = 0
    for bucket_start, bucket_end in _bucket_ranges(period):
        dates = [
            key
            for key in daily
            if bucket_start.isoformat() <= key <= bucket_end.isoformat()
        ]
        has_data = bool(dates)
        if has_data:
            income = sum((daily[key]["income"] for key in dates), Decimal("0"))
            expenses = sum((daily[key]["expenses"] for key in dates), Decimal("0"))
            transaction_count = sum(daily[key]["transaction_count"] for key in dates)
            total_income += income
            total_expenses += expenses
            covered_buckets += 1
        else:
            income = expenses = None
            transaction_count = 0
        points.append(
            {
                "bucket_start": bucket_start.isoformat(),
                "bucket_end": bucket_end.isoformat(),
                "income": str(income) if income is not None else None,
                "expenses": str(expenses) if expenses is not None else None,
                "net_cashflow": str(income - expenses) if income is not None else None,
                "transaction_count": transaction_count,
                "has_data": has_data,
            }
        )

    has_data = covered_buckets > 0
    return {
        "period": period,
        "aggregation": period["aggregation"],
        "points": points,
        "summary": {
            "has_data": has_data,
            "effective_income": str(total_income) if has_data else None,
            "effective_expenses": str(total_expenses) if has_data else None,
            "net_cashflow": str(total_income - total_expenses) if has_data else None,
            "covered_bucket_count": covered_buckets,
            "missing_bucket_count": len(points) - covered_buckets,
        },
    }
