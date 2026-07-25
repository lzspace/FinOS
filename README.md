# Agent OS – Finance Extension

This workspace contains the initial contracts and local-safety controls for the
Finance Extension described in `extensions/finance/README.md`.

No real bank exports, databases, receipts, tax documents, generated reports or
other personal finance data belong in this repository. Runtime data must be
stored in an OS-local application-data directory outside every Git worktree.

Run the repository checks with:

```bash
python -m pytest
python -m finance_extension.schema_check
python3 extensions/finance/tools/check_contracts.py
python -m finance_extension.repository_guard --all
ruff check src tests
```

## Installation and local keys

Python 3.11 or newer and Node.js are required. The release wheel is installed
without network access from a verified local artifact:

```bash
python3 -m venv .venv
.venv/bin/pip install --no-index /absolute/release/agent_os_finance-1.2.0-py3-none-any.whl
```

Production keys live in the OS credential store. The database identity is
`agent-os.finance/database`; the independent archive identity is
`agent-os.finance.backup/archive`. `FINANCE_TEST_KEY` and
`FINANCE_BACKUP_TEST_KEY` are accepted only by explicit synthetic test paths
and are not production key recovery mechanisms. Losing both the store key and
a usable archive key makes encrypted data unrecoverable.

## Agent OS Finance 1.2.0

Agent OS Finance 1.2.0 ist eine vollständig lokal betriebene persönliche
Finanzanwendung. Sie unterstützt das definierte CSV-Importformat,
Transaktionsklassifikation, Dubletten-, Transfer- und Erstattungsbereinigung,
wiederkehrende Zahlungen, Cashflow- und Monatsprognosen, Konten, Salden,
Liquidität, Nettovermögen sowie verschlüsselte Backups und Wiederherstellung.

Alle Finanzdaten verbleiben auf dem lokalen Gerät. Netzwerkzugriff,
Cloud-Synchronisation, externe KI, externe OCR und Git-Ablage produktiver
Finanzdaten sind technisch und vertraglich ausgeschlossen. Vollständige
Supportgrenzen stehen in [`SUPPORT.md`](SUPPORT.md), der finale Abnahmelauf in
[`ACCEPTANCE.md`](ACCEPTANCE.md) und die Änderungen in
[`RELEASE_NOTES.md`](RELEASE_NOTES.md).

## Vertical Slices 0.2.0 through 1.2.0

The first executable slice supports only `GenericFinanceCsvV1`:

```text
booking_date,value_date,amount,currency,counterparty,description
2026-07-01,2026-07-01,-42.80,EUR,Supermarkt Beispiel,Lebensmittel
```

It persists an encrypted local SQLite snapshot and encrypted original import;
no plaintext finance database is written to disk. Provision a Fernet key in the
OS credential store under service `agent-os.finance`, username `database`, then:

```bash
finance --data-dir /absolute/local/finance-data import /absolute/input.csv --account acc_01
finance --data-dir /absolute/local/finance-data cashflow --month 2026-07
```

Version 1.1.0 adds the file-centric German multi-account
profile `GermanMultiAccountCsvV1`. One monthly file belongs to exactly one
bank and contains ordered `CHECKING`, `SAVINGS` and `BROKERAGE` sections.
Sections are mapped, normalized, deduplicated and reconciled independently;
the file receives an aggregate status. Confirmed bank/title/type/account
bindings are proposed again on later monthly imports but remain visible for
review. CP1252/UTF-8, semicolon delimiters, German dates and decimal values are
supported:

```bash
finance --data-dir /absolute/local/finance-data import analyze /absolute/export.csv \
  --bank BANK_A
finance --data-dir /absolute/local/finance-data import map-sections \
  --analysis analysis_HASH \
  --section section_CHECKING=acc_checking \
  --section section_SAVINGS=acc_savings \
  --section section_BROKERAGE=acc_brokerage
finance --data-dir /absolute/local/finance-data balance opening record \
  --account acc_checking --date 2024-11-30 --amount 2450.83 --source manual
finance --data-dir /absolute/local/finance-data import execute \
  --analysis analysis_HASH
finance --data-dir /absolute/local/finance-data reconcile balance \
  --account acc_checking --from 2024-12-01 --to 2024-12-31
```

Unknown sections must be explicitly skipped with `section_ID=SKIP`; they are
never interpreted heuristically. A section duplicate is detected from account,
full reporting month, section type and content hash even when the filename
changes. Same-type sections require a stable account reference. Cash balances
and brokerage positions are reconciled per section, and cross-account funding
relations are detected only after section import. Checked-in parser fixtures
are synthetic.

Version 1.2.0 binds this workflow completely to the local React UI. Its exact
five-step assistant supports restart-safe continuation, explicit section and
opening-value review, a complete pre-import preview, durable results and
history, investment-funding decisions, balance/position reconciliation and
auditable corrections. React receives only an opaque host file reference,
never a local path, store handle or encryption key.

The test-only `FINANCE_TEST_KEY` environment variable is intentionally limited
to synthetic test data. Cashflow treats every positive amount as income and
every negative amount as an expense; transfers, investments and refunds are
not yet distinguished.

Version 1.4.0 unifies period control and the account workspace. `finance
account overview`/`finance account detail` and the `ListAccountOverviews` /
`GetAccountDetail` queries accept an explicit `MONTH`, `YEAR` or
`CUSTOM_RANGE` period instead of a free month string; the same period drives
the account overview, transactions, categories and wealth views, while an
import's own report month stays immutable. Each account now exposes a full
workspace — transactions, balances (with carry-forward and adjustment reasons
visible), period reconciliations, import history, brokerage positions and an
account-scoped audit trail — through eleven new or extended versioned
queries. Every query response carries `projection_version`,
`freshness_status` and `generated_at`; the capability manifest reports
`product_version`, `contract_version`, `store_schema_version` and
`ui_contract_version` explicitly, and Settings renders them from the runtime
manifest only. The store schema remains 3 since every new view is
reconstructed live from existing events.

```bash
finance --data-dir /absolute/local/finance-data period available
finance --data-dir /absolute/local/finance-data account overview \
  --period-mode MONTH --year 2026 --month 7
finance --data-dir /absolute/local/finance-data account detail \
  --account acc_checking --period-mode MONTH --year 2026 --month 7
```

Version 0.3.0 adds deterministic, event-sourced classification with stable
category codes. It deliberately contains no AI or probabilistic model.
Confirmed user decisions are authoritative; unmatched and conflicting results
remain visibly `UNCLASSIFIED`.

```bash
finance --data-dir /absolute/local/finance-data classify --month 2026-07
finance --data-dir /absolute/local/finance-data review classifications
finance --data-dir /absolute/local/finance-data classification confirm \
  --transaction txn_01 --category FOOD_GROCERIES
finance --data-dir /absolute/local/finance-data classification reject \
  --transaction txn_01
finance --data-dir /absolute/local/finance-data classification create-rule \
  --field counterparty --operator CONTAINS --value Supermarkt \
  --category FOOD_GROCERIES --priority 200
finance --data-dir /absolute/local/finance-data category-breakdown --month 2026-07
```

Adding `--create-rule-from counterparty` or
`--create-rule-from normalized_description` to a confirmation creates a
versioned future rule from that user decision.

Version 0.4.0 adds event-sourced duplicate, internal-transfer and refund
relations. The original cashflow remains available; reconciled projections
make gross values, exclusions, refunds and effective values explicit.

```bash
finance --data-dir /absolute/local/finance-data reconcile --month 2026-07
finance --data-dir /absolute/local/finance-data review duplicates
finance --data-dir /absolute/local/finance-data review transfers
finance --data-dir /absolute/local/finance-data review refunds
finance --data-dir /absolute/local/finance-data cashflow --month 2026-07 --reconciled
finance --data-dir /absolute/local/finance-data category-breakdown --month 2026-07 --reconciled
```

Version 0.5.0 adds deterministic recurring-payment detection, expected
transactions, three month-end forecast scenarios and forecast evaluation. No
statistical or AI model is used. Variable expenses use the median of complete,
historical, reconciled monthly expenses after confirmed recurring source
transactions have been removed.

```bash
finance --data-dir /absolute/local/finance-data recurring detect \
  --from 2025-01 --to 2026-07
finance --data-dir /absolute/local/finance-data review recurring
finance --data-dir /absolute/local/finance-data recurring confirm \
  --pattern pattern_01
finance --data-dir /absolute/local/finance-data forecast create --month 2026-08
finance --data-dir /absolute/local/finance-data forecast show --month 2026-08
finance --data-dir /absolute/local/finance-data forecast evaluate --month 2026-07
```

Version 0.6.0 adds the local React desktop surface. Components communicate
only with `FinanceApplicationService` through a narrow host-provided IPC bridge;
they cannot access SQLite, encryption keys, snapshots, source files or the
event append API. Navigation is derived from the capability manifest, and all
displayed finance values come from query projections.

For local UI development with synthetic preview data:

```bash
cd ui
npm ci
npm test
npm run build
npm run check:offline
```

The production desktop host injects `window.__FINANCE_IPC__` with `query`,
`command` and controlled local file-selection methods. The standalone browser
preview deliberately contains synthetic projections only. No public HTTP API,
external fonts, analytics, remote assets or external models are used.

Version 0.7.0 introduces explicit accounts and balance facts. Reported,
manual, calculated and reconciled balances remain distinguishable; corrections
append replacement events instead of mutating snapshots. Liquidity, assets,
liabilities, net worth and projected month-end balances are deterministic
projections with visible dates, freshness and currency conflicts.

```bash
finance --data-dir /absolute/local/finance-data account create \
  --id acc_main --name Girokonto --type CHECKING --institution "Lokale Bank"
finance --data-dir /absolute/local/finance-data balance record \
  --account acc_main --date 2026-07-20 --booked 1250.00 --available 1200.00
finance --data-dir /absolute/local/finance-data balance reconcile --account acc_main
finance --data-dir /absolute/local/finance-data liquidity
finance --data-dir /absolute/local/finance-data net-worth
```

Version 0.8.0 adds local disaster recovery: complete authenticated backups,
pre-restore integrity and compatibility validation, atomic full-store restore,
portable encrypted exports, key rotation with a mandatory recovery backup, and
versioned store migrations with downgrade protection. Database and archive keys
use separate Keychain identities (`agent-os.finance/database` and
`agent-os.finance.backup/archive`). Partial restoration and cloud targets are
deliberately unsupported.

```bash
finance --data-dir /absolute/local/finance-data backup create
finance --data-dir /absolute/local/finance-data backup list
finance --data-dir /absolute/local/finance-data backup verify /absolute/backup.finance-backup
finance --data-dir /absolute/local/finance-data backup restore /absolute/backup.finance-backup
finance --data-dir /absolute/local/finance-data data export
finance --data-dir /absolute/local/finance-data store validate
finance --data-dir /absolute/local/finance-data store migrations
finance --data-dir /absolute/local/finance-data key status
finance --data-dir /absolute/local/finance-data key rotate
```

Version 0.9.0 hardens startup and delivery. A sibling workspace lock permits
one writer, stale locks require explicit recovery, archive bombs and symlink
paths are blocked, migrations retain the encrypted pre-migration snapshot, and
startup fails closed when store, schema or UI bundle integrity differs from the
embedded release manifest. Diagnostics remain encrypted and contain only
allowlisted operational metadata.

Release construction performs two isolated wheel builds with a fixed source
epoch and rejects differing SHA-256 hashes. It emits CycloneDX and SPDX SBOMs,
a release manifest, a contract catalog and Ed25519 signatures. A release
signing key must be supplied explicitly; the build never invents a production
trust root.

```bash
finance-release --output /absolute/release \
  --signing-key /secure/offline/release-ed25519.pem \
  --python-tests 90 --frontend-tests 5 --schemas 80 \
  --acceptance-report /absolute/acceptance-report.json \
  --critical-findings 0 --high-findings 0 \
  --offline-verified
```

The product and build require no network access. For startup failures, do not
delete lock or data files manually: use the read-only status, then follow
[`RECOVERY.md`](RECOVERY.md). Security assumptions are documented in
[`SECURITY.md`](SECURITY.md) and [`THREAT_MODEL.md`](THREAT_MODEL.md).

## Betrieb, Diagnose und Grenzen

Der erste Import erfolgt mit `finance ... import`; unmittelbar danach sollte
ein unabhängiges lokales Backup erstellt und testweise verifiziert werden.
Schlüsselrotation erzeugt vorher zwingend ein Recovery-Backup. Migrationen und
vollständige Restores folgen den verlinkten Runbooks. Stabile Fehlercodes und
explizit exportierte, finanzdatenfreie Diagnostik sind die einzigen vorgesehenen
Supportdaten.

Zur vollständigen Datenlöschung alle Finance-Prozesse beenden, den exakten
lokalen Daten-, Backup- und Exportpfad prüfen, anschließend diese Verzeichnisse
über die Betriebssystemfunktion sicher entfernen und die beiden Finance-Einträge
im Keychain löschen. Dieser irreversible Schritt ist bewusst kein pauschaler
CLI-Befehl.

Bekannte Grenzen: kein Cloud-Sync, keine Bank-API, keine Steuer- oder
Belegfunktion, kein OCR, keine externe KI, keine Mehrbenutzernutzung und keine
Wiederherstellung ohne mindestens einen passenden Schlüssel. Details zum
lokalen Datenpfad, Sicherheitsmodell und Incident-Verhalten stehen in
[`SECURITY.md`](SECURITY.md).
## Desktop-Host

Der produktive Realdatenimport läuft ausschließlich im Tauri-Desktop-Host. Vor
dem Laden der React-Anwendung stellt er `window.__FINANCE_IPC__` bereit. Die
Oberfläche erhält bei der nativen CSV-Auswahl nur eine kurzlebige
`file_ref`, den Anzeigenamen und die Dateigröße. Der geprüfte lokale Pfad
verbleibt im Rust-Host und wird nur über ein zeilenbasiertes
stdin/stdout-Protokoll an den lokalen Python-Application-Service übergeben.

`npm run dev` bleibt eine deutlich gekennzeichnete UI-Vorschau mit
ausschließlich synthetischen Daten. Ohne Desktop-Bridge zeigt ein
Produktionsbundle `DESKTOP_BRIDGE_UNAVAILABLE`; es fällt nicht auf Mockdaten
oder direkten Datenzugriff zurück. Der E2E-Fixture-Registrar wird nur mit dem
Rust-Feature `e2e-fixtures` kompiliert.
