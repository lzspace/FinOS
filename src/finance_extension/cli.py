from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal

from .accounts import (
    account_balance_history,
    account_overview,
    close_account,
    correct_balance_snapshot,
    create_account,
    liquidity_overview,
    net_worth_overview,
    reconcile_account_balance,
    record_balance_snapshot,
    update_account,
)
from .cashflow import monthly_cashflow
from .classification import (
    category_breakdown,
    classification_review,
    classify_transactions,
    confirm_classification,
    create_rule,
    reject_classification,
)
from .crypto import KeychainKeyProvider, StaticKeyProvider
from .forecasting import (
    confirm_recurring_pattern,
    create_forecasts,
    detect_recurring_patterns,
    end_recurring_pattern,
    evaluate_forecast,
    monthly_forecast,
    pause_recurring_pattern,
    recurring_patterns,
    reject_recurring_pattern,
    update_recurring_pattern,
)
from .importer import import_csv, normalize_batch
from .multi_account_import import (
    PROFILE_ID,
    analyze_import_file,
    break_investment_funding_relation,
    confirm_investment_funding_relation,
    detect_investment_funding_relations,
    import_mapped_sections,
    investment_funding_relations,
    map_import_sections,
    reconcile_imported_period_balance,
    record_opening_balance,
    reject_investment_funding_relation,
)
from .reconciliation import (
    break_transfer,
    confirm_duplicate,
    confirm_refund,
    confirm_transfer,
    reconcile,
    reconciled_category_breakdown,
    reconciled_monthly_cashflow,
    reject_duplicate,
    reject_refund,
    reject_transfer,
    relation_review,
)
from .recovery import (
    create_backup,
    delete_backup,
    export_finance_data,
    import_finance_archive,
    key_status,
    list_backups,
    migration_status,
    repair_local_store,
    restore_backup,
    rotate_encryption_key,
    validate_store_integrity,
    verify_archive,
)
from .store import LocalFinanceStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    sub = parser.add_subparsers(dest="action", required=True)

    imp = sub.add_parser("import")
    imp.add_argument("import_action_or_file")
    imp.add_argument("file", nargs="?")
    imp.add_argument("--account")
    imp.add_argument("--analysis")
    imp.add_argument("--section", action="append", default=[])
    imp.add_argument(
        "--mode", choices=["VALIDATE_ONLY", "IMPORT_NEW", "FORCE_REIMPORT"], default="IMPORT_NEW"
    )
    imp.add_argument("--profile", default=PROFILE_ID)
    imp.add_argument("--bank")

    cash = sub.add_parser("cashflow")
    cash.add_argument("--month", required=True)
    cash.add_argument("--reconciled", action="store_true")

    classify = sub.add_parser("classify")
    classify.add_argument("--month")

    review = sub.add_parser("review")
    review.add_argument(
        "target",
        choices=[
            "classifications",
            "duplicates",
            "transfers",
            "refunds",
            "recurring",
            "investment-relations",
        ],
    )

    classification = sub.add_parser("classification")
    classification_sub = classification.add_subparsers(dest="classification_action", required=True)
    confirm = classification_sub.add_parser("confirm")
    confirm.add_argument("--transaction", required=True)
    confirm.add_argument("--category", required=True)
    confirm.add_argument("--create-rule-from", choices=["counterparty", "normalized_description"])
    confirm.add_argument("--priority", type=int, default=200)
    reject = classification_sub.add_parser("reject")
    reject.add_argument("--transaction", required=True)
    rule = classification_sub.add_parser("create-rule")
    rule.add_argument("--field", choices=["counterparty", "normalized_description"], required=True)
    rule.add_argument("--operator", choices=["CONTAINS", "EQUALS", "STARTS_WITH"], required=True)
    rule.add_argument("--value", required=True)
    rule.add_argument("--category", required=True)
    rule.add_argument("--priority", type=int, default=200)

    breakdown = sub.add_parser("category-breakdown")
    breakdown.add_argument("--month", required=True)
    breakdown.add_argument("--reconciled", action="store_true")

    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("reconcile_target", nargs="?", choices=["balance"])
    reconcile_parser.add_argument("--month")
    reconcile_parser.add_argument("--account")
    reconcile_parser.add_argument("--from", dest="period_start")
    reconcile_parser.add_argument("--to", dest="period_end")

    duplicate = sub.add_parser("duplicate")
    duplicate_sub = duplicate.add_subparsers(dest="duplicate_action", required=True)
    for action in ("confirm", "reject"):
        command = duplicate_sub.add_parser(action)
        command.add_argument("--relation", required=True)

    transfer = sub.add_parser("transfer")
    transfer_sub = transfer.add_subparsers(dest="transfer_action", required=True)
    for action in ("confirm", "reject", "break"):
        command = transfer_sub.add_parser(action)
        command.add_argument("--outgoing", required=True)
        command.add_argument("--incoming", required=True)

    refund = sub.add_parser("refund")
    refund_sub = refund.add_subparsers(dest="refund_action", required=True)
    refund_confirm = refund_sub.add_parser("confirm")
    refund_confirm.add_argument("--refund", required=True)
    refund_confirm.add_argument("--original", required=True)
    refund_confirm.add_argument("--amount", required=True)
    refund_reject = refund_sub.add_parser("reject")
    refund_reject.add_argument("--refund", required=True)
    refund_reject.add_argument("--original", required=True)

    investment = sub.add_parser("investment-relation")
    investment_sub = investment.add_subparsers(
        dest="investment_action", required=True
    )
    for action in ("confirm", "reject", "break"):
        command = investment_sub.add_parser(action)
        command.add_argument("--relation", required=True)

    recurring = sub.add_parser("recurring")
    recurring_sub = recurring.add_subparsers(dest="recurring_action", required=True)
    recurring_detect = recurring_sub.add_parser("detect")
    recurring_detect.add_argument("--from", dest="from_month", required=True)
    recurring_detect.add_argument("--to", dest="to_month", required=True)
    for action in ("confirm", "reject", "pause", "end"):
        command = recurring_sub.add_parser(action)
        command.add_argument("--pattern", required=True)
    recurring_update = recurring_sub.add_parser("update")
    recurring_update.add_argument("--pattern", required=True)
    recurring_update.add_argument("--amount", required=True)
    recurring_update.add_argument("--day-from", type=int, required=True)
    recurring_update.add_argument("--day-to", type=int, required=True)

    forecast = sub.add_parser("forecast")
    forecast_sub = forecast.add_subparsers(dest="forecast_action", required=True)
    for action in ("create", "show", "evaluate"):
        command = forecast_sub.add_parser(action)
        command.add_argument("--month", required=True)

    account = sub.add_parser("account")
    account_sub = account.add_subparsers(dest="account_action", required=True)
    account_sub.add_parser("list")
    account_create = account_sub.add_parser("create")
    account_create.add_argument("--id")
    account_create.add_argument("--name", required=True)
    account_create.add_argument("--type", required=True)
    account_create.add_argument("--institution", default="")
    account_create.add_argument("--currency", default="EUR")
    account_create.add_argument("--opened-at")
    account_create.add_argument("--reference")
    account_create.add_argument("--cashflow", action=argparse.BooleanOptionalAction, default=True)
    account_create.add_argument("--liquidity", action=argparse.BooleanOptionalAction)
    account_create.add_argument("--net-worth", action=argparse.BooleanOptionalAction, default=True)
    account_update = account_sub.add_parser("update")
    account_update.add_argument("--account", required=True)
    account_update.add_argument("--name")
    account_update.add_argument("--institution")
    account_update.add_argument("--cashflow", action=argparse.BooleanOptionalAction)
    account_update.add_argument("--liquidity", action=argparse.BooleanOptionalAction)
    account_update.add_argument("--net-worth", action=argparse.BooleanOptionalAction)
    account_close = account_sub.add_parser("close")
    account_close.add_argument("--account", required=True)
    account_close.add_argument("--date", required=True)

    balance = sub.add_parser("balance")
    balance_sub = balance.add_subparsers(dest="balance_action", required=True)
    balance_record = balance_sub.add_parser("record")
    balance_record.add_argument("--account", required=True)
    balance_record.add_argument("--date", required=True)
    balance_record.add_argument("--booked", required=True)
    balance_record.add_argument("--available")
    balance_record.add_argument("--currency", default="EUR")
    balance_record.add_argument(
        "--source",
        choices=["IMPORT_SOURCE", "MANUAL_ENTRY", "CALCULATED", "RECONCILED"],
        default="MANUAL_ENTRY",
    )
    balance_record.add_argument("--confidence", choices=["HIGH", "MEDIUM", "LOW"], default="HIGH")
    balance_correct = balance_sub.add_parser("correct")
    balance_correct.add_argument("--snapshot", required=True)
    balance_correct.add_argument("--booked", required=True)
    balance_correct.add_argument("--available")
    balance_correct.add_argument("--reason", required=True)
    balance_reconcile = balance_sub.add_parser("reconcile")
    balance_reconcile.add_argument("--account", required=True)
    balance_history = balance_sub.add_parser("history")
    balance_history.add_argument("--account", required=True)
    balance_opening = balance_sub.add_parser("opening")
    balance_opening.add_argument("balance_detail_action", choices=["record"])
    balance_opening.add_argument("--account", required=True)
    balance_opening.add_argument("--date", required=True)
    balance_opening.add_argument("--amount", required=True)
    balance_opening.add_argument("--available")
    balance_opening.add_argument("--currency", default="EUR")
    balance_opening.add_argument(
        "--source",
        choices=["bank-statement", "bank-export", "manual", "previous-system"],
        default="manual",
    )
    balance_opening.add_argument("--comment")

    sub.add_parser("liquidity")
    sub.add_parser("net-worth")

    backup = sub.add_parser("backup")
    backup_sub = backup.add_subparsers(dest="backup_action", required=True)
    backup_create = backup_sub.add_parser("create")
    backup_create.add_argument("--directory")
    backup_sub.add_parser("list").add_argument("--directory")
    backup_verify = backup_sub.add_parser("verify")
    backup_verify.add_argument("archive")
    backup_restore = backup_sub.add_parser("restore")
    backup_restore.add_argument("archive")
    backup_delete = backup_sub.add_parser("delete")
    backup_delete.add_argument("--id", required=True)
    backup_delete.add_argument("--directory")

    data = sub.add_parser("data")
    data_sub = data.add_subparsers(dest="data_action", required=True)
    data_export = data_sub.add_parser("export")
    data_export.add_argument("--directory")
    data_import = data_sub.add_parser("import-archive")
    data_import.add_argument("archive")

    store_parser = sub.add_parser("store")
    store_sub = store_parser.add_subparsers(dest="store_action", required=True)
    for action in ("validate", "repair", "migrations"):
        store_sub.add_parser(action)

    key_parser = sub.add_parser("key")
    key_sub = key_parser.add_subparsers(dest="key_action", required=True)
    key_sub.add_parser("status")
    key_rotate = key_sub.add_parser("rotate")
    key_rotate.add_argument("--backup-directory")
    return parser


def _print_cashflow(flow: dict[str, object]) -> None:
    print(
        f"Cashflow – {flow['period']}\n\n"
        f"Einnahmen: {flow['total_income']:.2f} EUR\n"
        f"Ausgaben: {flow['total_expenses']:.2f} EUR\n"
        f"Netto-Cashflow: {flow['net_cashflow']:.2f} EUR\n\n"
        f"Transaktionen: {flow['transaction_count']}\n"
        f"Datenstand: Event {flow['last_event_sequence']}\n"
        "Speicherung: lokal und verschlüsselt"
    )


def _print_reconciled_cashflow(flow: dict[str, object]) -> None:
    print(
        f"Bereinigter Cashflow – {flow['period']}\n\n"
        f"Brutto-Einnahmen: {flow['gross_income']:.2f} EUR\n"
        f"Brutto-Ausgaben: {flow['gross_expenses']:.2f} EUR\n"
        f"Interne Transfers: {flow['internal_transfers']:.2f} EUR\n"
        f"Rückerstattungen: {flow['refunds']:.2f} EUR\n"
        f"Ausgeschlossene Dubletten: {flow['excluded_duplicates']:.2f} EUR\n"
        f"Effektive Einnahmen: {flow['effective_income']:.2f} EUR\n"
        f"Effektive Ausgaben: {flow['effective_expenses']:.2f} EUR\n"
        f"Netto-Cashflow: {flow['net_cashflow']:.2f} EUR"
    )


def main() -> int:
    args = _parser().parse_args()
    provider = (
        StaticKeyProvider(os.environ["FINANCE_TEST_KEY"].encode())
        if os.getenv("FINANCE_TEST_KEY")
        else KeychainKeyProvider()
    )
    archive_provider = (
        StaticKeyProvider(os.environ["FINANCE_BACKUP_TEST_KEY"].encode())
        if os.getenv("FINANCE_BACKUP_TEST_KEY")
        else KeychainKeyProvider(service="agent-os.finance.backup", username="archive")
    )
    with LocalFinanceStore(args.data_dir, provider) as store:
        if args.action == "backup":
            if args.backup_action == "create":
                result = create_backup(store, archive_provider, args.directory)
            elif args.backup_action == "list":
                result = list_backups(store, archive_provider, args.directory)
            elif args.backup_action == "verify":
                verified = verify_archive(
                    args.archive,
                    archive_provider,
                    expected_kind="BACKUP",
                    repository_roots=store.repository_roots,
                    known_network_roots=store.known_network_roots,
                )
                result = {**verified.manifest, "verification_status": "VALID"}
            elif args.backup_action == "restore":
                result = restore_backup(store, args.archive, archive_provider)
            else:
                result = delete_backup(store, archive_provider, args.id, args.directory)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "data":
            result = (
                export_finance_data(store, archive_provider, args.directory)
                if args.data_action == "export"
                else import_finance_archive(store, args.archive, archive_provider)
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "store":
            if args.store_action == "validate":
                result = validate_store_integrity(store)
            elif args.store_action == "repair":
                result = repair_local_store(store)
            else:
                result = migration_status(store)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "key":
            result = (
                key_status(store, archive_provider)
                if args.key_action == "status"
                else rotate_encryption_key(store, archive_provider, args.backup_directory)
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "account":
            if args.account_action == "create":
                account_id = create_account(
                    store,
                    account_id=args.id,
                    display_name=args.name,
                    account_type=args.type,
                    institution=args.institution,
                    currency=args.currency,
                    include_in_cashflow=args.cashflow,
                    include_in_liquidity=args.liquidity,
                    include_in_net_worth=args.net_worth,
                    opened_at=args.opened_at,
                    account_reference=args.reference,
                )
                print(f"Konto erstellt: {account_id}")
            elif args.account_action == "update":
                changes = {
                    key: value
                    for key, value in {
                        "display_name": args.name,
                        "institution": args.institution,
                        "include_in_cashflow": args.cashflow,
                        "include_in_liquidity": args.liquidity,
                        "include_in_net_worth": args.net_worth,
                    }.items()
                    if value is not None
                }
                update_account(store, args.account, **changes)
                print("Konto aktualisiert.")
            elif args.account_action == "close":
                close_account(store, args.account, args.date)
                print("Konto geschlossen.")
            else:
                for item in account_overview(store):
                    print(
                        f"{item['account_id']} | {item['display_name']} | "
                        f"{item['account_type']} | {item['latest_balance'] or '–'} "
                        f"{item['currency']} | {item['freshness']}"
                    )
        elif args.action == "balance":
            if args.balance_action == "record":
                snapshot_id = record_balance_snapshot(
                    store,
                    account_id=args.account,
                    balance_date=args.date,
                    booked_balance=args.booked,
                    available_balance=args.available,
                    currency=args.currency,
                    source=args.source,
                    confidence=args.confidence,
                )
                print(f"Saldo-Snapshot erfasst: {snapshot_id}")
            elif args.balance_action == "correct":
                replacement = correct_balance_snapshot(
                    store,
                    args.snapshot,
                    booked_balance=args.booked,
                    available_balance=args.available,
                    reason=args.reason,
                )
                print(f"Saldo-Snapshot korrigiert: {replacement}")
            elif args.balance_action == "reconcile":
                reconcile_account_balance(store, args.account)
                print("Saldenabgleich abgeschlossen.")
            elif args.balance_action == "opening":
                source = {
                    "bank-statement": "BANK_STATEMENT",
                    "bank-export": "BANK_EXPORT",
                    "manual": "MANUAL_ENTRY",
                    "previous-system": "PREVIOUS_SYSTEM",
                }[args.source]
                balance_id = record_opening_balance(
                    store,
                    account_id=args.account,
                    balance_date=args.date,
                    booked_balance=args.amount,
                    available_balance=args.available,
                    currency=args.currency,
                    source=source,
                    confirmation=True,
                    comment=args.comment,
                )
                print(f"Anfangssaldo erfasst: {balance_id}")
            else:
                for item in account_balance_history(store, args.account):
                    print(
                        f"{item['snapshot_id']} | {item['balance_date']} | "
                        f"{item['booked_balance']} {item['currency']} | {item['source']}"
                    )
        elif args.action == "liquidity":
            result = liquidity_overview(store)
            print(
                f"Liquidität: {Decimal(result['liquid_funds']):.2f} "
                f"{result['valuation_currency']} | Stand {result['as_of']}"
            )
        elif args.action == "net-worth":
            result = net_worth_overview(store)
            print(
                f"Nettovermögen: {Decimal(result['net_worth']):.2f} "
                f"{result['valuation_currency']} | Vermögen "
                f"{Decimal(result['total_assets']):.2f} | Verbindlichkeiten "
                f"{Decimal(result['liabilities']):.2f}"
            )
        elif args.action == "import":
            if args.import_action_or_file == "analyze":
                if not args.file:
                    raise SystemExit("IMPORT_SOURCE_FILE_REQUIRED")
                result = analyze_import_file(
                    store, args.file, args.profile, args.bank
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.import_action_or_file == "map-sections":
                if not args.analysis or not args.section:
                    raise SystemExit("IMPORT_SECTION_MAPPING_MISSING")
                mappings = []
                for value in args.section:
                    if "=" not in value:
                        raise SystemExit("IMPORT_SECTION_MAPPING_INVALID")
                    section_id, account_id = value.split("=", 1)
                    mappings.append(
                        {
                            "section_id": section_id,
                            "account_id": None if account_id == "SKIP" else account_id,
                            "action": (
                                "SKIP_SECTION"
                                if account_id == "SKIP"
                                else "USE_EXISTING_ACCOUNT"
                            ),
                        }
                    )
                result = map_import_sections(store, args.analysis, mappings)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.import_action_or_file == "execute":
                if not args.analysis:
                    raise SystemExit("IMPORT_ANALYSIS_NOT_FOUND")
                result = import_mapped_sections(
                    store,
                    args.analysis,
                    parser_profile=args.profile,
                    import_mode=args.mode,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                if not args.account:
                    raise SystemExit("FINANCE_ACCOUNT_ID_REQUIRED")
                batch = import_csv(store, args.import_action_or_file, args.account)
                normalize_batch(store, batch)
                print("Import abgeschlossen: lokal und verschlüsselt")
        elif args.action == "cashflow":
            if args.reconciled:
                _print_reconciled_cashflow(reconciled_monthly_cashflow(store, args.month))
            else:
                _print_cashflow(monthly_cashflow(store, args.month))
        elif args.action == "classify":
            count = classify_transactions(store, args.month)
            print(f"Klassifikation abgeschlossen: {count} Event(s) erzeugt")
        elif args.action == "review":
            if args.target == "classifications":
                rows = classification_review(store)
                if not rows:
                    print("Keine offenen Klassifikationen.")
                for row in rows:
                    transaction = row["transaction"]
                    classification = row["classification"]
                    category = (
                        classification["payload"]["category_code"]
                        if classification
                        else "UNCLASSIFIED"
                    )
                    status = classification["payload"]["status"] if classification else "OPEN"
                    print(
                        f"{transaction['transaction_id']} | {transaction['booking_date']} | "
                        f"{transaction['counterparty']} | {transaction['amount']} "
                        f"{transaction['currency']} | {category} | {status}"
                    )
            elif args.target == "recurring":
                rows = [
                    pattern
                    for pattern in recurring_patterns(store).values()
                    if pattern["status"] == "PROPOSED"
                ]
                if not rows:
                    print("Keine offenen wiederkehrenden Muster.")
                for pattern in rows:
                    print(
                        f"{pattern['pattern_id']} | {pattern['merchant_key']} | "
                        f"{pattern['frequency']} | {pattern['expected_amount']} EUR | "
                        f"{pattern['confidence']}"
                    )
            elif args.target == "investment-relations":
                rows = [
                    relation
                    for relation in investment_funding_relations(store).values()
                    if relation["status"] == "PROPOSED"
                ]
                if not rows:
                    print("Keine offenen Investment-Verknüpfungen.")
                for relation in rows:
                    print(
                        f"{relation['relation_id']} | {relation['amount']} "
                        f"{relation['currency']} | {relation['status']}"
                    )
            else:
                relations = relation_review(store, args.target)
                if not relations:
                    print(f"Keine offenen Relationen: {args.target}.")
                for relation in relations:
                    print(
                        f"{relation['aggregate_id']} | {relation['event_type']} | "
                        f"{relation['payload']['status']}"
                    )
        elif args.action == "classification" and args.classification_action == "confirm":
            confirm_classification(
                store,
                args.transaction,
                args.category,
                create_rule_from=args.create_rule_from,
                priority=args.priority,
            )
            print("Klassifikation bestätigt.")
        elif args.action == "classification" and args.classification_action == "reject":
            reject_classification(store, args.transaction)
            print("Klassifikation abgelehnt.")
        elif args.action == "classification" and args.classification_action == "create-rule":
            rule_id = create_rule(
                store,
                field=args.field,
                operator=args.operator,
                value=args.value,
                category_code=args.category,
                priority=args.priority,
            )
            print(f"Regel erstellt: {rule_id}")
        elif args.action == "category-breakdown":
            breakdown = (
                reconciled_category_breakdown(store, args.month)
                if args.reconciled
                else category_breakdown(store, args.month)
            )
            print(f"Kategorien – {breakdown['period']}")
            for category, value in sorted(breakdown["categories"].items()):
                if args.reconciled:
                    print(
                        f"{category}: brutto {value['gross_expense']:.2f} EUR | "
                        f"Erstattungen {value['refund_amount']:.2f} EUR | "
                        f"effektiv {value['effective_expense']:.2f} EUR"
                    )
                else:
                    print(f"{category}: {value:.2f} EUR")
            if not args.reconciled:
                print(f"Transaktionen: {breakdown['transaction_count']}")
        elif args.action == "reconcile":
            if args.reconcile_target == "balance":
                if not args.account or not args.period_start or not args.period_end:
                    raise SystemExit("FINANCE_BALANCE_RECONCILIATION_INVALID")
                result = reconcile_imported_period_balance(
                    store,
                    account_id=args.account,
                    period_start=args.period_start,
                    period_end=args.period_end,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                counts = reconcile(store, args.month)
                funding = detect_investment_funding_relations(store)
                print(
                    f"Abgleich abgeschlossen: {counts['duplicates']} Dubletten-, "
                    f"{counts['transfers']} Transfer-, {counts['refunds']} Erstattungs- "
                    f"und {funding} Investment-Event(s)"
                )
        elif args.action == "duplicate":
            if args.duplicate_action == "confirm":
                confirm_duplicate(store, args.relation)
            else:
                reject_duplicate(store, args.relation)
            print("Dublettenentscheidung gespeichert.")
        elif args.action == "transfer":
            if args.transfer_action == "confirm":
                confirm_transfer(store, args.outgoing, args.incoming)
            elif args.transfer_action == "reject":
                reject_transfer(store, args.outgoing, args.incoming)
            else:
                break_transfer(store, args.outgoing, args.incoming)
            print("Transferentscheidung gespeichert.")
        elif args.action == "refund":
            if args.refund_action == "confirm":
                confirm_refund(store, args.refund, args.original, args.amount)
            else:
                reject_refund(store, args.refund, args.original)
            print("Rückerstattungsentscheidung gespeichert.")
        elif args.action == "investment-relation":
            if args.investment_action == "confirm":
                confirm_investment_funding_relation(store, args.relation)
            elif args.investment_action == "reject":
                reject_investment_funding_relation(store, args.relation)
            else:
                break_investment_funding_relation(store, args.relation)
            print("Investment-Verknüpfung gespeichert.")
        elif args.action == "recurring":
            if args.recurring_action == "detect":
                count = detect_recurring_patterns(store, args.from_month, args.to_month)
                print(f"Wiederkehrende Muster erkannt: {count} Event(s)")
            elif args.recurring_action == "confirm":
                confirm_recurring_pattern(store, args.pattern)
                print("Muster bestätigt.")
            elif args.recurring_action == "reject":
                reject_recurring_pattern(store, args.pattern)
                print("Muster abgelehnt.")
            elif args.recurring_action == "update":
                update_recurring_pattern(
                    store,
                    args.pattern,
                    amount=args.amount,
                    day_from=args.day_from,
                    day_to=args.day_to,
                )
                print("Muster aktualisiert.")
            elif args.recurring_action == "pause":
                pause_recurring_pattern(store, args.pattern)
                print("Muster pausiert.")
            else:
                end_recurring_pattern(store, args.pattern)
                print("Muster beendet.")
        elif args.action == "forecast":
            if args.forecast_action == "create":
                count = create_forecasts(store, args.month)
                print(f"Prognose erstellt: {count} Event(s)")
            elif args.forecast_action == "evaluate":
                count = evaluate_forecast(store, args.month)
                print(f"Prognose ausgewertet: {count} Event(s)")
            else:
                scenarios = monthly_forecast(store, args.month)
                if not scenarios:
                    print("Keine Prognose vorhanden.")
                else:
                    base = scenarios.get("BASE") or next(iter(scenarios.values()))
                    realized = Decimal(base["realized_income"]) - Decimal(base["realized_expenses"])
                    print(
                        f"Prognose {args.month}\n\n"
                        f"Realisierter Cashflow: {realized:.2f} EUR\n"
                        f"Noch erwartete Einnahmen: "
                        f"{Decimal(base['expected_income']):.2f} EUR\n"
                        f"Noch erwartete Fixkosten: "
                        f"{Decimal(base['expected_fixed_expenses']):.2f} EUR\n"
                        f"Variable Ausgaben, Prognose: "
                        f"{Decimal(base['predicted_variable_expenses']):.2f} EUR\n\n"
                        f"Erwarteter Monatsüberschuss: "
                        f"{Decimal(base['predicted_surplus']):.2f} EUR"
                    )
                    for name in ("CONSERVATIVE", "BASE", "OPTIMISTIC"):
                        if name in scenarios:
                            print(
                                f"{name}: {Decimal(scenarios[name]['predicted_surplus']):.2f} EUR"
                            )
    return 0
