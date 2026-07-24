# Finance Extension contracts v1.1.0

This directory turns the agreed finance domain model into executable interface
contracts. It deliberately contains no production finance data and has no
network dependency.

Version 0.2.0 adds an executable local vertical slice in the repository-level
`src/finance_extension/` package: strict `GenericFinanceCsvV1` and
`GermanMultiAccountCsvV1` input, immutable
raw events, normalized transactions and a monthly cashflow projection. The
encrypted store and import files are deliberately outside this repository.

Version 0.3.0 adds deterministic classification rules, explicit user review,
immutable classification decisions and category breakdowns reconstructed from
events. Stable technical category codes are defined in
`src/finance_extension/categories.py`; display names may be localized later.

Version 0.4.0 adds append-only duplicate, transfer and refund relations plus
reconciled monthly cashflow and category projections. Gross and effective
values remain separately reconstructable from the event stream.

Version 0.5.0 adds deterministic recurring-pattern detection, versioned user
decisions, expected-transaction matching, three forecast scenarios and
event-sourced forecast evaluation. Paused, ended and irregular patterns are
not forecast-relevant. Forecast history remains immutable.

Version 0.6.0 adds a capability-driven React desktop UI and an explicit local
Application API. Query responses use versioned Draft 2020-12 schemas and
generated TypeScript contracts. The host boundary is deliberately narrow:
React uses Desktop IPC, IPC invokes named commands or projection queries, and
no UI component can append events or reach protected storage directly.

Version 0.7.0 adds event-sourced accounts, immutable balance snapshots,
explicit balance reconciliation, asset and liability valuations, consolidated
liquidity and net-worth projections, and a balance-based month-end forecast.
Reported and calculated balances are never conflated. Foreign currencies stay
visible as conflicts until an explicit valuation rate is available.

Version 0.8.0 adds authenticated local backup and portable export archives,
complete pre-restore verification, atomic rollback-safe restoration, independent
archive-key handling, encryption-key rotation, store integrity checks and
versioned migration history. Newer stores and archives are rejected by older
software; partial restoration and cloud destinations are forbidden.

Version 0.9.0 adds single-writer coordination, explicit stale-lock recovery,
archive resource limits, atomic migration rollback, startup integrity checks,
contract compatibility classification, encrypted minimal diagnostics and a
reproducible signed release pipeline with two SBOM formats.

Version 1.0.0 freezes the MVP feature set and adds the Wheel-only acceptance
runner, complete signed release-set verification, measured capacity boundaries
and final support/release documentation. Store schema 3, event schema 1.0.0 and
contract package 1.1.0 remain independently versioned.

Version 1.1.0 defines `GermanMultiAccountCsvV1` as one monthly export from one
bank containing ordered `CHECKING`, `SAVINGS` and `BROKERAGE` sections. The
`BankMonthlyExport` aggregate owns the source hash and aggregate result, while
every `BankAccountSection` carries its own type, account mapping, content hash,
status and cash-balance or security-position reconciliation. Confirmed local
bindings are reused as visible proposals; renamed files do not bypass
section-level duplicate and overlap checks.

## Binding security invariants

- `storage_scope` is `LOCAL_DEVICE_ONLY`.
- `trust_zone` is `PRIVATE_LOCAL`.
- network egress, cloud sync, external model processing and Git storage are
  denied.
- runtime data must resolve outside every Git worktree.
- raw imports and raw transactions are immutable.
- user-confirmed decisions cannot be overwritten automatically.
- every state change carries correlation, causation, idempotency and audit
  metadata.

The JSON contracts use decimal strings such as `"-120.50"` for monetary
amounts. Consumers must parse these with a decimal/fixed-point implementation,
never binary floating point.

## Contract files

- `schemas/common.schema.json`: shared IDs, security context, actor, money and
  rule primitives.
- `schemas/commands.schema.json`: discriminated command envelopes and concrete
  payloads for all commands in specification v0.1.
- `schemas/events.schema.json`: append-only domain-event envelopes and concrete
  payload families.
- `schemas/classification_events.schema.json`: executable 0.3.0 classification
  event contracts.
- `schemas/reconciliation_commands.schema.json` and
  `schemas/reconciliation_events.schema.json`: executable 0.4.0 relationship
  contracts.
- `schemas/forecast_commands.schema.json` and
  `schemas/forecast_events.schema.json`: executable 0.5.0 recurring and
  forecast contracts.
- `schemas/*.response.schema.json`: executable query-response contracts since 0.6.0
  for capabilities, runtime security, dashboard, transactions, reviews,
  recurring patterns and forecasts.
- `schemas/account_commands.schema.json` and
  `schemas/account_events.schema.json`: executable 0.7.0 contracts for account,
  balance, asset and liability changes.
- the 0.7.0 account, liquidity, net-worth and projected-balance response
  schemas extend the local Application API without adding a public HTTP API.
- `schemas/recovery_commands.schema.json` and the backup, integrity, key and
  migration response schemas define the local 0.8.0 disaster-recovery boundary.
- `examples/`: synthetic envelopes for integration tests and documentation.

## Local UI boundary

The source is in `ui/`. `npm run generate:contracts` verifies the selected
versioned response schemas before generating the TypeScript contract module.
`npm run build` creates a relative-path production bundle, and
`npm run check:offline` rejects external resource candidates. Production uses
a desktop-host injection named `window.__FINANCE_IPC__`; the in-browser preview
adapter only returns synthetic data.

Schemas use JSON Schema Draft 2020-12. Schema version and policy version are
separate: an interface can remain at schema `1.0.0` while a classification or
calculation policy advances independently.

## Local storage

The default runtime root is selected by the host application, for example:

- macOS: `~/Library/Application Support/Agent OS/finance`
- Linux: `~/.local/share/agent-os/finance`
- Windows: `%LOCALAPPDATA%\\AgentOS\\finance`

The host must pass the fully resolved path through `local_storage_guard.py`
before creating a database, copy, cache, report or temporary file. The guard
rejects repository paths and common cloud-sync locations.

## Repository protection

`.gitignore` blocks finance file formats as a fallback. The repository guard is
the enforcing control for commits. To use it in a future Git repository:

```bash
git config core.hooksPath .githooks
```

Only fixtures under `tests/fixtures/synthetic/` containing the explicit marker
`SYNTHETIC_TEST_DATA` may use otherwise blocked data-file extensions.
