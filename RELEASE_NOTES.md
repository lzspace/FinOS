# Agent OS Finance 1.1.0

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
