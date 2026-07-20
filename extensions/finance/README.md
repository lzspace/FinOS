# Finance Extension contracts and Vertical Slice v0.6.0

This directory turns the agreed finance domain model into executable interface
contracts. It deliberately contains no production finance data and has no
network dependency.

Version 0.2.0 adds an executable local vertical slice in the repository-level
`src/finance_extension/` package: strict `GenericFinanceCsvV1` input, immutable
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
- `schemas/*.response.schema.json`: executable 0.6.0 query-response contracts
  for capabilities, runtime security, dashboard, transactions, reviews,
  recurring patterns and forecasts.
- `examples/`: synthetic envelopes for integration tests and documentation.

## Local UI boundary

The source is in `ui/`. `npm run generate:contracts` verifies the nine
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
