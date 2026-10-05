# Exact authorization-generation health expiry

Behavioral base: `8141266a` (canonical Gmail row-pin follow-up); an evidence-only fingerprint correction `62d77c7f` precedes this source commit. Final source: `7babac65eb74989ee49bf395c8284c79b42351e5`. This isolated slice changes the existing inbox health write and its resolver evidence. It adds no migration, OAuth workflow, external action, resend or operator adjudication.

## Reproduced failure

The original inbox health writer performed two separate synchronous compatibility-client updates by connector/tenant/status. It could expire a replacement account after a failure from the previous authorization. The earlier actual-method synthetic probe remains in `../ag06-inbox-account/expiry-probe.py` and `expiry-residual.json`.

`initial.txt` preserves two additional actual-read failures on the row-pin source: first-read 401 followed by a newer credential generation with no refresh token incorrectly expired the newer generation; a provider-confirmed exception lacking account-generation proof broadly expired the connected account. Both failed before this source change. No real customer/provider/DB was involved in those reproductions.

## Bounded repair

`ConnectorAuthorizationSnapshot` is immutable and has no generated repr. Its private generation SHA256 binds the exact encrypted access/refresh values, normalized expiry/refresh timestamps, and optional external ID read from persistence. The snapshot contains identity and a digest, not plaintext or retained ciphertext. Neither it nor the digest is included in public results or logs. JSON timestamp strings and asyncpg datetimes normalize to the same UTC representation. Missing/malformed proof becomes unavailable without breaking an otherwise valid read.

The resolver captures the selected generation before refresh and attaches it to a provider-confirmed refresh exception. Successful refresh constructs the next snapshot only from the acknowledged update's returned row, using the ciphertext already written; it does not encrypt a second time. The existing active-row CAS remains required. A second 401 therefore refers to the refreshed generation. When no refresh was sent after the initial 401, the health attempt uses the original rejected generation, never a newer row observed during lookup. Inner/outer exception boundaries make one health attempt for each failure.

The existing `_mark_email_authorization_expired` is now awaited through the existing client pool and tenant-scoped transaction. It locks the exact active parent, then all existing tenant-bound account rows in ID order, including inactive rows. A fresh active-account query then applies the existing latest-refresh ordering; equal latest timestamps are ambiguous and cause no transition. The selected row and generation must match the immutable proof. Exact account and parent updates require returned acknowledgements in the same transaction. Parent locking blocks new account inserts through the existing FK, while locking all children prevents an older/inactive row from changing the authoritative choice inside the decision.

The five-second context bounds acquisition/lock/work acknowledgement. Timeout or storage failure has no broad fallback; cancellation propagates through rollback. A True return requires completed transaction acknowledgement. False means no transition was acknowledged, not proof that no commit happened if its acknowledgement was interrupted. The existing provider-error response remains truthful about the observed rejection without claiming a health write was saved.

## Verification

- `initial.txt`: 2 failed / 34 deselected before the atomic repair.
- `focused-draft.txt`: 79 passed / 2 failed. The two older fixtures assumed broad expiry from connector ID alone. They now explicitly assert no broad write with missing generation; new actual-helper positives preserve current-generation expiry rather than deleting the positive contract.
- `related.txt`: 241 passed / 0 failed / 0 skipped, 13 existing datetime warnings across the 13 modules listed in `manifest.json`.
- `lint.txt`: Ruff F clean for the two application files and three affected test modules. Owned tests are Black formatted; diff check is clean.

Independent realtime-agent source review is clear: it checked lock coverage/order, generation comparison, transaction acknowledgements, timeout/cancellation and the three failure-proof paths, and found no additional material defect. It ran no tests or PostgreSQL/provider operations; the 241-test count remains the implementation agent's result. Root independently ran the actual-helper PostgreSQL validation below. Root also reviewed the final source before authorizing this commit.

Owned tests use actual application methods with synthetic provider/database ports. They verify current-generation commit, stale/newer/tied/revoked/missing proof, parent-update failure and zero-row rollback, transparent cancellation, bounded timeout, timestamp equivalence, proof privacy/immutability, and the initial/refresh/second-401 propagation paths. These tests alone are not PostgreSQL lock/RLS evidence. The separate root-owned actual-helper concurrency validation is linked below; it is not an additional run by this agent.

Exact environment matches `../ag06-inbox-account/verification-row-pin.md`: repository Python 3.12 interpreter, `ENVIRONMENT=test`, deliberately unavailable synthetic default DATABASE_URL, and the exact-requirements overlay before the existing Lua overlay. No provider/network/PG operations were made by this agent for this slice. The full command is `python -m pytest ` followed by the ordered `modules` in `manifest.json`, then `-q -o addopts=`.

## Root-owned PostgreSQL validation

The [actual-helper result](../inbox-health-actual-pg/result.json) records **13 passing PostgreSQL controls**. Root ran the frozen application helper and existing tenant-transaction wrapper on a disposable private schema with synthetic tokens and a restricted role. The two application source hashes were equal before and after the run and match the explicitly UTF-8 normalized committed files recorded in `manifest.json`.

The controls cover current confirmed expiry and JSON/datetime proof equality, stale ciphertext with an unchanged timestamp, replacement account preservation, missing/foreign proof, equal-latest ambiguity, both parent-lock/FK-insert orders, an older active or inactive account changing during a lock wait, later inactive activation blocked by all-row locks, rollback of a real account update when the parent boundary fails, external cancellation rollback, and a 0.1-second test timeout with pool reuse. The production timeout remains five seconds.

Root's runner was `tmp/verify-inbox-health-actual-pg.py` in the main implementation worktree; root owns its preservation and the separate result artifact. Public table digests, catalog and migration head were unchanged. The private schema and role were removed. The private copies reproduce the canonical account-to-connector foreign key and a restrictive forced tenant policy, but do not copy every production trigger, tenant FK or grant. These are actual helper/transaction/concurrency controls, not deployment, full production RLS parity, provider-account acceptance or delivery proof. The earlier seven-case SQL prototype remains historical design evidence and is not relabeled as application verification.

## Fingerprint correction

The draft health manifest used the same Windows CP1252 decoding mistake diagnosed in [the row-pin correction](../ag06-inbox-account/row-pin-hash-correction.md). Before publishing this manifest, its hashes were recalculated from exact committed Git blobs using explicit UTF-8 and LF normalization. All previous draft hashes reproduce from the same final source under the old algorithm; they are retained under `draft_fingerprint_correction`. This is a fingerprint correction, not a source behavior change after tests. The corrected app hashes also match root's actual-PG source hashes.

## Existing writer boundaries and limits

The compatibility client's `.execute()` calls open independent transactions; they cannot make the previous account+parent writes atomic. OAuth reconnect inserts a fresh account and later activates its parent. Existing token refresh updates the same row. The health transaction uses these established FK/row-lock semantics; it does not change those workflows.

Tenant manual refresh, Admin reconnect, and token-rotation paths retain their existing response/status behavior. This repair does not certify every administrative writer, make external provider state transactional with PostgreSQL, or establish provider-stable identity from a local authorization row. A health transition ordered before a later valid reconnect does not prevent that reconnect. Unavailable proof/status persistence remains unavailable rather than guessed. Designated-account/browser acceptance and supported held-action adjudication remain open.
