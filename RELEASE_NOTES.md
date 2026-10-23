# Agent OS Finance 1.5.0

The transaction area is now the primary daily workspace. Its stable table
combines merchant, description, category, account, amount and decision status,
with composable filters for period, account, category, direction, amount,
classification, local-AI suggestions and import provenance. Opening a
transaction keeps that table context visible in a keyboard-accessible side
panel, including relations, import provenance and audit history. Category and
AI decisions update immediately. Safe bulk category and suggestion review are
available, while relations and balance corrections deliberately remain
single-record decisions.

The account area is now the central operational workspace. Money accounts
show reported and calculated balances separately alongside income, expenses,
net cashflow, last import and the account-specific slice of the canonical
review inbox. Brokerage accounts instead lead with positions and their latest
position reconciliation. Carry-forward balances expose their confirmed source
period and any manual adjustment, while transactions, imports, balances,
categories and audit events link to one another. Closed accounts remain
available in an explicit read-only view.

The Review Center consolidates unresolved categories, local AI suggestions,
balance issues, incomplete imports, transaction and investment relations, and
import-format changes in the rebuildable `GetReviewInbox` projection. The
navigation badge, dashboard count, type summaries, and task list use that same
projection; resolved decisions disappear without a second persisted source of
truth. Each task carries a typed action target into the affected transaction,
account, category suggestion, or import. Navigation is reduced to the primary
finance workflows, and technical states now share one user-facing German
status vocabulary while internal codes remain under technical details.

Version 1.5.0 adds the offline AI import-mapping slice. Unknown tabular bank
exports receive a content-free structural fingerprint; exact confirmed
profiles are reused without an AI call, while relevant format changes are
classified and audited. An optional, explicitly enabled compatibility rule can
accept additive columns only after a successful full-file dry run.

The local model receives only the detected structure and at most five
deterministically selected, redacted sample rows. Its strictly structured
proposal is limited to an allowlist of target fields and transformations. It
cannot import, append events or create a profile. The UI exposes a fully
editable mapping table, automatic local revalidation, explicit warning
confirmation and manual mapping when the model is unavailable. Confirmation
creates an immutable, versioned profile; proposal, validation, user diff,
model, policy and fingerprint versions remain auditable across restart,
backup and restore.

Category Shadow Mode adds a strictly separate local evaluation layer for
ordinary debit and credit transactions. It sends only redacted merchant and
description text, direction, currency and an amount bucket to the registered
local model. Predictions never change classifications, cashflow, reports,
forecasts or rules. Historical user-confirmed categories can be evaluated in
restart-safe, manually resumable runs; model, policy and taxonomy versions are
kept in separate metric groups. Accuracy, precision, recall and confidence
threshold metrics remain hidden as insufficient until at least 30 applicable
samples exist. Shadow history and evaluation runs survive restart, backup and
restore.

Category Suggestion Review makes local-model proposals visible for genuinely
unresolved transactions while preserving deterministic and user decisions as
higher-priority sources. Suggestions remain non-effective until the user
confirms or corrects them; rejection leaves the transaction unclassified and
there is no confidence-based auto-accept. The category area now provides an
explicit review queue, controlled reason codes, alternatives, model confidence,
optional historically measured accuracy and an opt-in action for creating a
deterministic rule from the decision. Pending suggestions are immutable,
restart-safe and become stale or are versionedly superseded when their
transaction, taxonomy, inference policy or model basis changes.

Inference policy 1.1.0 changes the productive llama.cpp structured-output
backend from the experimental `--json-schema` sampler to controlled GBNF.
Reasoning is disabled for health checks and finance inference. GBNF only
constrains the generated JSON shape: parsing, Draft 2020-12 validation and the
finance semantic validator remain mandatory and authoritative. Grammar,
parsing, schema, semantic and timeout failures retain stable error codes and
fall back to manual import mapping without affecting the Finance Core.

llama.cpp runtime discovery no longer depends on the shell `PATH`. A signed
runtime bundled with the macOS application is preferred; development builds
may use an explicitly configured binary, and users can register a controlled
external fallback through the native file picker. The host passes only an
opaque reference to React. File type, symlink status, executable permission,
SHA-256, version and minimum compatible build are verified before use and on
every restart. Missing or invalid runtimes disable only Local AI; all Finance
Core functions remain available.

The product and additive application/UI contracts are 1.5.0. The physical
store schema remains 3 because the new state is represented by versioned
events and existing encrypted import-file storage.

## 1.4.0

Version 1.4.0 unifies period selection and the account workspace. A central,
typed period control (`MONTH`, `YEAR`, `CUSTOM_RANGE`) replaces the free month
text input and drives the account overview, transactions, categories and
wealth views; an import's own report month remains immutable. `QUARTER` and
`ALL_TIME` modes are reserved internally but not yet exposed.

The account overview and a full seven-tab account workspace (Übersicht,
Transaktionen, Salden, Abgleiche, Importe, Positionen for brokerage accounts,
Audit) are backed by eleven new or extended versioned queries
(`GetAvailablePeriods`, `ListAccountOverviews`, `GetAccountDetail`,
`GetAccountPeriodSummary`, `ListAccountTransactions`,
`ListAccountReconciliations`, `ListAccountImports`, `ListAccountPositions`,
`GetAccountPositionHistory`, `GetAccountAuditTrail`, plus the existing
`GetAccountBalanceHistory`). Reported and calculated balances stay clearly
separated everywhere; brokerage accounts show positions instead of a checking
balance. Every response envelope now carries `projection_version`,
`freshness_status` and `generated_at` alongside the existing
`projection_sequence`/`event_store_sequence` pair.

The capability manifest explicitly reports `product_version` (1.4.0),
`contract_version` (1.4.0, additive), `store_schema_version` (unchanged at 3)
and `ui_contract_version` (1.4.0) plus per-projection versions; Settings
renders all of them from the runtime manifest, never hardcoded. Because no new
physical store structure or reinterpreted event payload is required, the
store schema stays 3 and every new view is reconstructed live from existing
events rather than a persisted, potentially lagging projection.

## 1.2.0

Version 1.2.0 completes the local React integration for
`GermanMultiAccountCsvV1`. The import area is now a projection-backed,
restart-safe five-step assistant from opaque host file selection through
analysis, account mapping, opening values, complete preview, explicit
confirmation, execution, relation review and per-section reconciliation.

New read models provide wizard state, durable execution results, import
history/detail, balance context and position context. Security-position
corrections and explanations for balance differences append new versioned
events; historical events remain immutable. The UI exposes partial, stale,
locked, incompatible, validating and executing states and has been verified at
390 px without horizontal overflow.

The store remains at schema 3; the additive contract package is 1.3.0. File paths,
store handles and encryption keys do not cross into React. Runtime remains
offline, and all fixtures are synthetic.

## 1.1.0

Version 1.1.0 adds the explicit, file-centric `GermanMultiAccountCsvV1` profile
for local German bank exports. Each export represents exactly one bank and one
calendar month with ordered checking, savings and brokerage sections. It
analyzes CP1252 or UTF-8 semicolon files before import, requires visible account
mapping and confirmed opening values, persists reusable bank/section/account
bindings, handles empty sections and isolates section failures. Cash and
security transactions remain separate event streams.

Opening and reported closing balances remain independent snapshots. Period
reconciliation reports calculated and reported balances plus any difference
without correcting it. Matching checking debits and brokerage purchases can be
confirmed as `INVESTMENT_FUNDING`, which keeps both account-level records while
excluding the pure asset transfer from consumption cashflow.

Duplicate and overlap protection is evaluated for every account section from
account, complete reporting month, section type and content hash. A renamed
file therefore cannot silently re-import the same section. Overall imports use
`COMPLETED`, `PARTIALLY_COMPLETED`, `REVIEW_REQUIRED` or `FAILED`; each section
uses `IMPORTED`, `EMPTY_COMPLETED`, `SKIPPED`, `REVIEW_REQUIRED` or `FAILED`.
Brokerage closing positions are reconciled independently from cash balances.

The store stays at schema 3. Contract package 1.2.0 is additive and remains
compatible with existing 1.0.0 workspaces. No real bank file is included in
tests, source archives or release artifacts.

Supported import profiles:

- `GenericFinanceCsvV1`
- `GermanMultiAccountCsvV1` version 1.0.0

Not included are bank connectivity, heuristic CSV detection, PDF/OCR, live
prices, foreign-currency security valuation, taxes or cloud synchronization.

## 1.0.0

Agent OS Finance 1.0.0 is a fully local personal-finance MVP. It imports the
defined generic CSV format and provides deterministic transaction
classification, duplicate/transfer/refund reconciliation, recurring-payment
detection, cashflow and monthly forecasts, accounts, balances, liquidity, net
worth, encrypted backups, restore and key rotation.

The release adds no new financial domain beyond 0.9.0. Its changes are release
qualification: a Wheel-contained end-to-end acceptance runner, a verifier for
the complete signed artifact set, immutable migration-entry fixtures, measured
capacity boundaries, checksums and final operations/support documentation.

All financial data stays on the local device. Runtime network access, cloud
sync, external AI/OCR and Git storage of productive finance data are excluded.

## Compatibility

- Product: 1.0.0
- Store: 3 (migration inputs 1–2)
- Event schema: 1.0.0
- Contract package: 1.1.0, unchanged from 0.9.0
- Import profile: `GenericFinanceCsvV1`

## Post-MVP roadmap

- 1.1.x: additional CSV import profiles
- 1.2.x: budgets and financial goals
- 1.3.x: improved local analysis and anomaly detection
- 1.4.x: optional local model operation
- later: tax support as a separate optional extension with independent rules,
  contracts, security reviews and release cycle
