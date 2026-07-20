"""Deterministic recurring-payment detection and monthly forecasting for 0.5.0."""

from __future__ import annotations

import calendar
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Any

from .classification import active_classifications
from .reconciliation import reconciled_monthly_cashflow, reconciled_transactions
from .store import LocalFinanceStore, StoreInvariantError

DETECTION_POLICY_VERSION = "recurring@1.0.0"
FORECAST_POLICY_VERSION = "forecast@1.0.0"
FREQUENCIES = (
    "WEEKLY",
    "BIWEEKLY",
    "MONTHLY",
    "BIMONTHLY",
    "QUARTERLY",
    "SEMI_ANNUALLY",
    "ANNUALLY",
    "IRREGULAR",
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode()).hexdigest()[:32]}"


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
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": command_id,
        "causation_id": command_id,
        "payload": payload,
    }


def _latest(store: LocalFinanceStore, prefix: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["event_type"].startswith(prefix):
            result[event["aggregate_id"]] = event
    return result


def _transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["payload"]["transaction_id"]: {**event["payload"], "event_id": event["event_id"]}
        for event in store.events("TransactionNormalized")
    }


def _month_bounds(month: str) -> tuple[date, date]:
    year, month_number = (int(part) for part in month.split("-"))
    return date(year, month_number, 1), date(
        year, month_number, calendar.monthrange(year, month_number)[1]
    )


def _add_months(value: date, count: int, day: int | None = None) -> date:
    month_index = value.year * 12 + value.month - 1 + count
    year, month = divmod(month_index, 12)
    month += 1
    target_day = day or value.day
    return date(year, month, min(target_day, calendar.monthrange(year, month)[1]))


def _frequency(interval: int) -> str:
    if 6 <= interval <= 8:
        return "WEEKLY"
    if 12 <= interval <= 16:
        return "BIWEEKLY"
    if 26 <= interval <= 35:
        return "MONTHLY"
    if 54 <= interval <= 70:
        return "BIMONTHLY"
    if 80 <= interval <= 100:
        return "QUARTERLY"
    if 165 <= interval <= 200:
        return "SEMI_ANNUALLY"
    if 340 <= interval <= 390:
        return "ANNUALLY"
    return "IRREGULAR"


def _next_date(last: date, frequency: str, expected_day: int) -> date:
    days = {"WEEKLY": 7, "BIWEEKLY": 14}
    months = {
        "MONTHLY": 1,
        "BIMONTHLY": 2,
        "QUARTERLY": 3,
        "SEMI_ANNUALLY": 6,
        "ANNUALLY": 12,
    }
    if frequency in days:
        return last + timedelta(days=days[frequency])
    if frequency in months:
        return _add_months(last, months[frequency], expected_day)
    return last


def recurring_patterns(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        pattern_id: event["payload"]
        for pattern_id, event in _latest(store, "RecurringPattern").items()
    }


def detect_recurring_patterns(
    store: LocalFinanceStore, from_month: str, to_month: str, minimum_occurrences: int = 3
) -> int:
    period_start, _ = _month_bounds(from_month)
    _, period_end = _month_bounds(to_month)
    transactions = _transactions(store)
    reconciled = reconciled_transactions(store)
    classifications = active_classifications(store)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for transaction_id, transaction in transactions.items():
        booking_date = date.fromisoformat(transaction["booking_date"])
        view = reconciled[transaction_id]
        if (
            not period_start <= booking_date <= period_end
            or not view["cashflow_relevant"]
            or view["effective_amount"] == 0
        ):
            continue
        classification = classifications.get(transaction_id)
        category = (
            classification["payload"]["category_code"]
            if classification
            and classification["event_type"] == "TransactionClassificationConfirmed"
            else "UNCLASSIFIED"
        )
        merchant_key = " ".join(transaction["counterparty"].casefold().split())
        direction = "INCOME" if Decimal(transaction["amount"]) > 0 else "EXPENSE"
        groups[(transaction["account_id"], merchant_key, direction, category)].append(transaction)

    existing = recurring_patterns(store)
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for (account_id, merchant_key, direction, category), observations in groups.items():
        if len(observations) < minimum_occurrences:
            continue
        observations.sort(key=lambda item: item["booking_date"])
        dates = [date.fromisoformat(item["booking_date"]) for item in observations]
        intervals = [(right - left).days for left, right in zip(dates, dates[1:], strict=False)]
        frequency = _frequency(round(median(intervals)))
        amounts = [abs(Decimal(item["amount"])) for item in observations]
        expected_amount = (sum(amounts) / len(amounts)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        tolerance = max((abs(amount - expected_amount) for amount in amounts), default=Decimal("0"))
        tolerance = max(tolerance, Decimal("0.01")).quantize(Decimal("0.01"))
        expected_day = round(median([value.day for value in dates]))
        pattern_id = _stable_id("pattern", account_id, merchant_key, direction, category)
        source_ids = [item["transaction_id"] for item in observations]
        fingerprint = hashlib.sha256(
            json.dumps(source_ids, separators=(",", ":")).encode()
        ).hexdigest()
        current = existing.get(pattern_id)
        if current and current["status"] in {"ACTIVE", "PAUSED", "ENDED"}:
            continue
        if current and current.get("source_fingerprint") == fingerprint:
            continue
        confidence = "HIGH" if frequency != "IRREGULAR" and len(observations) >= 3 else "LOW"
        payload = {
            "pattern_id": pattern_id,
            "account_id": account_id,
            "merchant_key": merchant_key,
            "direction": direction,
            "category_code": category,
            "frequency": frequency,
            "expected_amount": str(expected_amount),
            "amount_tolerance": str(tolerance),
            "expected_day_from": max(1, min(value.day for value in dates) - 1),
            "expected_day_to": min(31, max(value.day for value in dates) + 1),
            "first_observed_date": dates[0].isoformat(),
            "last_observed_date": dates[-1].isoformat(),
            "next_expected_date": _next_date(dates[-1], frequency, expected_day).isoformat(),
            "confidence": confidence,
            "status": "PROPOSED",
            "source_transaction_ids": source_ids,
            "source_fingerprint": fingerprint,
            "detection_policy_version": DETECTION_POLICY_VERSION,
            "version": store.next_aggregate_version("RecurringPattern", pattern_id),
        }
        events.append(
            _event(
                "RecurringPatternProposed",
                "RecurringPattern",
                pattern_id,
                payload["version"],
                command_id,
                payload,
            )
        )
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "DetectRecurringPatterns",
                "idempotency_key": f"detect-recurring:{from_month}:{to_month}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def _change_pattern(
    store: LocalFinanceStore,
    pattern_id: str,
    event_type: str,
    status: str,
    command_type: str,
    changes: dict[str, Any] | None = None,
) -> int:
    current = recurring_patterns(store).get(pattern_id)
    if not current:
        raise StoreInvariantError("FINANCE_RECURRING_PATTERN_NOT_FOUND")
    if current["status"] == status and not changes:
        return 0
    allowed = {
        "RecurringPatternConfirmed": {"PROPOSED"},
        "RecurringPatternRejected": {"PROPOSED"},
        "RecurringPatternUpdated": {"ACTIVE", "PAUSED"},
        "RecurringPatternPaused": {"ACTIVE"},
        "RecurringPatternEnded": {"ACTIVE", "PAUSED"},
    }
    if current["status"] not in allowed[event_type]:
        raise StoreInvariantError("FINANCE_RECURRING_PATTERN_TRANSITION_INVALID")
    version = store.next_aggregate_version("RecurringPattern", pattern_id)
    payload = {**current, **(changes or {}), "status": status, "version": version}
    command_id = _id("cmd")
    store.append_events(
        {
            "command_id": command_id,
            "command_type": command_type,
            "idempotency_key": f"{command_type}:{pattern_id}:{version}",
        },
        [_event(event_type, "RecurringPattern", pattern_id, version, command_id, payload)],
    )
    return 1


def confirm_recurring_pattern(store: LocalFinanceStore, pattern_id: str) -> int:
    return _change_pattern(
        store, pattern_id, "RecurringPatternConfirmed", "ACTIVE", "ConfirmRecurringPattern"
    )


def reject_recurring_pattern(store: LocalFinanceStore, pattern_id: str) -> int:
    return _change_pattern(
        store, pattern_id, "RecurringPatternRejected", "REJECTED", "RejectRecurringPattern"
    )


def update_recurring_pattern(
    store: LocalFinanceStore,
    pattern_id: str,
    *,
    amount: str,
    day_from: int,
    day_to: int,
) -> int:
    value = Decimal(amount)
    if value <= 0 or not 1 <= day_from <= day_to <= 31:
        raise StoreInvariantError("FINANCE_RECURRING_PATTERN_UPDATE_INVALID")
    current = recurring_patterns(store).get(pattern_id)
    if not current:
        raise StoreInvariantError("FINANCE_RECURRING_PATTERN_NOT_FOUND")
    if (
        Decimal(current["expected_amount"]) == value
        and current["expected_day_from"] == day_from
        and current["expected_day_to"] == day_to
    ):
        return 0
    return _change_pattern(
        store,
        pattern_id,
        "RecurringPatternUpdated",
        recurring_patterns(store)[pattern_id]["status"],
        "UpdateRecurringPattern",
        {"expected_amount": str(value), "expected_day_from": day_from, "expected_day_to": day_to},
    )


def pause_recurring_pattern(store: LocalFinanceStore, pattern_id: str) -> int:
    return _change_pattern(
        store, pattern_id, "RecurringPatternPaused", "PAUSED", "PauseRecurringPattern"
    )


def end_recurring_pattern(store: LocalFinanceStore, pattern_id: str) -> int:
    return _change_pattern(
        store, pattern_id, "RecurringPatternEnded", "ENDED", "EndRecurringPattern"
    )


def expected_transactions(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        expected_id: event["payload"]
        for expected_id, event in _latest(store, "ExpectedTransaction").items()
    }


def _advance_expected(value: date, frequency: str, expected_day: int) -> date:
    return _next_date(value, frequency, expected_day)


def generate_expected_transactions(store: LocalFinanceStore, month: str) -> int:
    start, end = _month_bounds(month)
    existing = expected_transactions(store)
    transactions = _transactions(store)
    reconciled = reconciled_transactions(store)
    matched_transaction_ids = {
        item["matched_transaction_id"]
        for item in existing.values()
        if item.get("matched_transaction_id")
    }
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for pattern_id, pattern in recurring_patterns(store).items():
        if pattern["status"] != "ACTIVE" or pattern["frequency"] == "IRREGULAR":
            continue
        expected_date = date.fromisoformat(pattern["next_expected_date"])
        expected_day = round((pattern["expected_day_from"] + pattern["expected_day_to"]) / 2)
        while expected_date < start:
            expected_date = _advance_expected(expected_date, pattern["frequency"], expected_day)
        while expected_date <= end:
            expected_id = _stable_id("expected", pattern_id, expected_date.isoformat())
            if expected_id not in existing:
                amount = Decimal(pattern["expected_amount"])
                tolerance = Decimal(pattern["amount_tolerance"])
                payload = {
                    "expected_transaction_id": expected_id,
                    "recurring_pattern_id": pattern_id,
                    "account_id": pattern["account_id"],
                    "merchant_key": pattern["merchant_key"],
                    "expected_date": expected_date.isoformat(),
                    "expected_amount": str(amount),
                    "amount_lower_bound": str(max(Decimal("0"), amount - tolerance)),
                    "amount_upper_bound": str(amount + tolerance),
                    "direction": pattern["direction"],
                    "category_code": pattern["category_code"],
                    "status": "EXPECTED",
                    "matched_transaction_id": None,
                }
                events.append(
                    _event(
                        "ExpectedTransactionCreated",
                        "ExpectedTransaction",
                        expected_id,
                        1,
                        command_id,
                        payload,
                    )
                )
                candidates = []
                for transaction_id, transaction in transactions.items():
                    amount_actual = Decimal(transaction["amount"])
                    direction = "INCOME" if amount_actual > 0 else "EXPENSE"
                    merchant = " ".join(transaction["counterparty"].casefold().split())
                    distance = abs(
                        (date.fromisoformat(transaction["booking_date"]) - expected_date).days
                    )
                    if (
                        transaction_id not in matched_transaction_ids
                        and reconciled[transaction_id]["cashflow_relevant"]
                        and transaction["account_id"] == pattern["account_id"]
                        and merchant == pattern["merchant_key"]
                        and direction == pattern["direction"]
                        and distance <= 3
                        and Decimal(payload["amount_lower_bound"])
                        <= abs(amount_actual)
                        <= Decimal(payload["amount_upper_bound"])
                    ):
                        candidates.append((distance, transaction_id))
                if candidates:
                    matched_id = min(candidates)[1]
                    matched_transaction_ids.add(matched_id)
                    events.append(
                        _event(
                            "ExpectedTransactionMatched",
                            "ExpectedTransaction",
                            expected_id,
                            2,
                            command_id,
                            {**payload, "status": "MATCHED", "matched_transaction_id": matched_id},
                        )
                    )
            expected_date = _advance_expected(expected_date, pattern["frequency"], expected_day)
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "CreateExpectedTransaction",
                "idempotency_key": f"generate-expected:{month}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def match_expected_transactions(store: LocalFinanceStore, month: str | None = None) -> int:
    expectations = expected_transactions(store)
    patterns = recurring_patterns(store)
    transactions = _transactions(store)
    reconciled = reconciled_transactions(store)
    matched_ids = {
        item["matched_transaction_id"]
        for item in expectations.values()
        if item.get("matched_transaction_id")
    }
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    for expected_id, item in expectations.items():
        if item["status"] != "EXPECTED" or (month and not item["expected_date"].startswith(month)):
            continue
        pattern = patterns.get(item["recurring_pattern_id"])
        if not pattern:
            continue
        expected_date = date.fromisoformat(item["expected_date"])
        candidates: list[tuple[int, str]] = []
        for transaction_id, transaction in transactions.items():
            actual_amount = Decimal(transaction["amount"])
            actual_direction = "INCOME" if actual_amount > 0 else "EXPENSE"
            merchant = " ".join(transaction["counterparty"].casefold().split())
            distance = abs((date.fromisoformat(transaction["booking_date"]) - expected_date).days)
            if (
                transaction_id not in matched_ids
                and reconciled[transaction_id]["cashflow_relevant"]
                and transaction["account_id"] == item["account_id"]
                and merchant == pattern["merchant_key"]
                and actual_direction == item["direction"]
                and distance <= 3
                and Decimal(item["amount_lower_bound"])
                <= abs(actual_amount)
                <= Decimal(item["amount_upper_bound"])
            ):
                candidates.append((distance, transaction_id))
        if candidates:
            matched_id = min(candidates)[1]
            matched_ids.add(matched_id)
            version = store.next_aggregate_version("ExpectedTransaction", expected_id)
            events.append(
                _event(
                    "ExpectedTransactionMatched",
                    "ExpectedTransaction",
                    expected_id,
                    version,
                    command_id,
                    {**item, "status": "MATCHED", "matched_transaction_id": matched_id},
                )
            )
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "CreateExpectedTransaction",
                "idempotency_key": f"match-expected:{month or 'all'}:{store.events()[-1]['sequence_number']}",
            },
            events,
        )
    )


def _historical_variable_expenses(store: LocalFinanceStore, forecast_month: str) -> Decimal:
    transactions = _transactions(store)
    reconciled = reconciled_transactions(store)
    recurring_sources = {
        transaction_id
        for pattern in recurring_patterns(store).values()
        if pattern["status"] in {"ACTIVE", "PAUSED", "ENDED"}
        for transaction_id in pattern["source_transaction_ids"]
    }
    recurring_sources.update(
        item["matched_transaction_id"]
        for item in expected_transactions(store).values()
        if item.get("matched_transaction_id")
    )
    monthly: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for transaction_id, transaction in transactions.items():
        month = transaction["booking_date"][:7]
        if month >= forecast_month or transaction_id in recurring_sources:
            continue
        effective_amount = Decimal(reconciled[transaction_id]["effective_amount"])
        if effective_amount < 0 and reconciled[transaction_id]["cashflow_relevant"]:
            monthly[month] += abs(effective_amount)
    return Decimal(str(median(monthly.values()))) if monthly else Decimal("0")


def _realized_variable_expenses(store: LocalFinanceStore, month: str) -> Decimal:
    transactions = _transactions(store)
    reconciled = reconciled_transactions(store)
    recurring_transaction_ids = {
        transaction_id
        for pattern in recurring_patterns(store).values()
        for transaction_id in pattern["source_transaction_ids"]
    }
    recurring_transaction_ids.update(
        item["matched_transaction_id"]
        for item in expected_transactions(store).values()
        if item.get("matched_transaction_id")
    )
    return sum(
        (
            abs(Decimal(reconciled[transaction_id]["effective_amount"]))
            for transaction_id, transaction in transactions.items()
            if transaction["booking_date"].startswith(month)
            and transaction_id not in recurring_transaction_ids
            and Decimal(reconciled[transaction_id]["effective_amount"]) < 0
        ),
        Decimal("0"),
    )


def forecasts(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        forecast_id: event["payload"] for forecast_id, event in _latest(store, "Forecast").items()
    }


def create_forecasts(store: LocalFinanceStore, month: str) -> int:
    generate_expected_transactions(store, month)
    match_expected_transactions(store, month)
    start, end = _month_bounds(month)
    realized = reconciled_monthly_cashflow(store, month)
    expectations = [
        item
        for item in expected_transactions(store).values()
        if item["expected_date"].startswith(month) and item["status"] == "EXPECTED"
    ]
    expected_income = sum(
        (
            Decimal(item["expected_amount"])
            for item in expectations
            if item["direction"] == "INCOME"
        ),
        Decimal("0"),
    )
    expected_expenses = sum(
        (
            Decimal(item["expected_amount"])
            for item in expectations
            if item["direction"] == "EXPENSE"
        ),
        Decimal("0"),
    )
    variable_base = _historical_variable_expenses(store, month)
    remaining_variable = max(
        Decimal("0"), variable_base - _realized_variable_expenses(store, month)
    )
    current_forecasts = forecasts(store)
    source_events = [
        event for event in store.events() if not event["event_type"].startswith("Forecast")
    ]
    source_sequence = source_events[-1]["sequence_number"] if source_events else 0
    command_id = _id("cmd")
    events: list[dict[str, Any]] = []
    scenario_factors = {
        "CONSERVATIVE": (Decimal("0.95"), Decimal("1.15")),
        "BASE": (Decimal("1.00"), Decimal("1.00")),
        "OPTIMISTIC": (Decimal("1.00"), Decimal("0.85")),
    }
    for scenario, (income_factor, variable_factor) in scenario_factors.items():
        active = next(
            (
                (forecast_id, payload)
                for forecast_id, payload in current_forecasts.items()
                if payload["period_start"] == start.isoformat()
                and payload["scenario"] == scenario
                and payload["status"] == "ACTIVE"
            ),
            None,
        )
        if active and active[1]["source_event_sequence"] == source_sequence:
            continue
        if active:
            old_id, old_payload = active
            old_version = store.next_aggregate_version("Forecast", old_id)
            events.append(
                _event(
                    "ForecastSuperseded",
                    "Forecast",
                    old_id,
                    old_version,
                    command_id,
                    {**old_payload, "status": "SUPERSEDED"},
                )
            )
        forecast_id = _id("forecast")
        predicted_variable = (remaining_variable * variable_factor).quantize(Decimal("0.01"))
        scenario_income = (expected_income * income_factor).quantize(Decimal("0.01"))
        surplus = (
            realized["net_cashflow"] + scenario_income - expected_expenses - predicted_variable
        )
        band = max(predicted_variable * Decimal("0.15"), Decimal("10.00"))
        payload = {
            "forecast_id": forecast_id,
            "forecast_type": "MONTH_END_CASHFLOW",
            "base_date": datetime.now(UTC).date().isoformat(),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "scenario": scenario,
            "realized_income": str(realized["effective_income"]),
            "realized_expenses": str(realized["effective_expenses"]),
            "expected_income": str(scenario_income),
            "expected_fixed_expenses": str(expected_expenses),
            "predicted_variable_expenses": str(predicted_variable),
            "predicted_surplus": str(surplus),
            "lower_bound": str(surplus - band),
            "upper_bound": str(surplus + band),
            "confidence": "MEDIUM" if expectations else "LOW",
            "assumptions": [
                "confirmed active recurring patterns only",
                "median reconciled historical variable expenses",
                f"scenario={scenario}",
            ],
            "source_event_sequence": source_sequence,
            "forecast_policy_version": FORECAST_POLICY_VERSION,
            "status": "ACTIVE",
        }
        events.append(_event("ForecastCreated", "Forecast", forecast_id, 1, command_id, payload))
    if not events:
        return 0
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": "CreateForecast",
                "idempotency_key": f"create-forecast:{month}:{source_sequence}",
            },
            events,
        )
    )


def monthly_forecast(store: LocalFinanceStore, month: str) -> dict[str, dict[str, Any]]:
    start, _ = _month_bounds(month)
    result: dict[str, dict[str, Any]] = {}
    for payload in forecasts(store).values():
        if payload["period_start"] == start.isoformat() and payload["status"] in {
            "ACTIVE",
            "EVALUATED",
        }:
            result[payload["scenario"]] = payload
    return result


def evaluate_forecast(store: LocalFinanceStore, month: str) -> int:
    match_expected_transactions(store, month)
    active = monthly_forecast(store, month).get("BASE")
    if active and active["status"] == "EVALUATED":
        return 0
    if not active or active["status"] != "ACTIVE":
        raise StoreInvariantError("FINANCE_FORECAST_NOT_ACTIVE")
    expectations = expected_transactions(store)
    command_id = _id("cmd")
    missed_events: list[dict[str, Any]] = []
    for expected_id, item in expectations.items():
        if item["expected_date"].startswith(month) and item["status"] == "EXPECTED":
            version = store.next_aggregate_version("ExpectedTransaction", expected_id)
            missed_events.append(
                _event(
                    "ExpectedTransactionMissed",
                    "ExpectedTransaction",
                    expected_id,
                    version,
                    command_id,
                    {**item, "status": "MISSED"},
                )
            )
    actual = reconciled_monthly_cashflow(store, month)
    actual_surplus = actual["net_cashflow"]
    predicted = Decimal(active["predicted_surplus"])
    absolute_error = abs(actual_surplus - predicted)
    percentage_error = (
        (absolute_error / abs(actual_surplus) * 100).quantize(Decimal("0.01"))
        if actual_surplus
        else None
    )
    refreshed_expectations = list(expectations.values())
    matched = sum(
        1
        for item in refreshed_expectations
        if item["expected_date"].startswith(month) and item["status"] == "MATCHED"
    )
    missed = sum(
        1
        for item in refreshed_expectations
        if item["expected_date"].startswith(month) and item["status"] == "EXPECTED"
    )
    transactions = _transactions(store)
    month_expectations = [
        item for item in refreshed_expectations if item["expected_date"].startswith(month)
    ]
    matched_income = sum(
        (
            abs(Decimal(transactions[item["matched_transaction_id"]]["amount"]))
            for item in month_expectations
            if item["status"] == "MATCHED" and item["direction"] == "INCOME"
        ),
        Decimal("0"),
    )
    matched_expenses = sum(
        (
            abs(Decimal(transactions[item["matched_transaction_id"]]["amount"]))
            for item in month_expectations
            if item["status"] == "MATCHED" and item["direction"] == "EXPENSE"
        ),
        Decimal("0"),
    )
    actual_variable = max(Decimal("0"), actual["effective_expenses"] - matched_expenses)
    forecast_id = active["forecast_id"]
    version = store.next_aggregate_version("Forecast", forecast_id)
    evaluation = {
        **active,
        "status": "EVALUATED",
        "actual_income": str(actual["effective_income"]),
        "actual_expenses": str(actual["effective_expenses"]),
        "actual_surplus": str(actual_surplus),
        "absolute_error": str(absolute_error),
        "percentage_error": str(percentage_error) if percentage_error is not None else None,
        "expected_transactions_matched": matched,
        "expected_transactions_missed": missed,
        "component_accuracy": {
            "recurring_income_matched": str(matched_income),
            "recurring_expenses_matched": str(matched_expenses),
            "predicted_variable_expenses": active["predicted_variable_expenses"],
            "actual_variable_expenses": str(actual_variable),
            "surplus_absolute_error": str(absolute_error),
        },
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    events = [
        *missed_events,
        _event("ForecastEvaluated", "Forecast", forecast_id, version, command_id, evaluation),
    ]
    store.append_events(
        {
            "command_id": command_id,
            "command_type": "EvaluateForecast",
            "idempotency_key": f"evaluate-forecast:{forecast_id}",
        },
        events,
    )
    return len(events)


def forecast_accuracy(store: LocalFinanceStore) -> list[dict[str, Any]]:
    return [payload for payload in forecasts(store).values() if payload["status"] == "EVALUATED"]
