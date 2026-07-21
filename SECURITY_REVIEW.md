# 1.1.0 security review

Release review date: 2026-07-21.

The threat-model test set, repository guard, schema/UI tamper checks, archive
authentication and resource limits, migration rollback, workspace locking,
offline Wheel acceptance and artifact-signature tests all passed. No open
critical or high finding is known in the project review.

The 1.1.0 review additionally covers CP1252 decoding, strict section and column
recognition, German decimal/date parsing, encrypted original-file retention,
symlink/path rejection, account-type mapping conflicts, explicit opening
balances, non-correcting period reconciliation and user-protected investment
relations. All fixtures are visibly synthetic.

The complete 27-package `requirements-runtime.lock` was checked with
`pip-audit 2.10.0` against the current PyPI advisory service. Result: no known
vulnerabilities. This is a point-in-time result, not a guarantee against future
advisories; `1.1.x` security updates remain in scope.

The final manifest records zero critical and zero high findings. A non-zero
value sets `release_blocked` and causes `finance-release-verify` to reject the
release. Production signing still requires the intended private release key;
the private key is never part of the repository or artifact set.
