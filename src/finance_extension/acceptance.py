"""Wheel-contained end-to-end acceptance scenario for the 1.1.0 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from . import __version__
from .accounts import (
    account_overview,
    asset_allocation,
    balance_snapshots,
    create_account,
    create_asset_snapshot,
    create_liability_snapshot,
    liquidity_overview,
    net_worth_overview,
    reconcile_account_balance,
    record_balance_snapshot,
)
from .classification import (
    active_classifications,
    classify_transactions,
    confirm_classification,
    create_rule,
)
from .crypto import MutableStaticKeyProvider, StaticKeyProvider
from .forecasting import (
    confirm_recurring_pattern,
    create_forecasts,
    detect_recurring_patterns,
    monthly_forecast,
    recurring_patterns,
)
from .importer import import_csv, normalize_batch
from .multi_account_import import (
    analyze_import_file,
    closing_balances,
    confirm_investment_funding_relation,
    confirm_empty_opening_security_positions,
    detect_investment_funding_relations,
    import_mapped_sections,
    imported_period_reconciliations,
    imported_section_runs,
    imported_security_position_reconciliations,
    investment_funding_relations,
    map_import_sections,
    opening_balances,
    reconcile_imported_period_balance,
    reconcile_imported_security_positions,
    record_closing_balance,
    record_closing_security_position,
    record_opening_balance,
    security_positions,
    security_transactions,
    section_bindings,
)
from .reconciliation import (
    confirm_duplicate,
    confirm_refund,
    confirm_transfer,
    detect_duplicates,
    detect_refunds,
    detect_transfers,
    reconciled_transactions,
    relation_review,
)
from .recovery import (
    create_backup,
    restore_backup,
    rotate_encryption_key,
    validate_store_integrity,
    verify_archive,
)
from .store import LocalFinanceStore


HEADER = "booking_date,value_date,amount,currency,counterparty,description\n"


def _offline(*_: Any, **__: Any) -> Any:
    raise AssertionError("FINANCE_ACCEPTANCE_NETWORK_ATTEMPTED")


def _import_rows(
    store: LocalFinanceStore, root: Path, name: str, account_id: str, rows: list[str]
) -> None:
    source = root / name
    source.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    batch = import_csv(store, source, account_id)
    normalize_batch(store, batch)


def _transaction(
    store: LocalFinanceStore,
    *,
    booking_date: str,
    amount: str,
    counterparty: str,
    account_id: str = "acc_main",
) -> str:
    return next(
        event["payload"]["transaction_id"]
        for event in store.events("TransactionNormalized")
        if event["payload"]["booking_date"] == booking_date
        and event["payload"]["amount"] == amount
        and event["payload"]["counterparty"] == counterparty
        and event["payload"]["account_id"] == account_id
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _projection_snapshot(store: LocalFinanceStore) -> dict[str, Any]:
    events = store.events()
    aggregate_versions = [
        dict(row)
        for row in store.connection.execute(
            "SELECT aggregate_type, aggregate_id, MAX(event_version) AS version "
            "FROM event_store GROUP BY aggregate_type, aggregate_id "
            "ORDER BY aggregate_type, aggregate_id"
        ).fetchall()
    ]
    relation_events = {
        "DuplicateTransactionConfirmed",
        "TransferMatchConfirmed",
        "RefundRelationConfirmed",
    }
    forecast_events = {"ForecastCreated", "ForecastSuperseded", "ForecastEvaluated"}
    snapshot = {
        "last_event_sequence": events[-1]["sequence_number"] if events else 0,
        "event_count": len(events),
        "active_aggregate_versions": aggregate_versions,
        "transaction_count": len(store.events("TransactionNormalized")),
        "active_classifications": active_classifications(store),
        "confirmed_relations": [
            {"event_type": event["event_type"], "payload": event["payload"]}
            for event in events
            if event["event_type"] in relation_events
        ],
        "reconciled_transactions": reconciled_transactions(store),
        "recurring_patterns": recurring_patterns(store),
        "forecast_history": [
            {"event_type": event["event_type"], "payload": event["payload"]}
            for event in events
            if event["event_type"] in forecast_events
        ],
        "monthly_forecast": monthly_forecast(store, "2026-04"),
        "accounts": account_overview(store, "2026-07-20"),
        "active_balance_snapshots": balance_snapshots(store),
        "liquidity": liquidity_overview(store, "EUR", "2026-07-20"),
        "net_worth": net_worth_overview(store, "EUR", "2026-07-20"),
        "asset_allocation": asset_allocation(store, "EUR"),
        "import_analyses": [
            event["payload"] for event in store.events("ImportFileAnalyzed")
        ],
        "import_section_bindings": section_bindings(store),
        "import_section_runs": imported_section_runs(store),
        "opening_balances": opening_balances(store),
        "closing_balances": closing_balances(store),
        "security_transactions": security_transactions(store),
        "security_positions": security_positions(store),
        "investment_funding_relations": investment_funding_relations(store),
        "imported_period_reconciliations": imported_period_reconciliations(store),
        "imported_security_position_reconciliations": imported_security_position_reconciliations(store),
    }
    return _json_safe(snapshot)


def _assert_same(expected: dict[str, Any], actual: dict[str, Any], stage: str) -> None:
    if expected != actual:
        raise AssertionError(f"FINANCE_ACCEPTANCE_PROJECTION_MISMATCH:{stage}")


def run_acceptance(
    work_root: str | Path | None = None,
    *,
    require_installed_wheel: bool = False,
    wheel_sha256: str | None = None,
) -> dict[str, Any]:
    if __version__ != "1.1.0":
        raise AssertionError("FINANCE_ACCEPTANCE_VERSION_MISMATCH")
    source_kind = "INSTALLED_WHEEL" if "site-packages" in Path(__file__).parts else "SOURCE_TREE"
    if require_installed_wheel and source_kind != "INSTALLED_WHEEL":
        raise AssertionError("FINANCE_ACCEPTANCE_WHEEL_REQUIRED")
    if wheel_sha256 is not None and (
        len(wheel_sha256) != 64
        or any(character not in "0123456789abcdef" for character in wheel_sha256)
    ):
        raise AssertionError("FINANCE_ACCEPTANCE_WHEEL_HASH_INVALID")
    temporary = None
    if work_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="finance-acceptance-")
        root = Path(temporary.name)
    else:
        root = Path(work_root).resolve()
        root.mkdir(parents=True, exist_ok=False)

    original_network = socket.getaddrinfo
    socket.getaddrinfo = _offline
    store: LocalFinanceStore | None = None
    restored: LocalFinanceStore | None = None
    try:
        store_provider = MutableStaticKeyProvider(Fernet.generate_key())
        archive_provider = StaticKeyProvider(Fernet.generate_key())
        source_data = root / "source-workspace"
        store = LocalFinanceStore(source_data, store_provider).open()

        create_account(
            store,
            account_id="acc_main",
            display_name="Synthetic Checking",
            account_type="CHECKING",
            institution="Local Test Bank",
            currency="EUR",
            opened_at="2024-01-01",
        )
        create_account(
            store,
            account_id="acc_import_checking",
            display_name="Synthetic Import Checking",
            account_type="CHECKING",
            institution="Local Test Bank",
            currency="EUR",
            opened_at="2024-01-01",
        )
        create_account(
            store,
            account_id="acc_import_savings",
            display_name="Synthetic Import Savings",
            account_type="SAVINGS",
            institution="Local Test Bank",
            currency="EUR",
            opened_at="2024-01-01",
        )
        create_account(
            store,
            account_id="acc_import_brokerage",
            display_name="Synthetic Import Brokerage",
            account_type="BROKERAGE",
            institution="Local Test Bank",
            currency="EUR",
            opened_at="2024-01-01",
        )
        create_account(
            store,
            account_id="acc_savings",
            display_name="Synthetic Savings",
            account_type="SAVINGS",
            institution="Local Test Bank",
            currency="EUR",
            opened_at="2024-01-01",
        )
        create_account(
            store,
            account_id="acc_card",
            display_name="Synthetic Card",
            account_type="CREDIT_CARD",
            institution="Local Test Bank",
            currency="EUR",
            include_in_liquidity=False,
            opened_at="2024-01-01",
        )

        _import_rows(
            store,
            root,
            "history.csv",
            "acc_main",
            [
                "2026-01-01,2026-01-01,3200.00,EUR,Employer Synthetic,Salary",
                "2026-02-01,2026-02-01,3200.00,EUR,Employer Synthetic,Salary",
                "2026-03-01,2026-03-01,3200.00,EUR,Employer Synthetic,Salary",
                "2026-01-03,2026-01-03,-1000.00,EUR,Landlord Synthetic,Rent",
                "2026-02-03,2026-02-03,-1000.00,EUR,Landlord Synthetic,Rent",
                "2026-03-03,2026-03-03,-1000.00,EUR,Landlord Synthetic,Rent",
                "2026-01-15,2026-01-15,-17.99,EUR,Streaming Synthetic,Subscription",
                "2026-02-15,2026-02-15,-17.99,EUR,Streaming Synthetic,Subscription",
                "2026-03-15,2026-03-15,-17.99,EUR,Streaming Synthetic,Subscription",
                "2026-01-20,2026-01-20,-200.00,EUR,Variable Synthetic,January",
                "2026-02-20,2026-02-20,-300.00,EUR,Variable Synthetic,February",
                "2026-03-20,2026-03-20,-250.00,EUR,Variable Synthetic,March",
                "2026-07-01,2026-07-01,-120.00,EUR,Shop Synthetic,Groceries",
                "2026-07-06,2026-07-06,-85.00,EUR,Store Synthetic,Groceries",
                "2026-07-10,2026-07-10,-500.00,EUR,Own Account,Transfer",
            ],
        )
        _import_rows(
            store,
            root,
            "review.csv",
            "acc_main",
            [
                "2026-07-01,2026-07-01,-120.00,EUR,Shop Synthetic,Groceries",
                "2026-07-07,2026-07-07,85.00,EUR,Store Synthetic,Refund",
            ],
        )
        _import_rows(
            store,
            root,
            "savings.csv",
            "acc_savings",
            ["2026-07-11,2026-07-11,500.00,EUR,Own Account,Transfer"],
        )

        german_source = root / "synthetic-german-multi-account.csv"
        german_source.write_bytes(
            (
                "Bank;SYNTHETIC_BANK\n"
                "Umsätze Girokonto\n"
                "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
                "01.12.24;02.12.24;Lastschrift/Belastung;Synthetischer Einkauf;-36,85\n"
                "05.12.24;06.12.24;Wertpapiere;Kauf Fonds WKN ABC123;-1.000,00\n\n"
                "Umsätze Tagesgeld PLUS-Konto\nKeine Umsätze vorhanden.\n\n"
                "Umsätze Depot\n"
                "Buchungstag;Geschäftstag;Stück / Nom.;Bezeichnung;WKN;Währung;Ausführungskurs;Umsatz in EUR\n"
                "06.12.24;05.12.24;10;Synthetischer Fonds;ABC123;EUR;100,00;-1.000,00\n"
            ).encode("cp1252")
        )
        analysis = analyze_import_file(store, german_source)
        account_by_section = {
            "CHECKING": "acc_import_checking",
            "SAVINGS": "acc_import_savings",
            "BROKERAGE": "acc_import_brokerage",
        }
        map_import_sections(
            store,
            analysis["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": account_by_section[section["section_type"]],
                    "action": "USE_EXISTING_ACCOUNT",
                }
                for section in analysis["sections"]
            ],
        )
        for account_id, amount in (
            ("acc_import_checking", "3500.00"),
            ("acc_import_savings", "5000.00"),
        ):
            record_opening_balance(
                store,
                account_id=account_id,
                balance_date="2024-11-30",
                booked_balance=amount,
                available_balance=None,
                currency="EUR",
                source="MANUAL_ENTRY",
                confirmation=True,
                comment="Synthetic acceptance balance",
            )
        confirm_empty_opening_security_positions(
            store,
            account_id="acc_import_brokerage",
            valuation_date="2024-11-30",
        )
        multi_result = import_mapped_sections(store, analysis["analysis_id"])
        if multi_result["normalized_transaction_count"] != 2:
            raise AssertionError("FINANCE_ACCEPTANCE_MULTI_IMPORT_FAILED")
        if detect_investment_funding_relations(store) != 1:
            raise AssertionError("FINANCE_ACCEPTANCE_INVESTMENT_MATCH_FAILED")
        funding_id = next(iter(investment_funding_relations(store)))
        confirm_investment_funding_relation(store, funding_id)
        record_closing_balance(
            store,
            account_id="acc_import_checking",
            balance_date="2024-12-31",
            booked_balance="2463.15",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
        )
        imported_balance = reconcile_imported_period_balance(
            store,
            account_id="acc_import_checking",
            period_start="2024-12-01",
            period_end="2024-12-31",
        )
        if imported_balance["status"] != "MATCHED":
            raise AssertionError("FINANCE_ACCEPTANCE_IMPORTED_BALANCE_FAILED")
        record_closing_security_position(
            store,
            account_id="acc_import_brokerage",
            valuation_date="2024-12-31",
            security_identifier_type="WKN",
            security_identifier="ABC123",
            security_name="Synthetischer Fonds",
            quantity="10",
            confirmation=True,
        )
        imported_positions = reconcile_imported_security_positions(
            store,
            account_id="acc_import_brokerage",
            period_start="2024-12-01",
            period_end="2024-12-31",
        )
        if imported_positions["status"] != "MATCHED":
            raise AssertionError("FINANCE_ACCEPTANCE_IMPORTED_POSITIONS_FAILED")

        for value, category in (
            ("Employer Synthetic", "INCOME_SALARY"),
            ("Landlord Synthetic", "HOUSING_RENT"),
            ("Streaming Synthetic", "SUBSCRIPTIONS"),
            ("Shop Synthetic", "FOOD_GROCERIES"),
            ("Store Synthetic", "FOOD_GROCERIES"),
        ):
            create_rule(
                store,
                field="counterparty",
                operator="EQUALS",
                value=value,
                category_code=category,
                priority=200,
            )
        classify_transactions(store)
        transactions = {
            event["payload"]["transaction_id"]: event["payload"]
            for event in store.events("TransactionNormalized")
        }
        for transaction_id, state in list(active_classifications(store).items()):
            proposed = state["payload"]["category_code"]
            if proposed == "UNCLASSIFIED":
                proposed = (
                    "INCOME_OTHER"
                    if Decimal(transactions[transaction_id]["amount"]) > 0
                    else "OTHER_EXPENSE"
                )
            confirm_classification(store, transaction_id, proposed)
        corrected = _transaction(
            store,
            booking_date="2026-07-01",
            amount="-120.00",
            counterparty="Shop Synthetic",
        )
        confirm_classification(store, corrected, "OTHER_EXPENSE")
        confirm_classification(store, corrected, "FOOD_GROCERIES")

        detect_duplicates(store, "2026-07")
        for relation in list(relation_review(store, "duplicates")):
            confirm_duplicate(store, relation["aggregate_id"])
        detect_transfers(store, "2026-07")
        for relation in list(relation_review(store, "transfers")):
            confirm_transfer(
                store,
                relation["payload"]["outgoing_transaction_id"],
                relation["payload"]["incoming_transaction_id"],
            )
        detect_refunds(store, "2026-07")
        original = _transaction(
            store,
            booking_date="2026-07-06",
            amount="-85.00",
            counterparty="Store Synthetic",
        )
        refund = _transaction(
            store,
            booking_date="2026-07-07",
            amount="85.00",
            counterparty="Store Synthetic",
        )
        confirm_refund(store, refund, original, "85.00")

        detect_recurring_patterns(store, "2026-01", "2026-03")
        for pattern in list(recurring_patterns(store).values()):
            confirm_recurring_pattern(store, pattern["pattern_id"])
        create_forecasts(store, "2026-04")

        record_balance_snapshot(
            store,
            account_id="acc_main",
            balance_date="2026-01-01",
            booked_balance="4000.00",
            available_balance="4000.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        record_balance_snapshot(
            store,
            account_id="acc_main",
            balance_date="2026-07-20",
            booked_balance="5200.00",
            available_balance="5100.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        record_balance_snapshot(
            store,
            account_id="acc_savings",
            balance_date="2026-07-20",
            booked_balance="10000.00",
            available_balance="10000.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        record_balance_snapshot(
            store,
            account_id="acc_card",
            balance_date="2026-07-20",
            booked_balance="-500.00",
            available_balance=None,
            currency="EUR",
            source="MANUAL_ENTRY",
            confidence="HIGH",
        )
        reconcile_account_balance(store, "acc_main")
        create_asset_snapshot(
            store,
            item_id="asset_fund",
            display_name="Synthetic Index Fund",
            item_type="INVESTMENT",
            valuation_date="2026-07-20",
            amount="20000.00",
            currency="EUR",
        )
        create_liability_snapshot(
            store,
            item_id="liability_loan",
            display_name="Synthetic Loan",
            item_type="LOAN",
            valuation_date="2026-07-20",
            amount="2000.00",
            currency="EUR",
        )

        expected = _projection_snapshot(store)
        store.close()
        store = LocalFinanceStore(source_data, store_provider).open()
        _assert_same(expected, _projection_snapshot(store), "restart")

        backup = create_backup(store, archive_provider, root / "backups")
        verify_archive(backup["path"], archive_provider, expected_kind="BACKUP")
        restored_provider = MutableStaticKeyProvider(Fernet.generate_key())
        restored = LocalFinanceStore(root / "restored-workspace", restored_provider).open()
        restore_backup(restored, backup["path"], archive_provider)
        restored_state = _projection_snapshot(restored)
        _assert_same(expected, restored_state, "restore")

        previous_key = restored_provider.get_key()
        rotation = rotate_encryption_key(
            restored, archive_provider, root / "rotation-backups"
        )
        if restored_provider.get_key() == previous_key:
            raise AssertionError("FINANCE_ACCEPTANCE_KEY_ROTATION_FAILED")
        _assert_same(expected, _projection_snapshot(restored), "key-rotation")
        integrity = validate_store_integrity(restored)
        if integrity["status"] != "VALID":
            raise AssertionError("FINANCE_ACCEPTANCE_INTEGRITY_FAILED")

        canonical = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        return {
            "status": "PASSED",
            "application_version": __version__,
            "source": source_kind,
            "wheel_sha256": wheel_sha256,
            "offline": True,
            "workspace_initialized": True,
            "restart_rebuild_equal": True,
            "restore_equal": True,
            "key_rotation": rotation["status"],
            "integrity": integrity["status"],
            "event_count": expected["event_count"],
            "transaction_count": expected["transaction_count"],
            "projection_sha256": hashlib.sha256(canonical).hexdigest(),
        }
    finally:
        socket.getaddrinfo = original_network
        if restored is not None:
            restored.close()
        if store is not None:
            store.close()
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root")
    parser.add_argument("--report")
    parser.add_argument("--wheel-sha256", required=True)
    args = parser.parse_args(argv)
    result = run_acceptance(
        args.work_root,
        require_installed_wheel=True,
        wheel_sha256=args.wheel_sha256,
    )
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
