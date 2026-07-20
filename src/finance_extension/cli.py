from __future__ import annotations

import argparse
import os
from decimal import Decimal

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
from .store import LocalFinanceStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    sub = parser.add_subparsers(dest="action", required=True)

    imp = sub.add_parser("import")
    imp.add_argument("file")
    imp.add_argument("--account", required=True)

    cash = sub.add_parser("cashflow")
    cash.add_argument("--month", required=True)
    cash.add_argument("--reconciled", action="store_true")

    classify = sub.add_parser("classify")
    classify.add_argument("--month")

    review = sub.add_parser("review")
    review.add_argument(
        "target",
        choices=["classifications", "duplicates", "transfers", "refunds", "recurring"],
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
    reconcile_parser.add_argument("--month")

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
    with LocalFinanceStore(args.data_dir, provider) as store:
        if args.action == "import":
            batch = import_csv(store, args.file, args.account)
            normalize_batch(store, batch)
            print("Import abgeschlossen: lokal und verschlüsselt")
        elif args.action == "cashflow":
            if args.reconciled:
                _print_reconciled_cashflow(reconciled_monthly_cashflow(store, args.month))
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
            counts = reconcile(store, args.month)
            print(
                f"Abgleich abgeschlossen: {counts['duplicates']} Dubletten-, "
                f"{counts['transfers']} Transfer- und {counts['refunds']} Erstattungs-Event(s)"
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
