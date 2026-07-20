# Finance Extension contracts and Vertical Slice v0.3.0

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
- `examples/`: synthetic envelopes for integration tests and documentation.

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
