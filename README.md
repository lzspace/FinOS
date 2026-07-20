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

## Vertical Slices 0.2.0 through 0.6.0

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

The test-only `FINANCE_TEST_KEY` environment variable is intentionally limited
to synthetic test data. Cashflow treats every positive amount as income and
every negative amount as an expense; transfers, investments and refunds are
not yet distinguished.

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
