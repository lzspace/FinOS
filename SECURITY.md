# Security policy

Agent OS Finance is a single-user, local-only application. Finance data,
decrypted imports, keys, backups and diagnostics must stay outside Git and
outside cloud-synchronized or network-mounted directories. Runtime network
egress, telemetry, remote fonts, external AI and external banking access are
not part of the trust model.

The encrypted store uses an OS-credential-store key. Backups use an independent
local key and authenticated encryption. One process owns the writer lock;
read-only lock inspection is always permitted. A lock is never removed merely
because its process appears absent: recovery requires the stale state and exact
instance identifier.

Release consumers must verify the release manifest and Ed25519 signatures
against a separately trusted public-key fingerprint. At startup the application
recomputes hashes for the embedded JSON Schemas and UI bundle and blocks normal
finance views on mismatch. Production release keys must not be committed,
copied into the wheel or generated implicitly by the build.

Report suspected vulnerabilities privately to the repository owner. Do not
attach real finance data, keys, decrypted archives or full diagnostic stores.
Include only the stable error code, application version and explicitly exported
allowlisted diagnostic metadata.

Supported security updates cover the current `1.1.x` release line. Older
versions should be upgraded through the documented migration and recovery
procedure rather than opened after a newer store has been written.

The point-in-time 1.0.0 review and dependency-audit evidence is summarized in
[`SECURITY_REVIEW.md`](SECURITY_REVIEW.md).
