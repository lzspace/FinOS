from __future__ import annotations
from decimal import Decimal
from .store import LocalFinanceStore


def monthly_cashflow(store: LocalFinanceStore, month: str) -> dict[str, object]:
    income = expenses = Decimal("0")
    count = 0
    last = 0
    for event in store.events("TransactionNormalized"):
        if event["payload"]["booking_date"].startswith(month):
            amount = Decimal(event["payload"]["amount"])
            income += max(amount, Decimal("0"))
            expenses += max(-amount, Decimal("0"))
            count += 1
            last = event["sequence_number"]
    return {
        "period": month,
        "total_income": income,
        "total_expenses": expenses,
        "net_cashflow": income - expenses,
        "transaction_count": count,
        "last_event_sequence": last,
    }
