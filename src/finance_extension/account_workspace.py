"""Period-scoped account workspace projections for the 1.4.0 account UI.

Every function here is a pure, synchronous read over the event store — there
is no persisted projection state, so results always reflect the full event
history at call time (see ``application.py``'s response envelope for the
resulting freshness semantics).
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .accounts import (
    account_balance_history,
    account_overview,
    account_reviews,
    accounts,
    balance_reconciliations,
)
from .multi_account_import import (
    PARSER_VERSION,
    PROFILE_VERSION,
    analyses,
    balance_difference_documentations,
    closing_security_positions,
    imported_period_reconciliations,
    opening_security_positions,
    security_positions,
    security_transactions,
)
from .reconciliation import reconciled_transactions
from .store import LocalFinanceStore

OVERVIEW_STATUSES = (
    "MATCHED",
    "DIFFERENCE",
    "STALE",
    "MISSING_BALANCE",
    "REVIEW_REQUIRED",
    "CLOSED",
)

ACCOUNT_AUDIT_EVENT_TYPES = {
    "AccountCreated",
    "AccountUpdated",
    "AccountClosed",
    "BalanceSnapshotRecorded",
    "BalanceSnapshotCorrected",
    "AccountBalanceReconciled",
    "OpeningBalanceRecorded",
    "ClosingBalanceRecorded",
    "OpeningBalanceCarryForwardAdjusted",
    "OpeningSecurityPositionRecorded",
    "ClosingSecurityPositionRecorded",
    "EmptyOpeningSecurityPositionsConfirmed",
    "SecurityPositionSnapshotCorrected",
    "ImportSectionCompleted",
    "EmptyImportSectionProcessed",
    "ImportedPeriodBalanceReconciled",
    "BalanceDifferenceDocumented",
    "ImportedSecurityPositionsReconciled",
    "InvestmentFundingRelationProposed",
    "InvestmentFundingRelationConfirmed",
    "InvestmentFundingRelationRejected",
    "InvestmentFundingRelationBroken",
}


def _in_period(value: str | None, period: dict[str, Any] | None) -> bool:
    if period is None or not value:
        return True
    return period["start_date"] <= value <= period["end_date"]


def _in_period_with_carry_forward_tolerance(value: str | None, period: dict[str, Any] | None) -> bool:
    """Like ``_in_period`` but also matches the day an opening balance is carried
    forward from (the previous period's last day), so a period's opening entry
    remains visible even though it is dated one day before the period starts."""

    if period is None or not value:
        return True
    try:
        value_date = date.fromisoformat(value)
        start = date.fromisoformat(period["start_date"]) - timedelta(days=1)
        end = date.fromisoformat(period["end_date"])
    except ValueError:
        return _in_period(value, period)
    return start <= value_date <= end


def account_review_counts(store: LocalFinanceStore) -> dict[str, int]:
    counts: dict[str, int] = {}
    for review in account_reviews(store):
        account_id = review.get("account_id")
        if account_id:
            counts[account_id] = counts.get(account_id, 0) + 1
    return counts


def account_last_import(store: LocalFinanceStore, account_id: str) -> dict[str, Any] | None:
    runs = [
        {**event["payload"], "occurred_at": event["occurred_at"]}
        for event in store.events("ImportSectionCompleted")
        if event["payload"]["account_id"] == account_id
    ]
    if not runs:
        return None
    latest = max(runs, key=lambda item: (item["period_start"] or "", item["occurred_at"]))
    return {
        "report_month": (latest["period_start"] or "")[:7],
        "status": latest["status"],
        "export_id": latest["export_id"],
    }


def derive_overview_status(row: dict[str, Any]) -> str:
    if row["status"] == "CLOSED":
        return "CLOSED"
    if row["latest_balance"] is None:
        return "MISSING_BALANCE"
    if row["freshness"] == "STALE":
        return "STALE"
    reconciliation_status = row["reconciliation_status"]
    if reconciliation_status == "MATCHED":
        return "MATCHED"
    if reconciliation_status == "REVIEW_REQUIRED":
        return "DIFFERENCE"
    return "REVIEW_REQUIRED"


def _period_cashflow(store: LocalFinanceStore, account_id: str, period: dict[str, Any]) -> dict[str, str]:
    projected = reconciled_transactions(store)
    income = expenses = Decimal("0")
    for event in store.events("TransactionNormalized"):
        item = event["payload"]
        if item["account_id"] != account_id or not _in_period(item["booking_date"], period):
            continue
        view = projected[item["transaction_id"]]
        if view["duplicate_status"] == "CONFIRMED":
            continue
        if (
            view["transfer_status"] == "CONFIRMED"
            or view["investment_funding_status"] == "CONFIRMED"
            or view["refund_status"] == "REFUND"
        ):
            continue
        amount = Decimal(item["amount"])
        income += max(amount, Decimal("0"))
        expenses += max(-amount, Decimal("0"))
    return {
        "period_income": str(income),
        "period_expenses": str(expenses),
        "period_net_cashflow": str(income - expenses),
    }


def account_overview_for_period(
    store: LocalFinanceStore,
    period: dict[str, Any],
    *,
    account_types: list[str] | None = None,
    status: str | None = None,
    currency: str | None = None,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    base_rows = account_overview(store, period["end_date"])
    review_counts = account_review_counts(store)
    rows: list[dict[str, Any]] = []
    for base in base_rows:
        if base["status"] == "CLOSED" and not include_closed:
            continue
        if account_types and base["account_type"] not in account_types:
            continue
        if currency and base["currency"] != currency:
            continue
        cashflow = _period_cashflow(store, base["account_id"], period)
        last_import = account_last_import(store, base["account_id"])
        row = {
            "account_id": base["account_id"],
            "display_name": base["display_name"],
            "account_type": base["account_type"],
            "institution": base["institution"],
            "currency": base["currency"],
            "status": base["status"],
            "reported_balance": base["latest_balance"],
            "reported_balance_date": base["balance_date"],
            "calculated_balance": base["latest_balance"],
            "calculated_balance_date": base["balance_date"],
            "balance_difference": None,
            "reconciliation_status": base["reconciliation_status"],
            **cashflow,
            "last_import_month": last_import["report_month"] if last_import else None,
            "last_import_status": last_import["status"] if last_import else None,
            "open_review_count": review_counts.get(base["account_id"], 0),
            "data_freshness": base["freshness"],
            "position_count": None,
            "acquisition_value": None,
            "open_investment_funding_relations": 0,
            "projection_sequence": None,
            "projection_version": "1.0.0",
        }
        reconciliation = balance_reconciliations(store).get(base["account_id"])
        if reconciliation:
            row["calculated_balance"] = reconciliation.get("calculated_balance")
            row["balance_difference"] = reconciliation.get("balance_difference")
        if base["account_type"] == "BROKERAGE":
            positions = [
                item for item in security_positions(store).values() if item["account_id"] == base["account_id"]
            ]
            row["position_count"] = len(positions)
            opening = [
                item
                for item in opening_security_positions(store).values()
                if item["account_id"] == base["account_id"]
            ]
            row["acquisition_value"] = str(
                sum((Decimal(item["market_value"]) for item in opening if item.get("market_value")), Decimal("0"))
            ) if opening else None
        row["overview_status"] = derive_overview_status(base)
        if status and row["overview_status"] != status:
            continue
        rows.append(row)
    return sorted(rows, key=lambda item: (item["status"] != "ACTIVE", item["display_name"]))


def account_period_summary(store: LocalFinanceStore, account_id: str, period: dict[str, Any]) -> dict[str, Any]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    cashflow = _period_cashflow(store, account_id, period)
    transaction_count = sum(
        1
        for event in store.events("TransactionNormalized")
        if event["payload"]["account_id"] == account_id and _in_period(event["payload"]["booking_date"], period)
    )
    balances = [
        item
        for item in account_balance_history(store, account_id)
        if _in_period(item.get("balance_date"), period)
    ]
    return {
        "account_id": account_id,
        "period": period,
        **cashflow,
        "transaction_count": transaction_count,
        "balance_point_count": len(balances),
    }


def account_transactions_for_period(
    store: LocalFinanceStore,
    account_id: str,
    period: dict[str, Any] | None,
    *,
    category_code: str | None = None,
    direction: str | None = None,
    import_month: str | None = None,
) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    projected = reconciled_transactions(store)
    rows: list[dict[str, Any]] = []
    for event in store.events("TransactionNormalized"):
        item = event["payload"]
        if item["account_id"] != account_id or not _in_period(item["booking_date"], period):
            continue
        if import_month and item.get("export_id"):
            export_month = (analyses(store).get(item["export_id"], {}).get("report_month"))
            if export_month != import_month:
                continue
        direction_value = "CREDIT" if Decimal(item["amount"]) >= 0 else "DEBIT"
        if direction and direction != direction_value:
            continue
        view = projected[item["transaction_id"]]
        rows.append(
            {
                **item,
                "direction": direction_value,
                "duplicate_status": view["duplicate_status"],
                "transfer_status": view["transfer_status"],
                "refund_status": view["refund_status"],
                "investment_funding_status": view["investment_funding_status"],
            }
        )
    if category_code:
        rows = [row for row in rows if row.get("category_code") == category_code]
    return sorted(rows, key=lambda item: item["booking_date"])


def account_balance_ledger(
    store: LocalFinanceStore, account_id: str, period: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    rows: list[dict[str, Any]] = []
    for event in store.events():
        payload = event["payload"]
        if payload.get("account_id") != account_id:
            continue
        event_type = event["event_type"]
        if event_type == "OpeningBalanceRecorded":
            rows.append(
                {
                    "balance_type": "OPENING",
                    "balance_date": payload["balance_date"],
                    "reported_value": payload["booked_balance"],
                    "calculated_value": payload.get("suggested_value"),
                    "currency": payload["currency"],
                    "source": payload["source"],
                    "confirmation_status": "CONFIRMED" if payload["confirmation"] else "UNCONFIRMED",
                    "correction_status": (
                        "ADJUSTED" if payload.get("adjustment_reason") else "AS_SUGGESTED"
                        if payload.get("suggested_value") else "MANUAL"
                    ),
                    "adjustment_reason": payload.get("adjustment_reason"),
                    "carry_forward_source_reconciliation_id": payload.get(
                        "carry_forward_source_reconciliation_id"
                    ),
                    "sequence_number": event["sequence_number"],
                }
            )
        elif event_type == "ClosingBalanceRecorded":
            rows.append(
                {
                    "balance_type": "CLOSING",
                    "balance_date": payload["balance_date"],
                    "reported_value": payload["booked_balance"],
                    "calculated_value": None,
                    "currency": payload["currency"],
                    "source": payload["source"],
                    "confirmation_status": "CONFIRMED" if payload["confirmation"] else "UNCONFIRMED",
                    "correction_status": "MANUAL",
                    "adjustment_reason": None,
                    "carry_forward_source_reconciliation_id": None,
                    "sequence_number": event["sequence_number"],
                }
            )
        elif event_type in ("BalanceSnapshotRecorded", "BalanceSnapshotCorrected"):
            rows.append(
                {
                    "balance_type": "CALCULATED" if payload["source"] == "CALCULATED" else "INTERMEDIATE",
                    "balance_date": payload["balance_date"],
                    "reported_value": payload["booked_balance"],
                    "calculated_value": payload["booked_balance"] if payload["source"] == "CALCULATED" else None,
                    "currency": payload["currency"],
                    "source": payload["source"],
                    "confirmation_status": "CONFIRMED",
                    "correction_status": "CORRECTED" if event_type == "BalanceSnapshotCorrected" else "AS_RECORDED",
                    "adjustment_reason": payload.get("correction_reason"),
                    "carry_forward_source_reconciliation_id": None,
                    "sequence_number": event["sequence_number"],
                }
            )
        elif event_type == "AccountBalanceReconciled":
            rows.append(
                {
                    "balance_type": "CALCULATED",
                    "balance_date": payload["balance_date"],
                    "reported_value": payload["reported_balance"],
                    "calculated_value": payload["calculated_balance"],
                    "currency": payload["currency"],
                    "source": "RECONCILED",
                    "confirmation_status": "CONFIRMED",
                    "correction_status": payload["status"],
                    "adjustment_reason": None,
                    "carry_forward_source_reconciliation_id": None,
                    "sequence_number": event["sequence_number"],
                }
            )
    if period is not None:
        rows = [
            row for row in rows if _in_period_with_carry_forward_tolerance(row["balance_date"], period)
        ]
    return sorted(rows, key=lambda item: (item["balance_date"] or "", item["sequence_number"]))


def account_reconciliations_for_period(
    store: LocalFinanceStore, account_id: str, period: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    explanations = {
        item["reconciliation_id"]: item for item in balance_difference_documentations(store).values()
    }
    rows: list[dict[str, Any]] = []
    for reconciliation in imported_period_reconciliations(store).values():
        if reconciliation["account_id"] != account_id:
            continue
        if period is not None and not _in_period(reconciliation["period_start"], period):
            continue
        explanation = explanations.get(reconciliation["reconciliation_id"])
        raw_status = reconciliation["status"]
        if raw_status == "DIFFERENCE":
            display_status = "DIFFERENCE" if explanation else "REVIEW_REQUIRED"
        else:
            display_status = raw_status
        rows.append(
            {
                "reconciliation_id": reconciliation["reconciliation_id"],
                "account_id": account_id,
                "report_month": reconciliation["period_start"][:7],
                "period_start": reconciliation["period_start"],
                "period_end": reconciliation["period_end"],
                "opening_balance": reconciliation["opening_balance"],
                "relevant_transaction_count": reconciliation["relevant_transaction_count"],
                "calculated_closing_balance": reconciliation["calculated_closing_balance"],
                "reported_closing_balance": reconciliation["reported_closing_balance"],
                "balance_difference": reconciliation["balance_difference"],
                "status": display_status,
                "raw_status": raw_status,
                "explanation": explanation["explanation"] if explanation else None,
                "export_id": reconciliation["export_id"],
            }
        )
    return sorted(rows, key=lambda item: item["period_start"], reverse=True)


def account_imports(store: LocalFinanceStore, account_id: str) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    all_analyses = analyses(store)
    rows: list[dict[str, Any]] = []
    for event in store.events("ImportSectionCompleted"):
        payload = event["payload"]
        if payload["account_id"] != account_id:
            continue
        analysis = all_analyses.get(payload["export_id"], {})
        rows.append(
            {
                "export_id": payload["export_id"],
                "section_id": payload["section_id"],
                "section_type": payload["section_type"],
                "bank_identifier": analysis.get("bank_identifier"),
                "report_month": (payload["period_start"] or "")[:7],
                "period_start": payload["period_start"],
                "period_end": payload["period_end"],
                "import_status": payload["status"],
                "record_count": payload["record_count"],
                "parser_version": PARSER_VERSION,
                "profile_version": analysis.get("profile_version", PROFILE_VERSION),
                "content_hash": payload["content_hash"],
                "imported_at": event["occurred_at"],
                "sequence_number": event["sequence_number"],
            }
        )
    return sorted(rows, key=lambda item: (item["period_start"] or "", item["sequence_number"]), reverse=True)


def account_positions(store: LocalFinanceStore, account_id: str) -> list[dict[str, Any]]:
    account = accounts(store).get(account_id)
    if not account:
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    if account["account_type"] != "BROKERAGE":
        return []
    opening = {
        key: value for key, value in opening_security_positions(store).items() if value["account_id"] == account_id
    }
    current = {
        key: value for key, value in security_positions(store).items() if value["account_id"] == account_id
    }
    closing = {
        key: value for key, value in closing_security_positions(store).items() if value["account_id"] == account_id
    }
    transactions = [item for item in security_transactions(store).values() if item["account_id"] == account_id]
    closing_position_ids = {
        "position_" + hashlib.sha256(f"{account_id}:{item['security_identifier']}".encode()).hexdigest()[:24]: item
        for item in closing.values()
    }
    position_ids = set(opening) | set(current) | set(closing_position_ids)
    rows: list[dict[str, Any]] = []
    for position_id in position_ids:
        open_row = opening.get(position_id)
        current_row = current.get(position_id)
        reported_closing = closing_position_ids.get(position_id)
        reference = open_row or current_row or reported_closing
        identifier = reference["security_identifier"]
        purchases = sum(
            (
                abs(Decimal(item["quantity"]))
                for item in transactions
                if item["security_identifier"] == identifier and item["transaction_type"] == "INVESTMENT_PURCHASE"
            ),
            Decimal("0"),
        )
        sales = sum(
            (
                abs(Decimal(item["quantity"]))
                for item in transactions
                if item["security_identifier"] == identifier and item["transaction_type"] == "INVESTMENT_SALE"
            ),
            Decimal("0"),
        )
        opening_quantity = Decimal(open_row["quantity"]) if open_row else Decimal("0")
        closing_quantity = (
            Decimal(current_row["quantity"]) if current_row else opening_quantity + purchases - sales
        )
        rows.append(
            {
                "position_id": position_id,
                "account_id": account_id,
                "security_identifier_type": reference["security_identifier_type"],
                "security_identifier": identifier,
                "security_name": reference["security_name"],
                "opening_quantity": str(opening_quantity),
                "purchased_quantity": str(purchases),
                "sold_quantity": str(sales),
                "closing_quantity": str(closing_quantity),
                "reported_closing_quantity": reported_closing["quantity"] if reported_closing else None,
                "position_difference": (
                    str(closing_quantity - Decimal(reported_closing["quantity"])) if reported_closing else None
                ),
                "market_value": (current_row or open_row or {}).get("market_value"),
                "acquisition_value": open_row.get("market_value") if open_row else None,
                "currency": (current_row or open_row or reported_closing or {}).get("price_currency"),
            }
        )
    return sorted(rows, key=lambda item: item["security_name"] or "")


def account_position_history(
    store: LocalFinanceStore, account_id: str, period: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    rows = [item for item in security_transactions(store).values() if item["account_id"] == account_id]
    if period is not None:
        rows = [item for item in rows if _in_period(item["booking_date"], period)]
    return sorted(rows, key=lambda item: item["booking_date"])


def account_audit_trail(
    store: LocalFinanceStore, account_id: str, period: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if account_id not in accounts(store):
        raise ValueError("FINANCE_ACCOUNT_NOT_FOUND")
    rows: list[dict[str, Any]] = []
    for event in store.events():
        if event["event_type"] not in ACCOUNT_AUDIT_EVENT_TYPES:
            continue
        payload = event["payload"]
        if payload.get("account_id") != account_id:
            continue
        if period is not None and not _in_period(event["occurred_at"][:10], period):
            continue
        rows.append(
            {
                "sequence_number": event["sequence_number"],
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "occurred_at": event["occurred_at"],
                "aggregate_type": event["aggregate_type"],
                "aggregate_id": event["aggregate_id"],
                "account_id": account_id,
                "related_import_id": payload.get("export_id") or payload.get("import_batch_id"),
                "related_balance_id": (
                    payload.get("snapshot_id") or payload.get("reconciliation_id") or payload.get("balance_id")
                ),
                "payload": payload,
            }
        )
    return sorted(rows, key=lambda item: item["sequence_number"])
