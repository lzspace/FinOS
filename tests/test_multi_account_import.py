from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cryptography.fernet import Fernet

from finance_extension.accounts import create_account
from finance_extension.application import FinanceApplicationService
from finance_extension.crypto import StaticKeyProvider
from finance_extension.multi_account_import import (
    MultiAccountImportError,
    analyze_import_file,
    break_investment_funding_relation,
    confirm_investment_funding_relation,
    detect_investment_funding_relations,
    get_import_analysis,
    get_import_section_preview,
    import_mapped_sections,
    imported_period_reconciliations,
    initial_balance_requirements,
    investment_funding_relations,
    list_import_sections,
    map_import_sections,
    parse_german_decimal,
    reconcile_imported_period_balance,
    record_closing_balance,
    record_opening_balance,
    record_opening_security_position,
    reject_investment_funding_relation,
    security_positions,
    security_transactions,
)
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.store import LocalFinanceStore, StoreInvariantError


SYNTHETIC_EXPORT = """Synthetischer Testexport
Umsätze Girokonto
Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR
01.12.24;02.12.24;Lastschrift/Belastung;Synthetischer Einkauf;-36,85
03.12.24;03.12.24;Entgelte;Synthetische Gebühr;-17
04.12.24;04.12.24;Gutschrift;Synthetisches Gehalt;1.300
05.12.24;06.12.24;Wertpapiere;Kauf Fonds WKN ABC123;-1.000,00

Umsätze Tagesgeld PLUS-Konto
Keine Umsätze vorhanden.

Umsätze Depot
Buchungstag;Geschäftstag;Stück / Nom.;Bezeichnung;WKN;Währung;Ausführungskurs;Umsatz in EUR
06.12.24;05.12.24;10;Synthetischer Fonds;ABC123;EUR;100,00;-1.000,00
"""


class GermanMultiAccountImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="finance-multi-import-")
        self.root = Path(self.temporary.name)
        self.key = Fernet.generate_key()
        self.store = LocalFinanceStore(
            self.root / "workspace", StaticKeyProvider(self.key)
        ).open()
        self.source = self.root / "synthetic-export.csv"
        self.source.write_bytes(SYNTHETIC_EXPORT.encode("cp1252"))
        self.accounts = {
            "CHECKING": create_account(
                self.store,
                account_id="acc_checking",
                display_name="Synthetisches Girokonto",
                account_type="CHECKING",
                institution="Lokale Testbank",
                currency="EUR",
                opened_at="2024-01-01",
            ),
            "SAVINGS": create_account(
                self.store,
                account_id="acc_savings",
                display_name="Synthetisches Tagesgeld",
                account_type="SAVINGS",
                institution="Lokale Testbank",
                currency="EUR",
                opened_at="2024-01-01",
            ),
            "BROKERAGE": create_account(
                self.store,
                account_id="acc_brokerage",
                display_name="Synthetisches Depot",
                account_type="BROKERAGE",
                institution="Lokale Testbank",
                currency="EUR",
                opened_at="2024-01-01",
            ),
        }

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _analyze_and_map(self) -> dict[str, object]:
        analysis = analyze_import_file(self.store, self.source)
        mappings = []
        account_by_section = {
            "CHECKING_TRANSACTIONS": self.accounts["CHECKING"],
            "SAVINGS_TRANSACTIONS": self.accounts["SAVINGS"],
            "BROKERAGE_TRANSACTIONS": self.accounts["BROKERAGE"],
        }
        for section in analysis["sections"]:
            mappings.append(
                {
                    "section_id": section["section_id"],
                    "account_id": account_by_section[section["section_type"]],
                    "action": "USE_EXISTING_ACCOUNT",
                }
            )
        map_import_sections(self.store, analysis["analysis_id"], mappings)
        return analysis

    def test_full_cp1252_workflow_rebuild_and_idempotency(self) -> None:
        analysis = self._analyze_and_map()
        self.assertEqual(analysis["encoding"], "cp1252")
        self.assertEqual(analysis["period_start"], "2024-12-01")
        self.assertEqual(analysis["period_end"], "2024-12-06")
        self.assertEqual(
            [section["section_type"] for section in analysis["sections"]],
            [
                "CHECKING_TRANSACTIONS",
                "SAVINGS_TRANSACTIONS",
                "BROKERAGE_TRANSACTIONS",
            ],
        )
        savings = analysis["sections"][1]
        self.assertTrue(savings["empty"])
        self.assertEqual(savings["record_count"], 0)
        checking_preview = get_import_section_preview(
            self.store, analysis["analysis_id"], analysis["sections"][0]["section_id"]
        )
        self.assertEqual(checking_preview["preview"][2]["amount"], "1300.00")
        self.assertEqual(checking_preview["preview"][3]["amount"], "-1000.00")
        self.assertEqual(checking_preview["preview"][0]["value_date"], "2024-12-02")

        requirements = initial_balance_requirements(self.store, analysis["analysis_id"])
        self.assertEqual(len(requirements), 3)
        self.assertFalse(requirements[0]["satisfied"])
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_OPENING_BALANCE_REQUIRED"
        ):
            import_mapped_sections(self.store, analysis["analysis_id"])

        record_opening_balance(
            self.store,
            account_id="acc_checking",
            balance_date="2024-11-30",
            booked_balance="2000.00",
            available_balance="2000.00",
            currency="EUR",
            source="MANUAL_ENTRY",
            confirmation=True,
            comment="Synthetischer Anfangswert",
        )
        record_opening_balance(
            self.store,
            account_id="acc_savings",
            balance_date="2024-11-30",
            booked_balance="5000.00",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
            comment=None,
        )
        record_opening_security_position(
            self.store,
            account_id="acc_brokerage",
            valuation_date="2024-11-30",
            security_identifier_type="WKN",
            security_identifier="OLD123",
            security_name="Synthetischer Altbestand",
            quantity="2",
            valuation_price="50",
            price_currency="EUR",
            market_value="100",
            valuation_source="MANUAL_ENTRY",
            confirmation=True,
        )

        validated = import_mapped_sections(
            self.store, analysis["analysis_id"], import_mode="VALIDATE_ONLY"
        )
        self.assertEqual(validated["status"], "VALIDATED")
        self.assertEqual(validated["raw_transaction_count"], 4)
        self.assertEqual(validated["security_transaction_count"], 1)
        self.assertEqual(validated["empty_section_count"], 1)

        imported = import_mapped_sections(self.store, analysis["analysis_id"])
        self.assertEqual(imported["status"], "COMPLETED")
        self.assertEqual(len(self.store.events("RawTransactionImported")), 4)
        self.assertEqual(len(self.store.events("TransactionNormalized")), 4)
        self.assertEqual(len(security_transactions(self.store)), 1)
        self.assertEqual(len(self.store.events("EmptyImportSectionProcessed")), 1)
        repeated = import_mapped_sections(self.store, analysis["analysis_id"])
        self.assertEqual(repeated["status"], "ALREADY_IMPORTED")
        self.assertEqual(len(self.store.events("RawTransactionImported")), 4)

        self.assertEqual(detect_investment_funding_relations(self.store), 1)
        relation_id = next(iter(investment_funding_relations(self.store)))
        confirm_investment_funding_relation(self.store, relation_id)
        self.assertEqual(
            investment_funding_relations(self.store)[relation_id]["status"], "CONFIRMED"
        )
        with self.assertRaisesRegex(
            StoreInvariantError, "FINANCE_CONFIRMED_RELATION_PROTECTED"
        ):
            reject_investment_funding_relation(self.store, relation_id)

        record_closing_balance(
            self.store,
            account_id="acc_checking",
            balance_date="2024-12-31",
            booked_balance="2246.15",
            available_balance=None,
            currency="EUR",
            source="BANK_STATEMENT",
            confirmation=True,
        )
        reconciliation = reconcile_imported_period_balance(
            self.store,
            account_id="acc_checking",
            period_start="2024-12-01",
            period_end="2024-12-31",
        )
        self.assertEqual(reconciliation["status"], "MATCHED")
        self.assertEqual(reconciliation["balance_difference"], "0.00")

        archive_key = StaticKeyProvider(Fernet.generate_key())
        backup = create_backup(self.store, archive_key, self.root / "backups")
        break_investment_funding_relation(self.store, relation_id)
        self.assertEqual(
            investment_funding_relations(self.store)[relation_id]["status"], "BROKEN"
        )
        restore_backup(self.store, backup["path"], archive_key)
        self.assertEqual(
            investment_funding_relations(self.store)[relation_id]["status"], "CONFIRMED"
        )

        analysis_id = analysis["analysis_id"]
        event_count = len(self.store.events())
        self.store.close()
        self.store = LocalFinanceStore(
            self.root / "workspace", StaticKeyProvider(self.key)
        ).open()
        self.assertEqual(len(self.store.events()), event_count)
        self.assertIsNotNone(get_import_analysis(self.store, analysis_id))
        self.assertEqual(len(list_import_sections(self.store, analysis_id)), 3)
        self.assertEqual(len(security_positions(self.store)), 2)
        self.assertEqual(
            next(iter(imported_period_reconciliations(self.store).values()))["status"],
            "MATCHED",
        )

    def test_utf8_unknown_section_and_invalid_encoding(self) -> None:
        utf8_path = self.root / "synthetic-utf8.csv"
        utf8_path.write_text(SYNTHETIC_EXPORT, encoding="utf-8")
        analysis = analyze_import_file(self.store, utf8_path)
        self.assertEqual(analysis["encoding"], "utf-8")

        unknown_path = self.root / "synthetic-unknown.csv"
        unknown_path.write_text(
            "Umsätze Fremdkonto\nKeine Umsätze vorhanden.\n", encoding="utf-8"
        )
        unknown = analyze_import_file(self.store, unknown_path)
        self.assertEqual(unknown["sections"][0]["section_type"], "UNKNOWN")
        self.assertIn("IMPORT_UNKNOWN_SECTION", unknown["warnings"])

        invalid_path = self.root / "synthetic-invalid.csv"
        invalid_path.write_bytes(b"Ums\x81tze Girokonto")
        with self.assertRaisesRegex(MultiAccountImportError, "IMPORT_ENCODING_INVALID"):
            analyze_import_file(self.store, invalid_path)

    def test_decimal_parser_and_mapping_conflict(self) -> None:
        self.assertEqual(parse_german_decimal("-36,85"), parse_german_decimal("-36,850"))
        self.assertEqual(str(parse_german_decimal("1.300")), "1300.00")
        self.assertEqual(str(parse_german_decimal("-17")), "-17.00")
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_DECIMAL_FORMAT_INVALID"
        ):
            parse_german_decimal("1,2,3")
        with self.assertRaisesRegex(
            StoreInvariantError, "FINANCE_BALANCE_CONFIRMATION_INVALID"
        ):
            record_opening_balance(
                self.store,
                account_id="acc_checking",
                balance_date="2024-11-30",
                booked_balance="1000",
                available_balance=None,
                currency="EUR",
                source="CALCULATED",
                confirmation=True,
                comment=None,
            )

        analysis = analyze_import_file(self.store, self.source)
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_ACCOUNT_TYPE_MISMATCH"
        ):
            map_import_sections(
                self.store,
                analysis["analysis_id"],
                [
                    {
                        "section_id": analysis["sections"][2]["section_id"],
                        "account_id": "acc_checking",
                        "action": "USE_EXISTING_ACCOUNT",
                    }
                ],
            )

    def test_application_commands_and_queries_cover_import_wizard(self) -> None:
        application = FinanceApplicationService(self.store)
        analyzed = application.command(
            "AnalyzeImportFile",
            {
                "source_file_path": str(self.source),
                "requested_profile": "GermanMultiAccountCsvV1",
            },
        )["result"]
        account_by_type = {
            "CHECKING_TRANSACTIONS": "acc_checking",
            "SAVINGS_TRANSACTIONS": "acc_savings",
            "BROKERAGE_TRANSACTIONS": "acc_brokerage",
        }
        application.command(
            "MapImportSections",
            {
                "analysis_id": analyzed["analysis_id"],
                "section_mappings": [
                    {
                        "section_id": section["section_id"],
                        "account_id": account_by_type[section["section_type"]],
                        "action": "USE_EXISTING_ACCOUNT",
                    }
                    for section in analyzed["sections"]
                ],
            },
        )
        for account_id, amount in (
            ("acc_checking", "2000.00"),
            ("acc_savings", "5000.00"),
        ):
            application.command(
                "RecordOpeningBalance",
                {
                    "account_id": account_id,
                    "balance_date": "2024-11-30",
                    "booked_balance": amount,
                    "available_balance": None,
                    "currency": "EUR",
                    "source": "MANUAL_ENTRY",
                    "confirmation": True,
                    "comment": None,
                },
            )
        result = application.command(
            "ImportMappedSections",
            {
                "analysis_id": analyzed["analysis_id"],
                "parser_profile": "GermanMultiAccountCsvV1",
                "parser_version": "1.0.0",
                "import_mode": "IMPORT_NEW",
            },
        )["result"]
        self.assertEqual(result["normalized_transaction_count"], 4)
        no_closing = application.command(
            "ReconcileImportedPeriodBalance",
            {
                "account_id": "acc_checking",
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        self.assertEqual(no_closing["status"], "NO_REPORTED_CLOSING_BALANCE")
        application.command(
            "RecordClosingBalance",
            {
                "account_id": "acc_checking",
                "balance_date": "2024-12-31",
                "booked_balance": "2000.00",
                "available_balance": None,
                "currency": "EUR",
                "source": "BANK_STATEMENT",
                "confirmation": True,
            },
        )
        difference = application.command(
            "ReconcileImportedPeriodBalance",
            {
                "account_id": "acc_checking",
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        self.assertEqual(difference["status"], "DIFFERENCE")
        self.assertEqual(
            application.query(
                "GetImportAnalysis", {"analysis_id": analyzed["analysis_id"]}
            )["state"],
            "READY",
        )
        self.assertEqual(
            len(
                application.query(
                    "ListImportSections", {"analysis_id": analyzed["analysis_id"]}
                )["data"]["sections"]
            ),
            3,
        )
        self.assertEqual(
            len(application.query("ListSecurityPositions")["data"]["positions"]), 1
        )
        self.assertEqual(
            application.query(
                "GetImportedPeriodReconciliation",
                {"account_id": "acc_checking"},
            )["data"]["status"],
            "DIFFERENCE",
        )

    def test_wrong_investment_match_is_ignored_and_user_can_reject(self) -> None:
        for account_id in ("acc_checking", "acc_savings"):
            record_opening_balance(
                self.store,
                account_id=account_id,
                balance_date="2024-11-30",
                booked_balance="1000.00",
                available_balance=None,
                currency="EUR",
                source="MANUAL_ENTRY",
                confirmation=True,
                comment=None,
            )
        mismatch = self.root / "synthetic-mismatch.csv"
        mismatch.write_bytes(
            SYNTHETIC_EXPORT.replace("Kauf Fonds WKN ABC123", "Kauf Fonds WKN WRONG1").encode(
                "cp1252"
            )
        )
        analysis = analyze_import_file(self.store, mismatch)
        account_by_type = {
            "CHECKING_TRANSACTIONS": "acc_checking",
            "SAVINGS_TRANSACTIONS": "acc_savings",
            "BROKERAGE_TRANSACTIONS": "acc_brokerage",
        }
        map_import_sections(
            self.store,
            analysis["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": account_by_type[section["section_type"]],
                    "action": "USE_EXISTING_ACCOUNT",
                }
                for section in analysis["sections"]
            ],
        )
        import_mapped_sections(self.store, analysis["analysis_id"])
        self.assertEqual(detect_investment_funding_relations(self.store), 0)

        valid = self._analyze_and_map()
        import_mapped_sections(self.store, valid["analysis_id"])
        self.assertEqual(detect_investment_funding_relations(self.store), 1)
        relation_id = next(iter(investment_funding_relations(self.store)))
        reject_investment_funding_relation(self.store, relation_id)
        self.assertEqual(
            investment_funding_relations(self.store)[relation_id]["status"], "REJECTED"
        )


if __name__ == "__main__":
    unittest.main()
