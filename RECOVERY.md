# Recovery runbook

1. Stop all Finance processes. Inspect the workspace lock and record its PID,
   start time and instance ID. Do not delete it automatically.
2. If the PID still exists, close that process. If it does not exist, invoke
   explicit stale-lock recovery with the exact inspected instance ID.
3. Check key status. A wrong database key must not be replaced blindly. Locate
   the independent archive key and a complete backup.
4. Verify the candidate archive. Authentication, archive limits, manifest,
   application version, schema, every file hash, SQLite integrity, event hashes
   and import hashes must all pass before restoration.
5. Ensure at least enough local free space for the current workspace, staged
   replacement and rollback copy. Never restore inside the current data folder.
6. Perform a complete restore. The implementation stages beside the workspace,
   swaps with atomic renames, opens and validates the new store, and rolls back
   the original on any failure. Partial restore is unsupported.
7. Rebuild projections, compare event sequence and run the integrity query.
   Keep the pre-recovery backup until normal operation is confirmed.

For `BUNDLE_TAMPERED` or `INCOMPATIBLE_VERSION`, preserve the data directory and
install a correctly signed compatible wheel. For `MIGRATION_FAILED`, the
encrypted pre-migration snapshot remains authoritative; do not manually edit
schema metadata. For total loss of both store and archive keys there is no
cryptographic recovery route.
