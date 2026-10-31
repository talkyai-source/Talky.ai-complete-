# Atomic inbox health verification

Source `7babac65`, integrated locally as `d43deab8`, fixes the stale email-health
write. A failed read or token refresh now carries private evidence identifying
the authorization row and exact stored credential generation it used. A later
successful refresh supplies its newly acknowledged generation. The error path
cannot expire a replacement account merely because it reused the same connector.

The existing health helper now uses one bounded tenant-scoped transaction. It
locks the connector, locks all its existing account rows in ID order, then reads
the current active account again. Only matching, unambiguous row/generation
evidence permits both account and connector status updates. Missing proof,
changed credentials, timeout or failed acknowledgement has no broad fallback.
An interrupted commit is unacknowledged, not proof that no write happened.

The [owner report](artifacts/ag06-inbox-health/verification.md) records **241
passing tests, zero failures and zero skips**, plus Ruff F and whitespace checks.
It covers canonical Gmail compatibility, exactly one expiry attempt per confirmed
failure, initial/refreshed credential ownership, and original-generation use when
a refresh token is unavailable. Root and RT independently reviewed the source.

Root additionally ran the actual helper and snapshot factory against private
PostgreSQL tables: **13 checks passed**. The [result](artifacts/inbox-health-actual-pg/result.json)
and [preserved program](artifacts/inbox-health-actual-pg/probe.py) record:

- Current-generation expiry and equivalent JSON/datetime evidence.
- Reconnected accounts, changed ciphertext, absent/foreign proof and tied active
  generations held without stale expiry.
- Observed PostgreSQL lock waits in both reconnect/parent-lock orders, changes to
  older active or inactive rows, and activation ordered after all-row locks.
- Rollback of a real account UPDATE after an injected parent-boundary failure.
- External cancellation rollback and lock-timeout cleanup, followed by successful
  pool reuse. Only the timeout case temporarily used a 0.1-second test budget;
  the production helper retains its five-second bound.

The test used disposable loopback database `cp04_acceptance_test` at migration
head `0061_dnc_runtime_contract`. Private `LIKE ... INCLUDING ALL` table copies
received the canonical account-to-connector foreign key and a restrictive forced
tenant policy. A fresh `NOBYPASSRLS` role had no public write privileges. Full
production triggers, tenant foreign keys, grants and policies were not copied.
The tested isolation level was `read committed`.

Public connector/account row digests, their security/grant metadata and migration
head were unchanged. The private schema and role were removed. The two application
hashes stayed unchanged during the run and exactly match the integrated files.
This is actual-helper SQL/concurrency evidence, not full deployment or provider
acceptance. The earlier seven-case SQL design probe remains a separate, narrower
historical experiment and is not added to this count.

The command ran from the production-readiness checkout:

```text
backend/.venv/Scripts/python.exe tmp/verify-inbox-health-actual-pg.py
```

The interpreter was the original repository's Python 3.12 environment, with
`PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, the existing exact-requirements overlay
then the OP02 test-dependency overlay. The preserved program retains its original
`tmp/` location assumptions and imports the frozen sibling
`inbox-original-account-20261005/backend` source; its recorded source hashes must
match before reproducing these observations. It refuses to overwrite an existing
result directory.

The lock establishes the point at which current health was checked. It cannot
prevent a later authorized account change, and token-only refresh is not itself
a parent-reactivation workflow. No customer/provider call, delivery retry,
production migration, push or deployment occurred. AG06 still needs supported
original-action-account inspection, approved operator resolution of unknown
outcomes and designated account/browser acceptance. Package and release gates
remain open.
