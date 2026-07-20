# Release procedure 0.9.0

1. Use the exact Python and npm dependency versions declared in
   `pyproject.toml`, `package.json` and `package-lock.json`; perform the UI build
   and offline-resource check.
2. Run all Python tests, UI tests, Draft-2020-12 schema checks, contract checks,
   Ruff and the repository guard. Include migration-abort, restore, archive-bomb,
   lock-contention and integrity-tamper tests.
3. Compare the generated contract catalog with the preceding release.
   Tightened/removed fields and enum removals are major; additive optional
   fields are minor; documentation-only changes are patch. Reject an
   insufficiently declared version.
4. Provide an offline Ed25519 release key to `finance-release`. The tool embeds
   current schema/UI hashes and builds the wheel twice with a fixed
   `SOURCE_DATE_EPOCH` and `PYTHONHASHSEED`. Differing SHA-256 hashes fail.
5. Retain the wheel, release manifest, contract catalog, CycloneDX and SPDX
   SBOMs, detached signatures and public key. Transfer the public-key
   fingerprint through an independent trusted channel.
6. In an isolated environment install the wheel with `--no-index`, verify the
   CLI and embedded schemas/UI, create synthetic data, back it up, restore it,
   rebuild projections and compare all totals and event sequences.

The release manifest records application and supported versions, artifact
hashes, build environment, offline requirement, integrity roots, SBOM hashes,
test summary and signing-key fingerprint. Machine-local paths, usernames,
caches, timestamps other than the fixed build epoch, real data and private keys
must not enter artifacts. A test-generated signing key may verify the pipeline,
but does not constitute a production release signature.
