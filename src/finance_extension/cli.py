from __future__ import annotations

import argparse
import os

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
from .importer import import_csv, normalize_batch
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

    classify = sub.add_parser("classify")
    classify.add_argument("--month")

    review = sub.add_parser("review")
    review.add_argument("target", choices=["classifications"])

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
            _print_cashflow(monthly_cashflow(store, args.month))
        elif args.action == "classify":
            count = classify_transactions(store, args.month)
            print(f"Klassifikation abgeschlossen: {count} Event(s) erzeugt")
        elif args.action == "review":
            rows = classification_review(store)
            if not rows:
                print("Keine offenen Klassifikationen.")
            for row in rows:
                transaction = row["transaction"]
                classification = row["classification"]
                category = (
                    classification["payload"]["category_code"] if classification else "UNCLASSIFIED"
                )
                status = classification["payload"]["status"] if classification else "OPEN"
                print(
                    f"{transaction['transaction_id']} | {transaction['booking_date']} | "
                    f"{transaction['counterparty']} | {transaction['amount']} {transaction['currency']} | "
                    f"{category} | {status}"
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
            breakdown = category_breakdown(store, args.month)
            print(f"Kategorien – {breakdown['period']}")
            for category, amount in sorted(breakdown["categories"].items()):
                print(f"{category}: {amount:.2f} EUR")
            print(f"Transaktionen: {breakdown['transaction_count']}")
    return 0
