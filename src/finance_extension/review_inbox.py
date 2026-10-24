"""Unified, rebuildable review inbox derived from existing projections."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .accounts import account_reviews
from .category_shadow import list_category_suggestions
from .classification import classification_review
from .import_ui import import_history, investment_funding_relation_contexts
from .multi_account_import import (
    imported_period_reconciliations,
    imported_security_position_reconciliations,
)
from .reconciliation import relation_review
from .store import LocalFinanceStore


REVIEW_TYPES = (
    "CATEGORY",
    "AI_SUGGESTION",
    "BALANCE",
    "IMPORT",
    "RELATION",
    "FORMAT_CHANGE",
)


class _ReviewAccumulator(list[dict[str, Any]]):
    """Count every review while optionally retaining only renderable items."""

    def __init__(self, materialize_limit: int | None) -> None:
        super().__init__()
        self.materialize_limit = materialize_limit
        self.total_open = 0
        self.type_counts: Counter[str] = Counter()
        self.severity_counts: Counter[str] = Counter()

    def append(self, item: dict[str, Any]) -> None:
        self.total_open += 1
        self.type_counts[str(item["review_type"])] += 1
        self.severity_counts[str(item["severity"])] += 1
        if self.materialize_limit is None or len(self) < self.materialize_limit:
            super().append(item)


def _item(
    review_type: str,
    entity_id: str,
    entity_type: str,
    title: str,
    description: str,
    *,
    account_ids: list[str] | None = None,
    severity: str,
    action_target: dict[str, str],
    created_at: str,
) -> dict[str, Any]:
    return {
        "review_id": f"review:{review_type.lower()}:{entity_id}",
        "review_type": review_type,
        "severity": severity,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "account_ids": sorted(set(account_ids or [])),
        "title": title,
        "description": description,
        "action_target": action_target,
        "created_at": created_at,
    }


def _event_times(store: LocalFinanceStore) -> dict[tuple[str, str], str]:
    times: dict[tuple[str, str], str] = {}
    for event in store.events():
        payload = event["payload"]
        for field in (
            "transaction_id",
            "suggestion_id",
            "account_id",
            "reconciliation_id",
            "relation_id",
            "analysis_id",
            "export_id",
        ):
            value = payload.get(field)
            if value:
                times[(field, str(value))] = event["occurred_at"]
    return times


def review_inbox(
    store: LocalFinanceStore, *, materialize_limit: int | None = None
) -> dict[str, Any]:
    """Build the current inbox without persisting a second source of truth."""

    events = store.events()
    fallback_time = events[-1]["occurred_at"] if events else "1970-01-01T00:00:00+00:00"
    times = _event_times(store)
    items = _ReviewAccumulator(materialize_limit)
    transaction_accounts = {
        str(event["payload"]["transaction_id"]): str(event["payload"]["account_id"])
        for event in store.events("TransactionNormalized")
    }
    export_accounts: dict[str, set[str]] = {}
    for event in store.events("ImportSectionCompleted"):
        payload = event["payload"]
        export_accounts.setdefault(str(payload["export_id"]), set()).add(str(payload["account_id"]))

    suggestions = list_category_suggestions(store, {"status": "PENDING"})
    suggestion_transactions = {str(row["transaction_id"]) for row in suggestions}
    for row in suggestions:
        suggestion_id = str(row["suggestion_id"])
        merchant = str(row.get("merchant") or "Unbekannter Anbieter")
        items.append(
            _item(
                "AI_SUGGESTION",
                suggestion_id,
                "CATEGORY_SUGGESTION",
                f"AI-Vorschlag für {merchant}",
                "Der lokale Kategorievorschlag wartet auf Bestätigung oder Korrektur.",
                account_ids=[transaction_accounts[str(row["transaction_id"])]]
                if str(row["transaction_id"]) in transaction_accounts else [],
                severity="INFO",
                action_target={
                    "page": "categories",
                    "view": "suggestions",
                    "entity_id": suggestion_id,
                },
                created_at=times.get(("suggestion_id", suggestion_id), fallback_time),
            )
        )

    for row in classification_review(store):
        transaction = row["transaction"]
        transaction_id = str(transaction["transaction_id"])
        if transaction_id in suggestion_transactions:
            continue
        merchant = str(transaction.get("counterparty") or "Unbekannter Anbieter")
        items.append(
            _item(
                "CATEGORY",
                transaction_id,
                "TRANSACTION",
                f"Kategorie für {merchant} festlegen",
                "Diese Buchung besitzt noch keine bestätigte Kategorie.",
                account_ids=[transaction_accounts[transaction_id]]
                if transaction_id in transaction_accounts else [],
                severity="WARNING",
                action_target={
                    "page": "transactions",
                    "view": "detail",
                    "entity_id": transaction_id,
                },
                created_at=times.get(("transaction_id", transaction_id), fallback_time),
            )
        )

    account_review_rows = account_reviews(store)
    accounts_with_balance_difference = {
        str(row["account_id"])
        for row in account_review_rows
        if row["review_type"] == "BALANCE_DIFFERENCE"
    }
    for row in account_review_rows:
        account_id = str(row["account_id"])
        review_type = str(row["review_type"])
        transaction_id = str(row.get("transaction_id") or "")
        entity_id = (
            transaction_id
            if review_type == "UNASSIGNED_ACCOUNT" and transaction_id
            else f"{account_id}:{review_type.lower()}"
        )
        titles = {
            "BALANCE_DIFFERENCE": "Saldenabweichung prüfen",
            "OPENING_BALANCE_MISSING": "Anfangssaldo erfassen",
            "STALE_BALANCE": "Saldo aktualisieren",
            "UNASSIGNED_ACCOUNT": "Buchung einem Konto zuordnen",
        }
        items.append(
            _item(
                "BALANCE",
                entity_id,
                "TRANSACTION" if review_type == "UNASSIGNED_ACCOUNT" else "ACCOUNT",
                f"{titles.get(review_type, 'Kontostand prüfen')}: {row.get('display_name', account_id)}",
                "Der Kontostand benötigt eine fachliche Prüfung oder Aktualisierung.",
                account_ids=[account_id],
                severity="WARNING",
                action_target=(
                    {
                        "page": "transactions",
                        "view": "detail",
                        "entity_id": transaction_id,
                    }
                    if review_type == "UNASSIGNED_ACCOUNT"
                    else {
                        "page": "accounts",
                        "view": "detail",
                        "entity_id": account_id,
                    }
                ),
                created_at=times.get(("account_id", account_id), fallback_time),
            )
        )

    imported_reconciliations = [
        *imported_period_reconciliations(store).values(),
        *imported_security_position_reconciliations(store).values(),
    ]
    for row in imported_reconciliations:
        if row.get("status") == "MATCHED":
            continue
        account_id = str(row["account_id"])
        if account_id in accounts_with_balance_difference:
            continue
        reconciliation_id = str(row["reconciliation_id"])
        items.append(
            _item(
                "BALANCE",
                reconciliation_id,
                "RECONCILIATION",
                "Importierten Endbestand abgleichen",
                "Der gemeldete und der berechnete Bestand stimmen noch nicht überein.",
                account_ids=[account_id],
                severity="WARNING",
                action_target={
                    "page": "accounts",
                    "view": "reconciliations",
                    "entity_id": account_id,
                },
                created_at=times.get(
                    ("reconciliation_id", reconciliation_id), fallback_time
                ),
            )
        )

    for row in import_history(store):
        if row["status"] not in {"REVIEW_REQUIRED", "PARTIALLY_COMPLETED", "FAILED"}:
            continue
        export_id = str(row["export_id"])
        items.append(
            _item(
                "IMPORT",
                export_id,
                "IMPORT",
                f"Import {row['report_month']} prüfen",
                "Der Import wurde nicht vollständig und ohne offenen Prüfbedarf abgeschlossen.",
                account_ids=sorted(export_accounts.get(export_id, set())),
                severity="ERROR" if row["status"] == "FAILED" else "WARNING",
                action_target={
                    "page": "imports",
                    "view": "detail",
                    "entity_id": export_id,
                },
                created_at=str(row["imported_at"]),
            )
        )

    for relation_type in ("duplicates", "transfers", "refunds"):
        for event in relation_review(store, relation_type):
            payload = event["payload"]
            relation_id = str(payload.get("relation_id") or event["aggregate_id"])
            transaction_id = str(
                payload.get("primary_transaction_id")
                or payload.get("outgoing_transaction_id")
                or payload.get("refund_transaction_id")
                or ""
            )
            items.append(
                _item(
                    "RELATION",
                    relation_id,
                    "TRANSACTION_RELATION",
                    {
                        "duplicates": "Mögliche Dublette prüfen",
                        "transfers": "Möglichen Transfer prüfen",
                        "refunds": "Mögliche Rückerstattung prüfen",
                    }[relation_type],
                    "Die vorgeschlagene Beziehung ist noch nicht bestätigt oder abgelehnt.",
                    account_ids=[transaction_accounts[transaction_id]]
                    if transaction_id in transaction_accounts else [],
                    severity="WARNING",
                    action_target={
                        "page": "transactions",
                        "view": relation_type,
                        "entity_id": transaction_id or relation_id,
                    },
                    created_at=str(event["occurred_at"]),
                )
            )

    for row in investment_funding_relation_contexts(store, status="PROPOSED"):
        relation_id = str(row["relation_id"])
        items.append(
            _item(
                "RELATION",
                relation_id,
                "INVESTMENT_FUNDING_RELATION",
                "Investment-Zahlung zuordnen",
                "Eine Kontobuchung könnte eine Wertpapiertransaktion finanzieren.",
                account_ids=sorted({
                    str(value)
                    for value in (row.get("cash_account_id"), row.get("brokerage_account_id"))
                    if value
                }),
                severity="WARNING",
                action_target={
                    "page": "imports",
                    "view": "relations",
                    "entity_id": str(row.get("export_id") or relation_id),
                },
                created_at=times.get(("relation_id", relation_id), fallback_time),
            )
        )

    decided_proposals = {
        event["aggregate_id"]
        for event in store.events()
        if event["event_type"] in {"ImportMappingConfirmed", "ImportMappingRejected"}
    }
    detections = {
        str(event["payload"]["analysis_id"]): event
        for event in store.events()
        if event["event_type"]
        in {"UnknownImportFormatDetected", "ImportFormatChangeDetected"}
    }
    for proposal in store.events("ImportMappingProposed"):
        if proposal["aggregate_id"] in decided_proposals:
            continue
        analysis_id = str(proposal["payload"]["analysis_id"])
        detection = detections.get(analysis_id)
        if not detection:
            continue
        changed = detection["event_type"] == "ImportFormatChangeDetected"
        items.append(
            _item(
                "FORMAT_CHANGE",
                analysis_id,
                "IMPORT_FORMAT",
                "Geändertes Importformat prüfen" if changed else "Unbekanntes Importformat zuordnen",
                "Die erkannten Spalten benötigen eine bestätigte Zuordnung.",
                account_ids=sorted(export_accounts.get(analysis_id, set())),
                severity="ERROR"
                if detection["payload"].get("severity") == "BREAKING"
                else "WARNING",
                action_target={
                    "page": "imports",
                    "view": "format_mapping",
                    "entity_id": str(proposal["aggregate_id"]),
                },
                created_at=str(detection["occurred_at"]),
            )
        )

    severity_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    items.sort(
        key=lambda item: (
            severity_order[item["severity"]],
            item["created_at"],
            item["review_id"],
        )
    )
    return {
        "total_open": items.total_open,
        "items_by_type": {
            review_type: items.type_counts[review_type] for review_type in REVIEW_TYPES
        },
        "severity": {
            severity: items.severity_counts[severity]
            for severity in ("ERROR", "WARNING", "INFO")
        },
        "items": list(items),
    }


__all__ = ["REVIEW_TYPES", "review_inbox"]
