"""Read-only projections for the complete desktop import workflow."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .accounts import accounts
from .classification import active_classifications
from .multi_account_import import (
    analyses,
    closing_balances,
    closing_security_positions,
    empty_opening_security_position_confirmations,
    get_import_analysis,
    get_import_section_preview,
    imported_period_reconciliations,
    imported_section_runs,
    imported_security_position_reconciliations,
    initial_balance_requirements,
    investment_funding_relations,
    opening_balances,
    opening_security_positions,
    section_mappings,
    security_transactions,
)
from .store import LocalFinanceStore


RESUMABLE_STATUSES = {"ANALYZED", "REVIEW_REQUIRED", "PARTIALLY_COMPLETED"}
SUCCESSFUL_SECTION_STATUSES = {"IMPORTED", "EMPTY_COMPLETED", "SKIPPED"}


def _latest_analysis_id(store: LocalFinanceStore, *, resumable_only: bool) -> str | None:
    for event in reversed(store.events("ImportFileAnalyzed")):
        analysis = get_import_analysis(store, event["aggregate_id"])
        if analysis and (
            not resumable_only or analysis.get("status") in RESUMABLE_STATUSES
        ):
            return event["aggregate_id"]
    return None


def import_history(store: LocalFinanceStore) -> list[dict[str, Any]]:
    analyzed_at = {
        event["aggregate_id"]: event["occurred_at"]
        for event in store.events("ImportFileAnalyzed")
    }
    completed_at: dict[str, str] = {}
    for event in store.events("ImportSectionCompleted"):
        export_id = event["payload"].get("export_id")
        if export_id:
            completed_at[str(export_id)] = event["occurred_at"]
    account_names = {
        account_id: value["display_name"]
        for account_id, value in accounts(store).items()
    }
    balance_reviews = list(imported_period_reconciliations(store).values())
    position_reviews = list(imported_security_position_reconciliations(store).values())
    relation_reviews = investment_funding_relation_contexts(store)
    classifications = active_classifications(store)
    unclassified_by_export: dict[str, int] = {}
    for event in store.events("TransactionNormalized"):
        payload = event["payload"]
        export_id = payload.get("export_id")
        classification = classifications.get(payload["transaction_id"])
        if export_id and (
            not classification
            or classification["event_type"] != "TransactionClassificationConfirmed"
        ):
            unclassified_by_export[str(export_id)] = (
                unclassified_by_export.get(str(export_id), 0) + 1
            )
    rows: list[dict[str, Any]] = []
    for export_id in reversed(list(analyses(store))):
        analysis = get_import_analysis(store, export_id)
        if not analysis:
            continue
        sections = analysis["sections"]
        completed = sum(
            section["import_status"] in SUCCESSFUL_SECTION_STATUSES
            for section in sections
        )
        mapped_accounts = sorted(
            {
                str(section["mapped_account_id"])
                for section in sections
                if section.get("mapped_account_id")
            }
        )
        open_review_count = sum(
            item.get("export_id") == export_id and item.get("status") != "MATCHED"
            for item in balance_reviews
        ) + sum(
            item.get("export_id") == export_id and item.get("status") != "MATCHED"
            for item in position_reviews
        ) + sum(
            item.get("export_id") == export_id and item.get("status") == "PROPOSED"
            for item in relation_reviews
        ) + unclassified_by_export.get(export_id, 0)
        workflow_status = (
            "REVIEW_REQUIRED"
            if analysis["status"] == "COMPLETED" and open_review_count > 0
            else analysis["status"]
        )
        rows.append(
            {
                "export_id": export_id,
                "bank_identifier": analysis["bank_identifier"],
                "report_month": analysis["report_month"],
                "imported_at": analyzed_at[export_id],
                "completed_at": completed_at.get(export_id),
                "period_start": analysis["period_start"],
                "period_end": analysis["period_end"],
                "section_count": len(sections),
                "completed_section_count": completed,
                "record_count": sum(
                    int(section.get("record_count", 0)) for section in sections
                ),
                "account_count": len(mapped_accounts),
                "account_names": [
                    account_names.get(account_id, account_id)
                    for account_id in mapped_accounts
                ],
                "open_review_count": open_review_count,
                "status": analysis["status"],
                "workflow_status": workflow_status,
                "import_profile": analysis["import_profile"],
                "profile_version": analysis["profile_version"],
                "parser_version": "GermanMultiAccountCsvV1@1.0.0",
                "source_file_hash": analysis["source_file_hash"],
                "resumable": analysis["status"] in RESUMABLE_STATUSES,
            }
        )
    return rows


def import_execution_result(
    store: LocalFinanceStore, export_id: str
) -> dict[str, Any] | None:
    analysis = get_import_analysis(store, export_id)
    if not analysis:
        return None
    runs = [
        run
        for run in imported_section_runs(store).values()
        if run["export_id"] == export_id
    ]
    latest_by_section = {run["section_id"]: run for run in runs}
    batch_ids = {run["import_batch_id"] for run in latest_by_section.values()}
    cash_transactions = [
        event["payload"]
        for event in store.events("TransactionNormalized")
        if event["payload"].get("import_batch_id") in batch_ids
    ]
    securities = [
        item
        for item in security_transactions(store).values()
        if item["import_batch_id"] in batch_ids
    ]
    balance_reconciliations = [
        value
        for value in imported_period_reconciliations(store).values()
        if value["export_id"] == export_id
    ]
    position_reconciliations = [
        value
        for value in imported_security_position_reconciliations(store).values()
        if value["export_id"] == export_id
    ]
    relations = investment_funding_relation_contexts(store, export_id=export_id)
    account_names = {
        account_id: value["display_name"]
        for account_id, value in accounts(store).items()
    }
    section_results = []
    for section in analysis["sections"]:
        result = latest_by_section.get(section["section_id"])
        account_id = section.get("mapped_account_id")
        section_results.append(
            {
                **section,
                **(result or {}),
                "status": result["status"] if result else section["import_status"],
                "local_account_name": account_names.get(account_id),
                "raw_transaction_count": sum(
                    item.get("section_id") == section["section_id"]
                    for item in cash_transactions
                ),
                "normalized_transaction_count": sum(
                    item.get("section_id") == section["section_id"]
                    for item in cash_transactions
                ),
                "security_transaction_count": sum(
                    item["section_id"] == section["section_id"] for item in securities
                ),
                "open_relation_count": sum(
                    item["section_id"] == section["section_id"]
                    and item["status"] == "PROPOSED"
                    for item in relations
                ),
                "balance_reconciliation": next(
                    (
                        item
                        for item in balance_reconciliations
                        if item["section_id"] == section["section_id"]
                    ),
                    None,
                ),
                "position_reconciliation": next(
                    (
                        item
                        for item in position_reconciliations
                        if item["section_id"] == section["section_id"]
                    ),
                    None,
                ),
            }
        )
    return {
        "export_id": export_id,
        "status": analysis["status"],
        "bank_identifier": analysis["bank_identifier"],
        "report_month": analysis["report_month"],
        "import_batch_ids": sorted(batch_ids),
        "raw_transaction_count": len(cash_transactions),
        "normalized_transaction_count": len(cash_transactions),
        "security_transaction_count": len(securities),
        "section_results": section_results,
        "relations": relations,
        "balance_reconciliations": balance_reconciliations,
        "position_reconciliations": position_reconciliations,
    }


def import_wizard_state(
    store: LocalFinanceStore, export_id: str | None = None
) -> dict[str, Any] | None:
    selected_id = export_id or _latest_analysis_id(store, resumable_only=True)
    if not selected_id:
        return None
    analysis = get_import_analysis(store, selected_id)
    if not analysis:
        return None
    mappings = section_mappings(store, selected_id)
    all_mapped = all(
        section["section_id"] in mappings for section in analysis["sections"]
    )
    requirements = initial_balance_requirements(store, selected_id) if all_mapped else []
    requirements_satisfied = all(
        not item["required"] or item["satisfied"] for item in requirements
    )
    has_runs = any(
        run["export_id"] == selected_id for run in imported_section_runs(store).values()
    )
    status = analysis["status"]
    if not all_mapped:
        current_step = 2
    elif not requirements_satisfied:
        current_step = 3
    elif not has_runs or status == "PARTIALLY_COMPLETED":
        current_step = 4
    else:
        current_step = 5
    completed_steps = list(range(1, current_step))
    return {
        "export_id": selected_id,
        "current_step": current_step,
        "completed_steps": completed_steps,
        "status": status,
        "can_resume": status in RESUMABLE_STATUSES,
        "continuation_allowed": status != "FAILED",
        "requires_preview_confirmation": current_step == 4,
        "analysis": analysis,
        "requirements": requirements,
        "execution_result": import_execution_result(store, selected_id),
    }


def import_history_detail(
    store: LocalFinanceStore, export_id: str
) -> dict[str, Any] | None:
    wizard = import_wizard_state(store, export_id)
    if not wizard:
        return None
    execution = wizard.get("execution_result") or import_execution_result(store, export_id)
    execution = execution or {
        "section_results": [], "relations": [], "balance_reconciliations": [],
        "position_reconciliations": [], "import_batch_ids": [],
    }
    batch_ids = set(execution.get("import_batch_ids", []))
    account_names = {
        account_id: value["display_name"]
        for account_id, value in accounts(store).items()
    }
    classifications = active_classifications(store)
    transactions = []
    for event in store.events("TransactionNormalized"):
        payload = event["payload"]
        if (
            payload.get("export_id") != export_id
            and payload.get("import_batch_id") not in batch_ids
        ):
            continue
        classification = classifications.get(payload["transaction_id"])
        category_code = "UNCLASSIFIED"
        classification_status = "UNCLASSIFIED"
        if classification:
            classification_status = str(classification["payload"].get("status", "UNCLASSIFIED"))
            if classification["event_type"] == "TransactionClassificationConfirmed":
                category_code = str(
                    classification["payload"].get("category_code", "UNCLASSIFIED")
                )
        transactions.append({
            "transaction_id": payload["transaction_id"],
            "section_id": payload.get("section_id"),
            "account_id": payload["account_id"],
            "account_name": account_names.get(payload["account_id"], payload["account_id"]),
            "booking_date": payload["booking_date"],
            "value_date": payload.get("value_date"),
            "counterparty": payload.get("counterparty", ""),
            "description": payload.get("normalized_description", ""),
            "amount": payload["amount"],
            "currency": payload["currency"],
            "direction": payload["direction"],
            "transaction_type": payload.get("transaction_type"),
            "category_code": category_code,
            "classification_status": classification_status,
        })
    transactions.sort(
        key=lambda item: (item["booking_date"], item["transaction_id"]),
        reverse=True,
    )
    securities = []
    for payload in security_transactions(store).values():
        if payload.get("import_batch_id") not in batch_ids:
            continue
        securities.append({
            **payload,
            "account_name": account_names.get(payload["account_id"], payload["account_id"]),
        })
    securities.sort(
        key=lambda item: (item.get("trade_date", ""), item["transaction_id"]),
        reverse=True,
    )
    open_balance = [
        item
        for item in execution.get("balance_reconciliations", [])
        if item.get("status") != "MATCHED"
    ]
    open_positions = [
        item
        for item in execution.get("position_reconciliations", [])
        if item.get("status") != "MATCHED"
    ]
    open_relations = [
        item
        for item in execution.get("relations", [])
        if item.get("status") == "PROPOSED"
    ]
    unclassified = sum(item["category_code"] == "UNCLASSIFIED" for item in transactions)
    review_summary = {
        "unclassified_transaction_count": unclassified,
        "open_relation_count": len(open_relations),
        "balance_review_count": len(open_balance),
        "position_review_count": len(open_positions),
        "total_open_count": unclassified + len(open_relations) + len(open_balance) + len(open_positions),
    }
    audit_types = {
        "ImportFileAnalyzed",
        "ImportSectionMapped",
        "ImportSectionSkipped",
        "ImportSectionBindingConfirmed",
        "ImportBatchStarted",
        "ImportSectionCompleted",
        "ImportBatchCompleted",
        "OpeningBalanceRecorded",
        "ClosingBalanceRecorded",
        "OpeningBalanceCarryForwardAdjusted",
        "OpeningSecurityPositionRecorded",
        "ClosingSecurityPositionRecorded",
        "EmptyOpeningSecurityPositionsConfirmed",
        "ImportedPeriodBalanceReconciled",
        "ImportedSecurityPositionsReconciled",
        "BalanceDifferenceDocumented",
    }
    related_section_ids = {
        section["section_id"] for section in wizard["analysis"]["sections"]
    }
    related_accounts = {
        section["mapped_account_id"]
        for section in wizard["analysis"]["sections"]
        if section.get("mapped_account_id")
    }
    period_start = date.fromisoformat(wizard["analysis"]["period_start"])
    period_end = date.fromisoformat(wizard["analysis"]["period_end"])
    opening_date = (period_start - timedelta(days=1)).isoformat()

    def account_event_in_period(payload: dict[str, Any]) -> bool:
        if payload.get("account_id") not in related_accounts:
            return False
        event_date = payload.get("balance_date") or payload.get("valuation_date")
        if not isinstance(event_date, str):
            return False
        return opening_date <= event_date <= period_end.isoformat()

    audit = []
    for event in store.events():
        payload = event["payload"]
        if event["event_type"] not in audit_types:
            continue
        if not (
            event["aggregate_id"] == export_id
            or event["aggregate_id"] in batch_ids
            or payload.get("export_id") == export_id
            or payload.get("analysis_id") == export_id
            or payload.get("section_id") in related_section_ids
            or account_event_in_period(payload)
        ):
            continue
        audit.append(
            {
                "sequence_number": event["sequence_number"],
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "occurred_at": event["occurred_at"],
                "aggregate_type": event["aggregate_type"],
                "aggregate_id": event["aggregate_id"],
                "correlation_id": event["correlation_id"],
                "causation_id": event["causation_id"],
                "schema_version": event["schema_version"],
                "account_id": payload.get("account_id"),
                "payload": payload,
            }
        )
    return {
        **wizard,
        "execution_result": execution,
        "transactions": transactions,
        "security_transactions": securities,
        "review_summary": review_summary,
        "section_previews": [
            get_import_section_preview(store, export_id, section["section_id"])
            for section in wizard["analysis"]["sections"]
        ],
        "audit_history": audit,
    }


def balance_reconciliation_context(
    store: LocalFinanceStore,
    *,
    account_id: str,
    period_start: str,
    period_end: str,
    section_id: str | None = None,
) -> dict[str, Any]:
    reconciliations = [
        value
        for value in imported_period_reconciliations(store).values()
        if value["account_id"] == account_id
        and value["period_start"] == period_start
        and value["period_end"] == period_end
        and (section_id is None or value["section_id"] == section_id)
    ]
    reconciliation = reconciliations[-1] if reconciliations else None
    selected_section_id = section_id or (
        reconciliation["section_id"] if reconciliation else None
    )
    transactions = [
        event["payload"]
        for event in store.events("TransactionNormalized")
        if event["payload"]["account_id"] == account_id
        and event["payload"].get("section_id") == selected_section_id
        and period_start <= event["payload"]["booking_date"] <= period_end
    ]
    movement_sum = sum(
        (Decimal(item["amount"]) for item in transactions), Decimal("0")
    )
    explanations = [
        event["payload"]
        for event in store.events("BalanceDifferenceDocumented")
        if event["payload"]["account_id"] == account_id
        and event["payload"]["period_start"] == period_start
        and event["payload"]["period_end"] == period_end
    ]
    return {
        "account_id": account_id,
        "section_id": selected_section_id,
        "period_start": period_start,
        "period_end": period_end,
        "opening_balance": opening_balances(store).get(account_id),
        "closing_balance": closing_balances(store).get(account_id),
        "balance_relevant_transactions": transactions,
        "balance_relevant_movement_sum": str(movement_sum),
        "reconciliation": reconciliation,
        "difference_explanations": explanations,
        "event_data_sequence": store.events()[-1]["sequence_number"]
        if store.events()
        else 0,
    }


def position_reconciliation_context(
    store: LocalFinanceStore,
    *,
    account_id: str,
    period_start: str,
    period_end: str,
    section_id: str | None = None,
) -> dict[str, Any]:
    reconciliations = [
        value
        for value in imported_security_position_reconciliations(store).values()
        if value["account_id"] == account_id
        and value["period_start"] == period_start
        and value["period_end"] == period_end
        and (section_id is None or value["section_id"] == section_id)
    ]
    reconciliation = reconciliations[-1] if reconciliations else None
    selected_section_id = section_id or (
        reconciliation["section_id"] if reconciliation else None
    )
    openings = [
        value
        for value in opening_security_positions(store).values()
        if value["account_id"] == account_id
    ]
    closings = [
        value
        for value in closing_security_positions(store).values()
        if value["account_id"] == account_id
    ]
    transactions = [
        value
        for value in security_transactions(store).values()
        if value["account_id"] == account_id
        and value["section_id"] == selected_section_id
        and period_start <= value["booking_date"] <= period_end
    ]
    identifiers = {
        value["security_identifier"] for value in [*openings, *closings, *transactions]
    }
    rows = []
    for identifier in sorted(identifiers):
        opening = next(
            (item for item in openings if item["security_identifier"] == identifier),
            None,
        )
        closing = next(
            (item for item in closings if item["security_identifier"] == identifier),
            None,
        )
        relevant = [
            item for item in transactions if item["security_identifier"] == identifier
        ]
        purchases = sum(
            (
                abs(Decimal(item["quantity"]))
                for item in relevant
                if item["transaction_type"] == "INVESTMENT_PURCHASE"
            ),
            Decimal("0"),
        )
        sales = sum(
            (
                abs(Decimal(item["quantity"]))
                for item in relevant
                if item["transaction_type"] == "INVESTMENT_SALE"
            ),
            Decimal("0"),
        )
        other = sum(
            (
                Decimal(item["quantity"])
                for item in relevant
                if item["transaction_type"]
                not in {"INVESTMENT_PURCHASE", "INVESTMENT_SALE"}
            ),
            Decimal("0"),
        )
        opening_quantity = Decimal(opening["quantity"]) if opening else Decimal("0")
        calculated = opening_quantity + purchases - sales + other
        reported = Decimal(closing["quantity"]) if closing else None
        rows.append(
            {
                "security_identifier": identifier,
                "security_identifier_type": (
                    opening or closing or relevant[0]
                )["security_identifier_type"],
                "security_name": (opening or closing or relevant[0])["security_name"],
                "opening_quantity": str(opening_quantity),
                "purchase_quantity": str(purchases),
                "sale_quantity": str(sales),
                "other_quantity": str(other),
                "calculated_closing_quantity": str(calculated),
                "reported_closing_quantity": str(reported)
                if reported is not None
                else None,
                "quantity_difference": str(reported - calculated)
                if reported is not None
                else None,
            }
        )
    empty_confirmation = empty_opening_security_position_confirmations(store).get(
        account_id
    )
    return {
        "account_id": account_id,
        "section_id": selected_section_id,
        "period_start": period_start,
        "period_end": period_end,
        "empty_opening_confirmed": bool(empty_confirmation),
        "positions": rows,
        "reconciliation": reconciliation,
        "event_data_sequence": store.events()[-1]["sequence_number"]
        if store.events()
        else 0,
    }


def investment_funding_relation_contexts(
    store: LocalFinanceStore,
    *,
    export_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    cash = {
        event["payload"]["transaction_id"]: event["payload"]
        for event in store.events("TransactionNormalized")
    }
    securities = security_transactions(store)
    runs = imported_section_runs(store)
    batch_export = {
        value["import_batch_id"]: value["export_id"] for value in runs.values()
    }
    rows = []
    for relation in investment_funding_relations(store).values():
        cash_item = cash.get(relation["cash_transaction_id"], {})
        security = securities.get(relation["security_transaction_id"], {})
        relation_export = batch_export.get(security.get("import_batch_id"))
        if export_id and relation_export != export_id:
            continue
        if status and relation["status"] != status:
            continue
        rows.append(
            {
                **relation,
                "export_id": relation_export,
                "section_id": security.get("section_id"),
                "cash_account_id": cash_item.get("account_id"),
                "brokerage_account_id": security.get("account_id"),
                "booking_date": cash_item.get("booking_date"),
                "trade_date": security.get("trade_date"),
                "security_identifier_type": security.get(
                    "security_identifier_type"
                ),
                "security_identifier": security.get("security_identifier"),
                "match_reason": "Betrag, Währung, Zeitfenster und Wertpapierkennung stimmen überein.",
                "match_score": "1.00",
            }
        )
    return rows


__all__ = [
    "balance_reconciliation_context",
    "import_execution_result",
    "import_history",
    "import_history_detail",
    "import_wizard_state",
    "investment_funding_relation_contexts",
    "position_reconciliation_context",
]
