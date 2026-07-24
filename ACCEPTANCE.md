# 1.1.0 release acceptance

The authoritative end-to-end command is `finance-acceptance` from the installed
Wheel. It refuses to claim Wheel acceptance when loaded from the repository
source tree. During the run DNS resolution is replaced with a failing guard.

The scenario initializes a new encrypted workspace, creates checking, savings,
credit-card and brokerage accounts, imports synthetic generic CSV history and
a synthetic CP1252 German multi-account export, confirms opening/closing
balances and an investment-funding relation, classifies and corrects a user decision,
confirms duplicate/transfer/refund relations, detects and confirms recurring
patterns, creates all three forecast scenarios, records balances/assets/
liabilities, restarts and rebuilds, creates a complete encrypted backup,
restores into a new workspace, compares projections, rotates the store key and
finishes with a full integrity check.

The canonical comparison includes:

- last sequence, event count and active aggregate versions;
- normalized transactions and active classifications;
- confirmed reconciliation relations and reconciled transaction state;
- recurring patterns and forecast history;
- accounts and active balance snapshots;
- liquidity, net worth and asset allocation.
- bank-month exports, section mappings and persistent bindings, section runs,
  opening/closing balances, security transactions and positions,
  investment-funding relations, cash reconciliation and brokerage-position
  reconciliation.

Only the destination encryption key, archive identifiers, backup timestamps and
temporary workspace paths may differ. None are part of the canonical projection
snapshot. A successful report is bound to the accepted Wheel through
`wheel_sha256` and contains `source: INSTALLED_WHEEL`,
`restart_rebuild_equal: true`, `restore_equal: true`, `key_rotation: ROTATED`,
`integrity: VALID` and a SHA-256 projection fingerprint.

Release gates additionally run all unit/schema/UI/guard checks, the immutable
0.2.0–1.0.0 migration fixture matrix, the 10,000-row capacity test, two
reproducible builds, signed-set tamper tests and an isolated `--no-index`
installation from a prepared local wheelhouse.
