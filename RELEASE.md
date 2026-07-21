# Release procedure 1.1.0

1. Use the exact Python and npm dependency versions declared in
   `pyproject.toml`, `requirements-runtime.lock`, `package.json` and
   `package-lock.json`; perform the UI build and offline-resource check.
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
5. Retain the wheel, release manifest, contract catalog, UI bundle catalog,
   `cyclonedx-sbom.json`, `spdx-sbom.json`, `checksums.sha256`, detached
   signatures and public key. Transfer the public-key
   fingerprint through an independent trusted channel.
6. Verify `release-manifest.sig` against the intended public key and its
   independently distributed fingerprint. Then verify all checksums and
   detached signatures with `finance-release-verify`.
7. In an isolated environment install the wheel and its pinned dependencies
   from a local wheelhouse using `--no-index`. Run `finance-acceptance`; the
   command refuses execution from the repository source tree. It creates only
   synthetic data, exercises every MVP domain including the German
   multi-account import, restarts, restores, rotates the key and compares the
   complete projection snapshot.

The release manifest records application and supported versions, artifact
hashes, build environment, offline requirement, integrity roots, SBOM hashes,
test summary and signing-key fingerprint. Machine-local paths, usernames,
caches, timestamps other than the fixed build epoch, real data and private keys
must not enter artifacts. A test-generated signing key may verify the pipeline,
but does not constitute a production release signature.

## User verification order

1. Obtain the intended public-key fingerprint through an independent trusted
   channel and verify `release-manifest.sig`.
2. Run `finance-release-verify RELEASE_DIR --public-key PUBLIC_KEY
   --expected-fingerprint FINGERPRINT`; this checks artifact signatures and all
   hashes in `checksums.sha256` and the manifest.
3. Install the wheel and pinned dependencies offline with `pip --no-index`.
4. Run `finance-acceptance --wheel-sha256 HASH` before opening productive data.
5. Only after all four steps pass, open or migrate a productive workspace.
