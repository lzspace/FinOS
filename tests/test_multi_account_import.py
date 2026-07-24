from __future__ import annotations

import hashlib
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
    confirm_empty_opening_security_positions,
    detect_investment_funding_relations,
    get_import_analysis,
    get_import_section_preview,
    import_mapped_sections,
    imported_period_reconciliations,
    imported_section_runs,
    initial_balance_requirements,
    investment_funding_relations,
    list_import_sections,
    map_import_sections,
    parse_german_decimal,
    parse_german_quantity,
    reconcile_imported_period_balance,
    reconcile_imported_security_positions,
    record_closing_balance,
    record_closing_security_position,
    record_opening_balance,
    record_opening_security_position,
    reject_investment_funding_relation,
    security_positions,
    security_transactions,
    section_bindings,
)
from finance_extension.recovery import create_backup, restore_backup
from finance_extension.schema_validation import validate_event
from finance_extension.store import LocalFinanceStore, StoreInvariantError


SYNTHETIC_EXPORT = """Bank;SYNTHETIC_BANK
Synthetischer Testexport
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
            "CHECKING": self.accounts["CHECKING"],
            "SAVINGS": self.accounts["SAVINGS"],
            "BROKERAGE": self.accounts["BROKERAGE"],
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

    def test_uses_period_in_section_heading_when_transactions_cover_one_day(self) -> None:
        source = self.root / "august-section-heading.csv"
        source.write_text(
            "Bank;SYNTHETIC_BANK\n"
            "Umsätze Girokonto;Zeitraum: 01.08.2024 - 31.08.2024\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "23.08.24;23.08.24;Lastschrift/Belastung;Synthetischer Einkauf;-36,85\n",
            encoding="cp1252",
        )

        analysis = analyze_import_file(self.store, source)

        self.assertEqual(analysis["period_start"], "2024-08-01")
        self.assertEqual(analysis["period_end"], "2024-08-31")

    def test_accepts_four_digit_booking_years(self) -> None:
        source = self.root / "four-digit-booking-year.csv"
        source.write_text(
            "Bank;SYNTHETIC_BANK\n"
            "Umsätze Girokonto;Zeitraum: 01.08.2024 - 31.08.2024\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "23.08.2024;23.08.2024;Lastschrift/Belastung;Synthetischer Einkauf;-36,85\n",
            encoding="cp1252",
        )

        analysis = analyze_import_file(self.store, source)

        self.assertEqual(analysis["period_start"], "2024-08-01")
        self.assertEqual(analysis["period_end"], "2024-08-31")

    def test_reanalysis_uses_parser_version_in_its_idempotency_key(self) -> None:
        source = self.root / "reanalysis.csv"
        source.write_text(
            "Bank;SYNTHETIC_BANK\n"
            "Umsätze Girokonto;Zeitraum: 01.08.2024 - 31.08.2024\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "23.08.2024;23.08.2024;Lastschrift/Belastung;Synthetischer Einkauf;-36,85\n",
            encoding="cp1252",
        )
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        # Simulate the command-log entry written by parser 1.0.0. The old
        # idempotency key must not suppress this corrected parser analysis.
        self.store.connection.execute(
            "INSERT INTO command_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmd_old_analysis",
                "AnalyzeImportFile",
                "AnalyzeImportFile:" + file_hash,
                "COMPLETED",
                "2024-08-23T00:00:00+00:00",
                "2024-08-23T00:00:00+00:00",
                "[]",
                None,
            ),
        )
        self.store.connection.commit()
        analysis = analyze_import_file(self.store, source)

        self.assertEqual(analysis["period_start"], "2024-08-01")
        self.assertEqual(analysis["period_end"], "2024-08-31")
        self.assertIsNotNone(get_import_analysis(self.store, analysis["analysis_id"]))

    def test_accepts_legacy_import_analysis_for_recovery_validation(self) -> None:
        validate_event(
            {
                "event_type": "ImportFileAnalyzed",
                "payload": {
                    "analysis_id": "analysis_" + "a" * 64,
                    "detected_profile": "GermanMultiAccountCsvV1",
                    "encoding": "cp1252",
                    "delimiter": ";",
                    "file_hash": "a" * 64,
                    "file_size": 1,
                    "period_start": "2024-08-23",
                    "period_end": "2024-08-23",
                    "profile_version": "1.0.0",
                    "sections": [{}],
                    "status": "ANALYZED",
                    "warnings": [],
                },
            }
        )

    def test_full_cp1252_workflow_rebuild_and_idempotency(self) -> None:
        analysis = self._analyze_and_map()
        self.assertEqual(analysis["encoding"], "cp1252")
        self.assertEqual(analysis["period_start"], "2024-12-01")
        self.assertEqual(analysis["period_end"], "2024-12-31")
        self.assertEqual(
            [section["section_type"] for section in analysis["sections"]],
            [
                "CHECKING",
                "SAVINGS",
                "BROKERAGE",
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
        blocked = import_mapped_sections(self.store, analysis["analysis_id"])
        self.assertEqual(blocked["status"], "REVIEW_REQUIRED")
        self.assertTrue(
            all(
                item["status"] == "REVIEW_REQUIRED"
                for item in blocked["section_results"]
                if item["section_type"] != "BROKERAGE"
            )
        )

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
        self.assertEqual(repeated["status"], "REVIEW_REQUIRED")
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
            "Bank;SYNTHETIC_BANK\n"
            "Umsätze Fremdkonto\nKeine Umsätze vorhanden.\n"
            "Umsätze Girokonto\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "01.12.24;01.12.24;Entgelte;Synthetisch;-1,00\n",
            encoding="utf-8",
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
        self.assertEqual(str(parse_german_quantity("0,906")), "0.90600000")
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

    def test_bank_month_binding_is_reused_but_visible_for_confirmation(self) -> None:
        december = self._analyze_and_map()
        self.assertEqual(december["bank_identifier"], "SYNTHETIC_BANK")
        self.assertEqual(len(section_bindings(self.store)), 3)

        january_path = self.root / "synthetic-january.csv"
        january_path.write_bytes(
            SYNTHETIC_EXPORT.replace(".12.24", ".01.25").encode("cp1252")
        )
        january = analyze_import_file(self.store, january_path)
        self.assertEqual(january["period_start"], "2025-01-01")
        self.assertEqual(january["period_end"], "2025-01-31")
        self.assertEqual(
            [section["mapped_account_id"] for section in january["sections"]],
            ["acc_checking", "acc_savings", "acc_brokerage"],
        )
        self.assertTrue(
            all(section["import_status"] == "ANALYZED" for section in january["sections"])
        )

    def test_bank_period_order_and_same_type_ambiguity_are_rejected(self) -> None:
        no_bank = self.root / "no-bank.csv"
        no_bank.write_text(
            "Umsätze Girokonto\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "01.12.24;01.12.24;Entgelte;Test;-1,00\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_BANK_IDENTIFIER_REQUIRED"
        ):
            analyze_import_file(self.store, no_bank)
        confirmed = analyze_import_file(
            self.store, no_bank, confirmed_bank_identifier="MANUAL_BANK"
        )
        self.assertEqual(confirmed["bank_identifier"], "MANUAL_BANK")

        all_empty = self.root / "all-empty.csv"
        all_empty.write_text(
            "Bank;SYNTHETIC_BANK\n"
            "Berichtsmonat;12.2024\n"
            "Umsätze Girokonto\nKeine Umsätze vorhanden.\n"
            "Umsätze Tagesgeld PLUS-Konto\nKeine Umsätze vorhanden.\n"
            "Umsätze Depot\nKeine Umsätze vorhanden.\n",
            encoding="utf-8",
        )
        empty_analysis = analyze_import_file(self.store, all_empty)
        self.assertEqual(empty_analysis["report_month"], "2024-12")
        self.assertTrue(all(section["empty"] for section in empty_analysis["sections"]))

        multiple_banks = self.root / "multiple-banks.csv"
        multiple_banks.write_text(
            SYNTHETIC_EXPORT.replace(
                "Bank;SYNTHETIC_BANK", "Bank;SYNTHETIC_BANK\nBank;OTHER_BANK"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_MULTIPLE_BANKS_NOT_SUPPORTED"
        ):
            analyze_import_file(self.store, multiple_banks)

        mixed_months = self.root / "mixed-months.csv"
        mixed_months.write_text(
            SYNTHETIC_EXPORT.replace(
                "06.12.24;05.12.24;10;", "06.01.25;05.01.25;10;"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MultiAccountImportError, "IMPORT_PERIOD_MISMATCH"):
            analyze_import_file(self.store, mixed_months)

        wrong_order = self.root / "wrong-order.csv"
        checking = SYNTHETIC_EXPORT.split("Umsätze Girokonto", 1)[1].split(
            "Umsätze Tagesgeld PLUS-Konto", 1
        )[0]
        wrong_order.write_text(
            "Bank;SYNTHETIC_BANK\nUmsätze Depot\n"
            "Buchungstag;Geschäftstag;Stück / Nom.;Bezeichnung;WKN;Währung;Ausführungskurs;Umsatz in EUR\n"
            "06.12.24;05.12.24;1;Fonds;ABC123;EUR;10,00;-10,00\n"
            "Umsätze Girokonto"
            + checking,
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            MultiAccountImportError, "IMPORT_SECTION_ORDER_INVALID"
        ):
            analyze_import_file(self.store, wrong_order)

        ambiguous = self.root / "ambiguous.csv"
        ambiguous.write_text(
            "Bank;SYNTHETIC_BANK\n"
            "Umsätze Girokonto\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "01.12.24;01.12.24;Entgelte;A;-1,00\n"
            "Umsätze Girokonto\n"
            "Buchungstag;Wertstellung (Valuta);Vorgang;Buchungstext;Umsatz in EUR\n"
            "02.12.24;02.12.24;Entgelte;B;-2,00\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MultiAccountImportError, "IMPORT_SECTION_AMBIGUOUS"):
            analyze_import_file(self.store, ambiguous)

    def test_section_duplicate_overlap_and_failure_are_isolated(self) -> None:
        analysis = self._analyze_and_map()
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
        confirm_empty_opening_security_positions(
            self.store,
            account_id="acc_brokerage",
            valuation_date="2024-11-30",
        )
        self.assertEqual(import_mapped_sections(self.store, analysis["analysis_id"])["status"], "COMPLETED")

        copy_path = self.root / "same-sections-new-name.csv"
        copy_path.write_text(
            "Export;copy\n" + SYNTHETIC_EXPORT,
            encoding="utf-8",
        )
        copied = analyze_import_file(self.store, copy_path)
        map_import_sections(
            self.store,
            copied["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": section["mapped_account_id"],
                    "action": "USE_EXISTING_ACCOUNT",
                }
                for section in copied["sections"]
            ],
        )
        duplicate = import_mapped_sections(self.store, copied["analysis_id"])
        self.assertEqual(duplicate["status"], "REVIEW_REQUIRED")
        self.assertTrue(
            all(
                "IMPORT_SECTION_DUPLICATE" in section["warnings"]
                for section in duplicate["section_results"]
            )
        )

        changed_path = self.root / "changed-section.csv"
        changed_path.write_text(
            SYNTHETIC_EXPORT.replace("Synthetischer Einkauf", "Geänderter Einkauf"),
            encoding="utf-8",
        )
        changed = analyze_import_file(self.store, changed_path)
        map_import_sections(
            self.store,
            changed["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": section["mapped_account_id"],
                    "action": "USE_EXISTING_ACCOUNT",
                }
                for section in changed["sections"]
            ],
        )
        overlap = import_mapped_sections(self.store, changed["analysis_id"])
        checking = next(
            item for item in overlap["section_results"] if item["section_type"] == "CHECKING"
        )
        self.assertIn("IMPORT_PERIOD_OVERLAP", checking["warnings"])

        invalid_path = self.root / "invalid-checking-section.csv"
        invalid_path.write_text(
            SYNTHETIC_EXPORT.replace("-36,85", "INVALID"),
            encoding="utf-8",
        )
        invalid = analyze_import_file(self.store, invalid_path)
        map_import_sections(
            self.store,
            invalid["analysis_id"],
            [
                {
                    "section_id": section["section_id"],
                    "account_id": section["mapped_account_id"],
                    "action": "USE_EXISTING_ACCOUNT",
                }
                for section in invalid["sections"]
            ],
        )
        isolated = import_mapped_sections(
            self.store, invalid["analysis_id"], import_mode="FORCE_REIMPORT"
        )
        self.assertEqual(isolated["status"], "PARTIALLY_COMPLETED")
        self.assertEqual(
            {item["section_type"]: item["status"] for item in isolated["section_results"]},
            {
                "CHECKING": "FAILED",
                "SAVINGS": "EMPTY_COMPLETED",
                "BROKERAGE": "IMPORTED",
            },
        )

    def test_brokerage_positions_are_reconciled_per_section(self) -> None:
        analysis = self._analyze_and_map()
        for account_id in ("acc_checking", "acc_savings"):
            record_opening_balance(
                self.store,
                account_id=account_id,
                balance_date="2024-11-30",
                booked_balance="0",
                available_balance=None,
                currency="EUR",
                source="MANUAL_ENTRY",
                confirmation=True,
                comment=None,
            )
        confirm_empty_opening_security_positions(
            self.store,
            account_id="acc_brokerage",
            valuation_date="2024-11-30",
        )
        import_mapped_sections(self.store, analysis["analysis_id"])
        record_closing_security_position(
            self.store,
            account_id="acc_brokerage",
            valuation_date="2024-12-31",
            security_identifier_type="WKN",
            security_identifier="ABC123",
            security_name="Synthetischer Fonds",
            quantity="10",
            confirmation=True,
        )
        result = reconcile_imported_security_positions(
            self.store,
            account_id="acc_brokerage",
            period_start="2024-12-01",
            period_end="2024-12-31",
        )
        self.assertEqual(result["status"], "MATCHED")
        brokerage_run = next(
            item
            for item in imported_section_runs(self.store).values()
            if item["section_type"] == "BROKERAGE"
        )
        self.assertEqual(result["section_id"], brokerage_run["section_id"])

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
            "CHECKING": "acc_checking",
            "SAVINGS": "acc_savings",
            "BROKERAGE": "acc_brokerage",
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
        application.command(
            "ConfirmEmptyOpeningSecurityPositions",
            {"account_id": "acc_brokerage", "valuation_date": "2024-11-30"},
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
        closing_position_id = application.command(
            "RecordClosingSecurityPosition",
            {
                "account_id": "acc_brokerage",
                "valuation_date": "2024-12-31",
                "security_identifier_type": "WKN",
                "security_identifier": "ABC123",
                "security_name": "Synthetischer Fonds",
                "quantity": "10",
                "confirmation": True,
            },
        )["result"]
        security_reconciliation = application.command(
            "ReconcileImportedSecurityPositions",
            {
                "account_id": "acc_brokerage",
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        self.assertEqual(security_reconciliation["status"], "MATCHED")
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
            application.query(
                "GetBankMonthlyExport", {"export_id": analyzed["export_id"]}
            )["data"]["status"],
            "COMPLETED",
        )
        self.assertEqual(
            len(application.query("ListImportSectionBindings")["data"]["bindings"]),
            3,
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
        self.assertEqual(
            application.query(
                "GetImportedSecurityPositionReconciliation",
                {"account_id": "acc_brokerage"},
            )["data"]["status"],
            "MATCHED",
        )
        manifest = application.query("GetCapabilityManifest")["data"]
        self.assertEqual(manifest["contract_version"], "1.3.0")
        self.assertTrue(manifest["capabilities"]["position_reconciliation_capability"])
        wizard = application.query(
            "GetImportWizardState", {"export_id": analyzed["export_id"]}
        )["data"]
        self.assertEqual(wizard["current_step"], 5)
        self.assertEqual(wizard["analysis"]["status"], "COMPLETED")
        self.assertEqual(
            application.query("GetImportHistory")["data"]["imports"][0][
                "export_id"
            ],
            analyzed["export_id"],
        )
        execution = application.query(
            "GetImportExecutionResult", {"export_id": analyzed["export_id"]}
        )["data"]
        self.assertEqual(execution["normalized_transaction_count"], 4)
        self.assertEqual(len(execution["section_results"]), 3)

        application.command(
            "DocumentBalanceDifference",
            {
                "reconciliation_id": difference["reconciliation_id"],
                "explanation": "Bankseitige Abschlussbuchung wird im Folgemonat gezeigt.",
            },
        )
        balance_context = application.query(
            "GetImportedPeriodReconciliationContext",
            {
                "account_id": "acc_checking",
                "section_id": difference["section_id"],
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["data"]
        self.assertEqual(len(balance_context["difference_explanations"]), 1)
        self.assertEqual(balance_context["reconciliation"]["status"], "DIFFERENCE")

        application.command(
            "CorrectSecurityPositionSnapshot",
            {
                "snapshot_id": closing_position_id,
                "quantity": "9",
                "reason": "Korrigierter Bankbestand",
            },
        )
        corrected = application.command(
            "ReconcileImportedPeriodPositions",
            {
                "account_id": "acc_brokerage",
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["result"]
        self.assertEqual(corrected["status"], "DIFFERENCE")
        position_context = application.query(
            "GetPositionReconciliation",
            {
                "account_id": "acc_brokerage",
                "section_id": corrected["section_id"],
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
            },
        )["data"]
        self.assertEqual(position_context["positions"][0]["quantity_difference"], "-1.00000000")
        self.assertTrue(
            any(
                item["event_type"] == "SecurityPositionSnapshotCorrected"
                for item in self.store.events()
            )
        )

        detail = application.query(
            "GetImportHistoryDetail", {"export_id": analyzed["export_id"]}
        )["data"]
        self.assertTrue(
            any(
                event["event_type"] == "BalanceDifferenceDocumented"
                for event in detail["audit_history"]
            )
        )

    def test_application_resolves_opaque_import_file_reference(self) -> None:
        application = FinanceApplicationService(
            self.store,
            import_file_resolver=lambda reference: (
                self.source
                if reference == "file_token_01"
                else self.root / "missing.csv"
            ),
        )
        analyzed = application.command(
            "AnalyzeImportFile",
            {
                "source_file_reference": "file_token_01",
                "requested_profile": "GermanMultiAccountCsvV1",
            },
        )["result"]
        self.assertEqual(analyzed["bank_identifier"], "SYNTHETIC_BANK")

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
        confirm_empty_opening_security_positions(
            self.store,
            account_id="acc_brokerage",
            valuation_date="2024-11-30",
        )
        mismatch = self.root / "synthetic-mismatch.csv"
        mismatch.write_bytes(
            SYNTHETIC_EXPORT.replace("Kauf Fonds WKN ABC123", "Kauf Fonds WKN WRONG1").encode(
                "cp1252"
            )
        )
        analysis = analyze_import_file(self.store, mismatch)
        account_by_type = {
            "CHECKING": "acc_checking",
            "SAVINGS": "acc_savings",
            "BROKERAGE": "acc_brokerage",
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
        import_mapped_sections(
            self.store, valid["analysis_id"], import_mode="FORCE_REIMPORT"
        )
        self.assertEqual(detect_investment_funding_relations(self.store), 1)
        relation_id = next(iter(investment_funding_relations(self.store)))
        reject_investment_funding_relation(self.store, relation_id)
        self.assertEqual(
            investment_funding_relations(self.store)[relation_id]["status"], "REJECTED"
        )


if __name__ == "__main__":
    unittest.main()
