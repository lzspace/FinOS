"""Event-sourced accounts, balance reconciliation, assets and net worth."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .forecasting import monthly_forecast
from .reconciliation import reconciled_transactions
from .store import LocalFinanceStore, StoreInvariantError

ACCOUNT_TYPES = (
    "CHECKING",
    "SAVINGS",
    "CREDIT_CARD",
    "CASH",
    "BROKERAGE",
    "LOAN",
    "MORTGAGE",
    "OTHER",
)
BALANCE_SOURCES = ("IMPORT_SOURCE", "MANUAL_ENTRY", "CALCULATED", "RECONCILED")
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")
ASSET_TYPES = ("INVESTMENT", "PROPERTY", "VEHICLE", "OTHER")
LIABILITY_TYPES = ("LOAN", "MORTGAGE", "CREDIT_CARD", "OTHER")
LIABILITY_ACCOUNT_TYPES = {"CREDIT_CARD", "LOAN", "MORTGAGE"}
LIQUID_ACCOUNT_TYPES = {"CHECKING", "SAVINGS", "CASH"}
ACCOUNT_POLICY_VERSION = "accounts@1.0.0"
BALANCE_POLICY_VERSION = "balance-reconciliation@1.0.0"
STALE_AFTER_DAYS = 35


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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


def _command(store: LocalFinanceStore, name: str, key: str, events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    command_id = events[0]["correlation_id"]
    return len(
        store.append_events(
            {
                "command_id": command_id,
                "command_type": name,
                "idempotency_key": f"{name}:{key}",
            },
            events,
        )
    )


def _decimal(value: str, error: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise StoreInvariantError(error) from exc
    if not parsed.is_finite() or (positive and parsed < 0):
        raise StoreInvariantError(error)
    return parsed


def _currency(value: str) -> str:
    if len(value) != 3 or not value.isalpha() or not value.isupper():
        raise StoreInvariantError("FINANCE_CURRENCY_INVALID")
    return value


def _valuation_fields(
    amount: Decimal,
    original_currency: str,
    valuation_currency: str | None,
    exchange_rate: str | None,
) -> dict[str, str | None]:
    target = valuation_currency or original_currency
    _currency(target)
    if target == original_currency:
        rate = Decimal("1")
    elif exchange_rate is None:
        rate = None
    else:
        rate = _decimal(exchange_rate, "FINANCE_EXCHANGE_RATE_INVALID", positive=True)
        if rate == 0:
            raise StoreInvariantError("FINANCE_EXCHANGE_RATE_INVALID")
    return {
        "original_amount": str(amount),
        "original_currency": original_currency,
        "valuation_currency": target,
        "exchange_rate": str(rate) if rate is not None else None,
        "valued_amount": str(amount * rate) if rate is not None else None,
    }


def _latest(store: LocalFinanceStore, aggregate_type: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in store.events():
        if event["aggregate_type"] == aggregate_type:
            result[event["aggregate_id"]] = event
    return result


def accounts(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {key: event["payload"] for key, event in _latest(store, "Account").items()}


def create_account(
    store: LocalFinanceStore,
    *,
    display_name: str,
    account_type: str,
    institution: str,
    currency: str,
    include_in_cashflow: bool = True,
    include_in_liquidity: bool | None = None,
    include_in_net_worth: bool = True,
    opened_at: str | None = None,
    account_reference: str | None = None,
    account_id: str | None = None,
) -> str:
    if account_type not in ACCOUNT_TYPES or not display_name.strip():
        raise StoreInvariantError("FINANCE_ACCOUNT_INVALID")
    _currency(currency)
    opened = opened_at or date.today().isoformat()
    date.fromisoformat(opened)
    account_id = account_id or _id("acc")
    if account_id in accounts(store):
        raise StoreInvariantError("FINANCE_ACCOUNT_ALREADY_EXISTS")
    command_id = _id("cmd")
    masked = None
    if account_reference:
        compact = "".join(account_reference.split())
        masked = f"•••• {compact[-4:]}" if len(compact) >= 4 else "••••"
    payload = {
        "account_id": account_id,
        "display_name": display_name.strip(),
        "account_type": account_type,
        "institution": institution.strip(),
        "currency": currency,
        "status": "ACTIVE",
        "include_in_cashflow": include_in_cashflow,
        "include_in_liquidity": (
            account_type in LIQUID_ACCOUNT_TYPES
            if include_in_liquidity is None
            else include_in_liquidity
        ),
        "include_in_net_worth": include_in_net_worth,
        "opened_at": opened,
        "closed_at": None,
        "masked_reference": masked,
        "account_reference": account_reference,
        "policy_version": ACCOUNT_POLICY_VERSION,
    }
    _command(
        store,
        "CreateAccount",
        account_id,
        [_event("AccountCreated", "Account", account_id, 1, command_id, payload)],
    )
    return account_id


def update_account(store: LocalFinanceStore, account_id: str, **changes: Any) -> int:
    current = accounts(store).get(account_id)
    if not current:
        raise StoreInvariantError("FINANCE_ACCOUNT_NOT_FOUND")
    if current["status"] == "CLOSED":
        raise StoreInvariantError("FINANCE_ACCOUNT_CLOSED")
    allowed = {
        "display_name",
        "institution",
        "include_in_cashflow",
        "include_in_liquidity",
        "include_in_net_worth",
    }
    if not changes or set(changes) - allowed:
        raise StoreInvariantError("FINANCE_ACCOUNT_UPDATE_INVALID")
    updated = {**current, **changes, "policy_version": ACCOUNT_POLICY_VERSION}
    if not str(updated["display_name"]).strip():
        raise StoreInvariantError("FINANCE_ACCOUNT_UPDATE_INVALID")
    if updated == current:
        return 0
    command_id = _id("cmd")
    version = store.next_aggregate_version("Account", account_id)
    return _command(
        store,
        "UpdateAccount",
        f"{account_id}:{hashlib.sha256(repr(sorted(changes.items())).encode()).hexdigest()}",
        [_event("AccountUpdated", "Account", account_id, version, command_id, updated)],
    )


def close_account(store: LocalFinanceStore, account_id: str, closed_at: str) -> int:
    current = accounts(store).get(account_id)
    if not current:
        raise StoreInvariantError("FINANCE_ACCOUNT_NOT_FOUND")
    if current["status"] == "CLOSED":
        return 0
    closed = date.fromisoformat(closed_at)
    if closed < date.fromisoformat(current["opened_at"]):
        raise StoreInvariantError("FINANCE_ACCOUNT_CLOSE_DATE_INVALID")
    command_id = _id("cmd")
    payload = {**current, "status": "CLOSED", "closed_at": closed_at}
    return _command(
        store,
        "CloseAccount",
        f"{account_id}:{closed_at}",
        [
            _event(
                "AccountClosed",
                "Account",
                account_id,
                store.next_aggregate_version("Account", account_id),
                command_id,
                payload,
            )
        ],
    )


def account_accepts_transaction(
    store: LocalFinanceStore, account_id: str, booking_date: str
) -> bool:
    account = accounts(store).get(account_id)
    if not account or account["status"] != "CLOSED":
        return True
    return date.fromisoformat(booking_date) <= date.fromisoformat(account["closed_at"])


def _active_snapshots(store: LocalFinanceStore, aggregate_type: str) -> dict[str, dict[str, Any]]:
    latest = _latest(store, aggregate_type)
    superseded = {
        event["payload"]["supersedes_snapshot_id"]
        for event in latest.values()
        if event["payload"].get("supersedes_snapshot_id")
    }
    return {
        snapshot_id: event["payload"]
        for snapshot_id, event in latest.items()
        if snapshot_id not in superseded
    }


def balance_snapshots(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return _active_snapshots(store, "AccountBalanceSnapshot")


def record_balance_snapshot(
    store: LocalFinanceStore,
    *,
    account_id: str,
    balance_date: str,
    booked_balance: str,
    available_balance: str | None,
    currency: str,
    source: str,
    confidence: str,
    source_import_id: str | None = None,
    valuation_currency: str | None = None,
    exchange_rate: str | None = None,
    snapshot_id: str | None = None,
) -> str:
    account = accounts(store).get(account_id)
    if not account:
        raise StoreInvariantError("FINANCE_ACCOUNT_NOT_FOUND")
    if account["status"] == "CLOSED" and date.fromisoformat(balance_date) > date.fromisoformat(
        account["closed_at"]
    ):
        raise StoreInvariantError("FINANCE_ACCOUNT_CLOSED")
    date.fromisoformat(balance_date)
    booked = _decimal(booked_balance, "FINANCE_BALANCE_INVALID")
    available = (
        _decimal(available_balance, "FINANCE_BALANCE_INVALID")
        if available_balance is not None
        else None
    )
    _currency(currency)
    if currency != account["currency"] or source not in BALANCE_SOURCES:
        raise StoreInvariantError("FINANCE_BALANCE_INVALID")
    if confidence not in CONFIDENCE_LEVELS:
        raise StoreInvariantError("FINANCE_BALANCE_INVALID")
    priority = BALANCE_SOURCES.index(source)
    if any(
        item["account_id"] == account_id
        and item["balance_date"] == balance_date
        and item["source_priority"] == priority
        for item in balance_snapshots(store).values()
    ):
        raise StoreInvariantError("FINANCE_BALANCE_SNAPSHOT_CONFLICT")
    snapshot_id = snapshot_id or _id("bal")
    command_id = _id("cmd")
    payload = {
        "snapshot_id": snapshot_id,
        "account_id": account_id,
        "balance_date": balance_date,
        "booked_balance": str(booked),
        "available_balance": str(available) if available is not None else None,
        "currency": currency,
        "source": source,
        "source_priority": priority,
        "source_import_id": source_import_id,
        "confidence": confidence,
        "supersedes_snapshot_id": None,
        "created_at": datetime.now(UTC).isoformat(),
        **_valuation_fields(booked, currency, valuation_currency, exchange_rate),
    }
    available_valuation = (
        _valuation_fields(available, currency, valuation_currency, exchange_rate)
        if available is not None
        else None
    )
    payload["valued_available_balance"] = (
        available_valuation["valued_amount"] if available_valuation else None
    )
    _command(
        store,
        "RecordBalanceSnapshot",
        snapshot_id,
        [
            _event(
                "BalanceSnapshotRecorded",
                "AccountBalanceSnapshot",
                snapshot_id,
                1,
                command_id,
                payload,
            )
        ],
    )
    return snapshot_id


def correct_balance_snapshot(
    store: LocalFinanceStore,
    snapshot_id: str,
    *,
    booked_balance: str,
    available_balance: str | None,
    reason: str,
) -> str:
    current = balance_snapshots(store).get(snapshot_id)
    if not current:
        raise StoreInvariantError("FINANCE_BALANCE_SNAPSHOT_NOT_ACTIVE")
    if not reason.strip():
        raise StoreInvariantError("FINANCE_CORRECTION_REASON_REQUIRED")
    booked = _decimal(booked_balance, "FINANCE_BALANCE_INVALID")
    available = (
        _decimal(available_balance, "FINANCE_BALANCE_INVALID")
        if available_balance is not None
        else None
    )
    replacement_id = _id("bal")
    command_id = _id("cmd")
    payload = {
        **current,
        "snapshot_id": replacement_id,
        "booked_balance": str(booked),
        "available_balance": str(available) if available is not None else None,
        "supersedes_snapshot_id": snapshot_id,
        "correction_reason": reason.strip(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload.update(
        _valuation_fields(
            booked,
            current["currency"],
            current.get("valuation_currency"),
            current.get("exchange_rate"),
        )
    )
    available_valuation = (
        _valuation_fields(
            available,
            current["currency"],
            current.get("valuation_currency"),
            current.get("exchange_rate"),
        )
        if available is not None
        else None
    )
    payload["valued_available_balance"] = (
        available_valuation["valued_amount"] if available_valuation else None
    )
    _command(
        store,
        "CorrectBalanceSnapshot",
        replacement_id,
        [
            _event(
                "BalanceSnapshotCorrected",
                "AccountBalanceSnapshot",
                replacement_id,
                1,
                command_id,
                payload,
            )
        ],
    )
    return replacement_id


def account_balance_history(store: LocalFinanceStore, account_id: str) -> list[dict[str, Any]]:
    return [
        {
            **event["payload"],
            "event_type": event["event_type"],
            "sequence_number": event["sequence_number"],
        }
        for event in store.events()
        if event["aggregate_type"] == "AccountBalanceSnapshot"
        and event["payload"]["account_id"] == account_id
    ]


def _account_transactions(store: LocalFinanceStore, account_id: str) -> list[dict[str, Any]]:
    reconciliation = reconciled_transactions(store)
    return [
        event
        for event in store.events("TransactionNormalized")
        if event["payload"]["account_id"] == account_id
        and reconciliation[event["payload"]["transaction_id"]]["duplicate_status"] != "CONFIRMED"
    ]


def reconcile_account_balance(store: LocalFinanceStore, account_id: str) -> int:
    reported = sorted(
        (
            item
            for item in balance_snapshots(store).values()
            if item["account_id"] == account_id and item["source"] != "CALCULATED"
        ),
        key=lambda item: (item["balance_date"], item["source_priority"], item["created_at"]),
    )
    if not reported:
        raise StoreInvariantError("FINANCE_BALANCE_SNAPSHOT_REQUIRED")
    target = reported[-1]
    previous = next(
        (item for item in reversed(reported[:-1]) if item["balance_date"] < target["balance_date"]),
        None,
    )
    transactions = [
        event
        for event in _account_transactions(store, account_id)
        if (previous is None or event["payload"]["booking_date"] > previous["balance_date"])
        and event["payload"]["booking_date"] <= target["balance_date"]
    ]
    if previous is None:
        calculated = None
        difference = None
        status = "OPENING_BALANCE_MISSING"
    else:
        calculated_value = Decimal(previous["booked_balance"]) + sum(
            (Decimal(event["payload"]["amount"]) for event in transactions), Decimal("0")
        )
        calculated = str(calculated_value)
        difference = str(Decimal(target["booked_balance"]) - calculated_value)
        status = "MATCHED" if Decimal(difference) == 0 else "REVIEW_REQUIRED"
    reconciliation_id = (
        f"balrec_{hashlib.sha256((account_id + target['snapshot_id']).encode()).hexdigest()[:32]}"
    )
    existing = _latest(store, "AccountBalanceReconciliation").get(reconciliation_id)
    if existing:
        return 0
    command_id = _id("cmd")
    payload = {
        "reconciliation_id": reconciliation_id,
        "account_id": account_id,
        "reported_snapshot_id": target["snapshot_id"],
        "previous_snapshot_id": previous["snapshot_id"] if previous else None,
        "balance_date": target["balance_date"],
        "reported_balance": target["booked_balance"],
        "calculated_balance": calculated,
        "balance_difference": difference,
        "currency": target["currency"],
        "transaction_event_ids": [event["event_id"] for event in transactions],
        "status": status,
        "policy_version": BALANCE_POLICY_VERSION,
    }
    return _command(
        store,
        "ReconcileAccountBalance",
        reconciliation_id,
        [
            _event(
                "AccountBalanceReconciled",
                "AccountBalanceReconciliation",
                reconciliation_id,
                1,
                command_id,
                payload,
            )
        ],
    )


def balance_reconciliations(store: LocalFinanceStore) -> dict[str, dict[str, Any]]:
    return {
        event["payload"]["account_id"]: event["payload"]
        for event in _latest(store, "AccountBalanceReconciliation").values()
    }


def _record_valuation_snapshot(
    store: LocalFinanceStore,
    *,
    aggregate_type: str,
    event_type: str,
    command_type: str,
    item_id: str,
    display_name: str,
    item_type: str,
    allowed_types: tuple[str, ...],
    valuation_date: str,
    amount: str,
    currency: str,
    valuation_currency: str | None = None,
    exchange_rate: str | None = None,
    snapshot_id: str | None = None,
) -> str:
    if item_type not in allowed_types or not display_name.strip():
        raise StoreInvariantError("FINANCE_VALUATION_INVALID")
    date.fromisoformat(valuation_date)
    value = _decimal(amount, "FINANCE_VALUATION_INVALID", positive=True)
    _currency(currency)
    snapshot_id = snapshot_id or _id("ast" if aggregate_type == "AssetSnapshot" else "lia")
    command_id = _id("cmd")
    payload = {
        "snapshot_id": snapshot_id,
        "item_id": item_id,
        "display_name": display_name.strip(),
        "item_type": item_type,
        "valuation_date": valuation_date,
        "amount": str(value),
        "currency": currency,
        "supersedes_snapshot_id": None,
        "created_at": datetime.now(UTC).isoformat(),
        **_valuation_fields(value, currency, valuation_currency, exchange_rate),
    }
    _command(
        store,
        command_type,
        snapshot_id,
        [_event(event_type, aggregate_type, snapshot_id, 1, command_id, payload)],
    )
    return snapshot_id


def create_asset_snapshot(store: LocalFinanceStore, **payload: Any) -> str:
    return _record_valuation_snapshot(
        store,
        aggregate_type="AssetSnapshot",
        event_type="AssetSnapshotRecorded",
        command_type="CreateAssetSnapshot",
        allowed_types=ASSET_TYPES,
        **payload,
    )


def create_liability_snapshot(store: LocalFinanceStore, **payload: Any) -> str:
    return _record_valuation_snapshot(
        store,
        aggregate_type="LiabilitySnapshot",
        event_type="LiabilitySnapshotRecorded",
        command_type="CreateLiabilitySnapshot",
        allowed_types=LIABILITY_TYPES,
        **payload,
    )


def _correct_valuation_snapshot(
    store: LocalFinanceStore,
    aggregate_type: str,
    event_type: str,
    command_type: str,
    snapshot_id: str,
    amount: str,
    reason: str,
) -> str:
    current = _active_snapshots(store, aggregate_type).get(snapshot_id)
    if not current or not reason.strip():
        raise StoreInvariantError("FINANCE_VALUATION_CORRECTION_INVALID")
    value = _decimal(amount, "FINANCE_VALUATION_INVALID", positive=True)
    replacement_id = _id("ast" if aggregate_type == "AssetSnapshot" else "lia")
    command_id = _id("cmd")
    payload = {
        **current,
        "snapshot_id": replacement_id,
        "amount": str(value),
        "supersedes_snapshot_id": snapshot_id,
        "correction_reason": reason.strip(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload.update(
        _valuation_fields(
            value,
            current["currency"],
            current.get("valuation_currency"),
            current.get("exchange_rate"),
        )
    )
    _command(
        store,
        command_type,
        replacement_id,
        [_event(event_type, aggregate_type, replacement_id, 1, command_id, payload)],
    )
    return replacement_id


def correct_asset_snapshot(
    store: LocalFinanceStore, snapshot_id: str, amount: str, reason: str
) -> str:
    return _correct_valuation_snapshot(
        store,
        "AssetSnapshot",
        "AssetSnapshotCorrected",
        "CorrectAssetSnapshot",
        snapshot_id,
        amount,
        reason,
    )


def correct_liability_snapshot(
    store: LocalFinanceStore, snapshot_id: str, amount: str, reason: str
) -> str:
    return _correct_valuation_snapshot(
        store,
        "LiabilitySnapshot",
        "LiabilitySnapshotCorrected",
        "CorrectLiabilitySnapshot",
        snapshot_id,
        amount,
        reason,
    )


def _latest_by_item(
    snapshots: dict[str, dict[str, Any]], as_of: str | None = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in snapshots.values():
        point = item.get("valuation_date", item.get("balance_date"))
        if as_of and point > as_of:
            continue
        current = result.get(item.get("item_id", item.get("account_id")))
        if not current or (point, item["created_at"]) > (
            current.get("valuation_date", current.get("balance_date")),
            current["created_at"],
        ):
            result[item.get("item_id", item.get("account_id"))] = item
    return result


def account_overview(store: LocalFinanceStore, as_of: str | None = None) -> list[dict[str, Any]]:
    as_of = as_of or date.today().isoformat()
    balances = _latest_by_item(balance_snapshots(store), as_of)
    reconciliations = balance_reconciliations(store)
    rows = []
    for account_id, account in accounts(store).items():
        balance = balances.get(account_id)
        balance_date = balance["balance_date"] if balance else None
        stale = balance_date is None or date.fromisoformat(balance_date) < date.fromisoformat(
            as_of
        ) - timedelta(days=STALE_AFTER_DAYS)
        rows.append(
            {
                **{key: value for key, value in account.items() if key != "account_reference"},
                "latest_balance": balance["booked_balance"] if balance else None,
                "snapshot_id": balance["snapshot_id"] if balance else None,
                "available_balance": balance["available_balance"] if balance else None,
                "balance_date": balance_date,
                "balance_source": balance["source"] if balance else None,
                "valuation_currency": balance.get("valuation_currency") if balance else None,
                "exchange_rate": balance.get("exchange_rate") if balance else None,
                "valued_balance": balance.get("valued_amount") if balance else None,
                "valued_available_balance": (
                    balance.get("valued_available_balance") if balance else None
                ),
                "reconciliation_status": reconciliations.get(account_id, {}).get(
                    "status", "NOT_RECONCILED"
                ),
                "freshness": "STALE" if stale else "CURRENT",
            }
        )
    return sorted(rows, key=lambda item: (item["status"] != "ACTIVE", item["display_name"]))


def _valuation_context(
    store: LocalFinanceStore, valuation_currency: str, as_of: str | None = None
) -> dict[str, Any]:
    _currency(valuation_currency)
    account_rows = account_overview(store, as_of)
    asset_rows = list(_latest_by_item(_active_snapshots(store, "AssetSnapshot"), as_of).values())
    liability_rows = list(
        _latest_by_item(_active_snapshots(store, "LiabilitySnapshot"), as_of).values()
    )
    conflicts = sorted(
        {
            item["currency"]
            for item in [*account_rows, *asset_rows, *liability_rows]
            if (
                item.get("valuation_currency", item.get("currency")) != valuation_currency
                or (
                    item.get("currency") != valuation_currency
                    and item.get("valued_balance", item.get("valued_amount")) is None
                )
            )
            and (item.get("latest_balance") is not None or item.get("amount") is not None)
        }
    )
    return {
        "accounts": account_rows,
        "assets": asset_rows,
        "liabilities": liability_rows,
        "currency_conflicts": conflicts,
    }


def liquidity_overview(
    store: LocalFinanceStore, valuation_currency: str = "EUR", as_of: str | None = None
) -> dict[str, Any]:
    context = _valuation_context(store, valuation_currency, as_of)
    included = [
        item
        for item in context["accounts"]
        if item["status"] == "ACTIVE"
        and item["include_in_liquidity"]
        and item.get("valuation_currency", item["currency"]) == valuation_currency
        and item["valued_balance"] is not None
        and item["latest_balance"] is not None
    ]
    total = sum(
        (Decimal(item["valued_available_balance"] or item["valued_balance"]) for item in included),
        Decimal("0"),
    )
    return {
        "valuation_currency": valuation_currency,
        "as_of": as_of or date.today().isoformat(),
        "liquid_funds": str(total),
        "accounts": included,
        "stale_account_ids": [
            item["account_id"] for item in included if item["freshness"] == "STALE"
        ],
        "currency_conflicts": context["currency_conflicts"],
    }


def net_worth_overview(
    store: LocalFinanceStore, valuation_currency: str = "EUR", as_of: str | None = None
) -> dict[str, Any]:
    context = _valuation_context(store, valuation_currency, as_of)
    valid_accounts = [
        item
        for item in context["accounts"]
        if item["include_in_net_worth"]
        and item.get("valuation_currency", item["currency"]) == valuation_currency
        and item["valued_balance"] is not None
        and item["latest_balance"] is not None
    ]
    positive_accounts = [
        item for item in valid_accounts if item["account_type"] not in LIABILITY_ACCOUNT_TYPES
    ]
    account_liabilities = [
        item for item in valid_accounts if item["account_type"] in LIABILITY_ACCOUNT_TYPES
    ]
    liquid = sum(
        (
            Decimal(item["valued_balance"])
            for item in positive_accounts
            if item["account_type"] in LIQUID_ACCOUNT_TYPES
        ),
        Decimal("0"),
    )
    savings = sum(
        (
            Decimal(item["valued_balance"])
            for item in positive_accounts
            if item["account_type"] == "SAVINGS"
        ),
        Decimal("0"),
    )
    investments = sum(
        (
            Decimal(item["valued_balance"])
            for item in positive_accounts
            if item["account_type"] == "BROKERAGE"
        ),
        Decimal("0"),
    ) + sum(
        (
            Decimal(item["valued_amount"])
            for item in context["assets"]
            if item.get("valuation_currency", item["currency"]) == valuation_currency
            and item["valued_amount"] is not None
            and item["item_type"] == "INVESTMENT"
        ),
        Decimal("0"),
    )
    other_assets = sum(
        (
            Decimal(item["valued_amount"])
            for item in context["assets"]
            if item.get("valuation_currency", item["currency"]) == valuation_currency
            and item["valued_amount"] is not None
            and item["item_type"] != "INVESTMENT"
        ),
        Decimal("0"),
    )
    other_account_assets = sum(
        (
            Decimal(item["valued_balance"])
            for item in positive_accounts
            if item["account_type"] not in LIQUID_ACCOUNT_TYPES | {"BROKERAGE"}
        ),
        Decimal("0"),
    )
    liabilities = sum(
        (abs(Decimal(item["valued_balance"])) for item in account_liabilities), Decimal("0")
    ) + sum(
        (
            Decimal(item["valued_amount"])
            for item in context["liabilities"]
            if item.get("valuation_currency", item["currency"]) == valuation_currency
            and item["valued_amount"] is not None
        ),
        Decimal("0"),
    )
    total_assets = liquid + investments + other_assets + other_account_assets
    return {
        "valuation_currency": valuation_currency,
        "as_of": as_of or date.today().isoformat(),
        "liquid_funds": str(liquid),
        "savings": str(savings),
        "investments": str(investments),
        "other_assets": str(other_assets + other_account_assets),
        "total_assets": str(total_assets),
        "liabilities": str(liabilities),
        "net_worth": str(total_assets - liabilities),
        "investable_assets": str(savings + investments),
        "currency_conflicts": context["currency_conflicts"],
        "source_snapshot_ids": [
            item.get("snapshot_id")
            for item in [*valid_accounts, *context["assets"], *context["liabilities"]]
            if item.get("snapshot_id")
        ],
    }


def net_worth_history(
    store: LocalFinanceStore, valuation_currency: str = "EUR"
) -> list[dict[str, Any]]:
    dates = sorted(
        {
            item.get("balance_date", item.get("valuation_date"))
            for item in [
                *balance_snapshots(store).values(),
                *_active_snapshots(store, "AssetSnapshot").values(),
                *_active_snapshots(store, "LiabilitySnapshot").values(),
            ]
        }
    )
    return [net_worth_overview(store, valuation_currency, point) for point in dates]


def liability_overview(store: LocalFinanceStore, valuation_currency: str = "EUR") -> dict[str, Any]:
    context = _valuation_context(store, valuation_currency)
    rows = [
        {
            "item_id": item["account_id"],
            "display_name": item["display_name"],
            "item_type": item["account_type"],
            "amount": str(abs(Decimal(item["valued_balance"]))),
            "currency": valuation_currency,
            "valuation_date": item["balance_date"],
        }
        for item in context["accounts"]
        if item["account_type"] in LIABILITY_ACCOUNT_TYPES
        and item.get("valuation_currency", item["currency"]) == valuation_currency
        and item["valued_balance"] is not None
        and item["latest_balance"] is not None
    ] + [
        {**item, "amount": item["valued_amount"], "currency": valuation_currency}
        for item in context["liabilities"]
        if item.get("valuation_currency", item["currency"]) == valuation_currency
        and item["valued_amount"] is not None
    ]
    return {
        "valuation_currency": valuation_currency,
        "total_liabilities": str(sum((Decimal(item["amount"]) for item in rows), Decimal("0"))),
        "liabilities": rows,
        "currency_conflicts": context["currency_conflicts"],
    }


def asset_allocation(store: LocalFinanceStore, valuation_currency: str = "EUR") -> dict[str, Any]:
    worth = net_worth_overview(store, valuation_currency)
    allocation = {
        "LIQUIDITY": worth["liquid_funds"],
        "INVESTMENTS": worth["investments"],
        "OTHER_ASSETS": worth["other_assets"],
    }
    return {
        "valuation_currency": valuation_currency,
        "total_assets": worth["total_assets"],
        "allocation": allocation,
        "currency_conflicts": worth["currency_conflicts"],
    }


def projected_month_end_balance(store: LocalFinanceStore, month: str) -> dict[str, Any]:
    liquidity = liquidity_overview(store)
    if not liquidity["accounts"]:
        return {
            "month": month,
            "status": "OPENING_BALANCE_MISSING",
            "latest_confirmed_liquid_balance": None,
            "realized_cashflow_since_snapshots": None,
            "remaining_expected_income": None,
            "remaining_expected_expenses": None,
            "projected_month_end_balance": None,
            "snapshot_dates": [],
            "source_event_sequence": store.events()[-1]["sequence_number"] if store.events() else 0,
        }
    realized = Decimal("0")
    for account in liquidity["accounts"]:
        for event in _account_transactions(store, account["account_id"]):
            booking = event["payload"]["booking_date"]
            if booking.startswith(month) and booking > account["balance_date"]:
                realized += Decimal(event["payload"]["amount"])
    forecast = monthly_forecast(store, month).get("BASE")
    expected_income = Decimal(forecast["expected_income"]) if forecast else Decimal("0")
    expected_expenses = (
        Decimal(forecast["expected_fixed_expenses"])
        + Decimal(forecast["predicted_variable_expenses"])
        if forecast
        else Decimal("0")
    )
    latest = Decimal(liquidity["liquid_funds"])
    projected = latest + realized + expected_income - expected_expenses
    return {
        "month": month,
        "status": "STALE" if liquidity["stale_account_ids"] else "READY",
        "latest_confirmed_liquid_balance": str(latest),
        "realized_cashflow_since_snapshots": str(realized),
        "remaining_expected_income": str(expected_income),
        "remaining_expected_expenses": str(expected_expenses),
        "projected_month_end_balance": str(projected),
        "snapshot_dates": sorted({item["balance_date"] for item in liquidity["accounts"]}),
        "source_event_sequence": store.events()[-1]["sequence_number"] if store.events() else 0,
    }


def account_reviews(store: LocalFinanceStore, as_of: str | None = None) -> list[dict[str, Any]]:
    rows = account_overview(store, as_of)
    assigned = set(accounts(store))
    reviews: list[dict[str, Any]] = []
    for item in rows:
        if item["latest_balance"] is None:
            reviews.append(
                {
                    "review_type": "OPENING_BALANCE_MISSING",
                    "account_id": item["account_id"],
                    "display_name": item["display_name"],
                }
            )
        elif item["freshness"] == "STALE":
            reviews.append(
                {
                    "review_type": "STALE_BALANCE",
                    "account_id": item["account_id"],
                    "display_name": item["display_name"],
                    "balance_date": item["balance_date"],
                }
            )
        if item["reconciliation_status"] == "REVIEW_REQUIRED":
            reviews.append(
                {
                    "review_type": "BALANCE_DIFFERENCE",
                    "account_id": item["account_id"],
                    "display_name": item["display_name"],
                    "reconciliation": balance_reconciliations(store)[item["account_id"]],
                }
            )
    for event in store.events("TransactionNormalized"):
        if event["payload"]["account_id"] not in assigned:
            reviews.append(
                {
                    "review_type": "UNASSIGNED_ACCOUNT",
                    "account_id": event["payload"]["account_id"],
                    "transaction_id": event["payload"]["transaction_id"],
                }
            )
    return reviews
