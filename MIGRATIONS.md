# Migration policy and matrix

Migrations are monotonic, logged and fail closed. Each source is first opened
with its original key, migrated in memory, fully validated, then persisted as
one authenticated snapshot. An exception before persistence leaves the prior
encrypted bytes unchanged. Stores newer than schema 3 and archives requiring a
newer application are rejected; downgrade writes are forbidden.

| Application source | Source store | Route to 1.1.0 / schema 3 | Verification |
|---|---:|---|---|
| 0.2.0 | legacy 1 | 1 → 2 → 3 | events, imports, cashflow rebuild |
| 0.3.0 | legacy 1 | 1 → 2 → 3 | classifications and rule history |
| 0.4.0 | legacy 1 | 1 → 2 → 3 | duplicate/transfer/refund chains |
| 0.5.0 | legacy 1 | 1 → 2 → 3 | recurring patterns and forecasts |
| 0.6.0 | legacy 1 | 1 → 2 → 3 | UI projections and sequences |
| 0.7.0 | legacy 1 | 1 → 2 → 3 | accounts, balances and wealth |
| 0.8.0 | 2 | 2 → 3 | backups, key state and recovery |
| 0.9.0 | 3 | no-op | migration idempotency |
| 1.0.0 | 3 | no-op | additive 1.1 import events rebuild |

The checked-in synthetic fixture catalog at
`tests/fixtures/migrations/supported_versions.json` is the immutable source for
all migration-entry tests. Product 1.1.0 intentionally keeps store schema 3;
the additive import contracts advance independently to version 1.2.0 and do
not rewrite existing events.

Version 3 adds release metadata without rewriting events. Old version-1 and
version-2 backups remain importable after complete verification and are then
migrated. Migration steps preserve event IDs, aggregate versions, projection
rebuild results, forecast versions and correction chains. Any future change to
that guarantee requires a declared major contract version.
