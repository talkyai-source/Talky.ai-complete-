# Reviewed selector: native PostgreSQL identity repair

On 2026-10-06, actual PostgreSQL execution exposed a defect missed by the preceding synthetic reviewed-account tests: the new selector rejected every native UUID authorization-row ID. The minimal boundary repair is committed as `5bdec4f32eaca4ba443a653746b9b8edd55a8253` over `4c91e112`. Final verification passed **168 focused unit controls** and **12 separately scoped actual PostgreSQL controls**. These are local source/SQL checks, not live connector acceptance.

## Reproduction and repair

The actual `PostgresClient` SELECT returned `asyncpg.pgproto.pgproto.UUID` for `connector_accounts.id` and `datetime` for `created_at`. The native ID satisfies `isinstance(value, uuid.UUID)` and does not satisfy `isinstance(value, str)`. The reviewed selector previously required a string and raised `ReviewedConnectorChanged`, with the cause `missing authorization row`, for a valid current account. Existing synthetic rows used string IDs and did not expose this database-boundary difference.

[baseline-uuid-result.json](baseline-uuid-result.json) and [baseline-uuid.txt](baseline-uuid.txt) preserve the real adapter reproduction. Its exact script is [baseline-uuid-probe.py](baseline-uuid-probe.py). The baseline run exited 0 because its explicit assertion was that the defect reproduced; that result is not a successful product behavior check.

The source repair imports `uuid.UUID` and normalizes only genuine UUID IDs to canonical strings in `_reviewed_account_row`. Existing nonempty text IDs retain their semantics. Blank strings and arbitrary objects are rejected. The helper returns a copied row containing the normalized ID, leaving the adapter response untouched. The exact original-account pin comparison remains in place. No global PostgreSQL decoder, CRM read mode or provider behavior changed.

The new 16-control module runs the actual adapter decode boundary with standard and asyncpg UUIDs, existing text IDs, invalid types, blank text and different reviewed pins. Its baseline [identity-red.txt](identity-red.txt) reports 5 failed and 11 passed: two positive UUID admissions failed, blank text was accepted, and two negative pin controls encountered the wrong rejection path. This is one native-type production blocker plus a blank-value guard correction; it is not five independent production defects.

## Final results

| Check | Result | Evidence |
| --- | --- | --- |
| Typed selector, canonical reviewed actions, original inbox account and inbox authorization health modules | 168 passed, 0 failed, 0 skipped, 11.41 s | [focused-final.txt](focused-final.txt) |
| Actual selector/current-account/refresh SQL | 12 controls passed | [final-result.json](final-result.json), [final.txt](final.txt) |
| Ruff F on resolver, new test and all three probe snapshots | Passed | [lint.txt](lint.txt) |
| Source staging | `git diff --cached --check` passed | Source commit `5bdec4f32eaca4ba443a653746b9b8edd55a8253` |

The 168-test count includes 16 new typed controls, 97 canonical reviewed-account controls, 34 inbox row controls and 21 inbox health controls. They are not additive to the preceding owner run's 396 tests. Root reviewed the helper-only source diff and all 16 typed controls before the final SQL run and source commit. No application/test edits occurred after that review or during SQL execution.

Exact arguments, working directories, environment and archived script identities are in [commands.json](commands.json); source and artifact hashes are in [manifest.json](manifest.json). Each SQL result also records source hashes before/after and the executing probe hash. The final SQL result was produced against the explicitly frozen uncommitted candidate and byte-matches the later source commit.

## Actual PostgreSQL boundary

The probe connected only to the designated disposable `cp04_acceptance_test` database at `127.0.0.1:55434`, with canonical public migration head `0061_dnc_runtime_contract`. It created two private `LIKE public.<table> INCLUDING ALL` tables (`connectors`, `connector_accounts`), added their connector foreign key, and used a separate restricted login role with no public table write grants. Both private tables used forced tenant-only RLS. Public data was never copied into them.

Every selector SELECT and token UPDATE ran through the actual `PostgresClient`, its synchronous worker connection path, real generated SQL, transaction/RLS context and real asyncpg result types. A private DSN `search_path` routed all unqualified table access to the private tables. The actual adapter's metadata lookup hardcodes the public schema; because the restricted role cannot see public columns without privileges, the probe preloaded the adapter's metadata cache from an owner connection's actual public column/type query after checking the private clone's column/type equality. **The metadata discovery branch itself was not tested.** No row IDs, timestamps, result types or query results were fabricated to make admission pass.

Encryption and the connector factory/token methods were synthetic. The same-row refresh used actual token-update SQL, timestamp bindings, returned-row acknowledgement and generation proof. Scheduling changes occurred deterministically inside the synthetic awaited refresh/token-install methods. This is not exhaustive concurrent scheduling, real OAuth refresh or provider effect execution.

The 12 controls cover:

1. A native UUID row is accepted and normalized without changing its identity.
2. Newest creation time beats newest refresh time; a retained older exact pin is denied.
3. Tied creation timestamps are denied.
4. A null creation timestamp, which the canonical schema permits, is denied.
5. Same-row forced refresh persists the new token/timestamps, retains the original authorization proof and derives its generation from the acknowledged database row.
6. A newer reconnect appearing during refresh prevents the old authorization from being returned or promoted. The still-active old row may receive its own refreshed tokens; no effect is dispatched.
7. Revocation during refresh causes a real zero-row token UPDATE and prevents token installation.
8. Deletion during refresh likewise causes a zero-row UPDATE and prevents token installation.
9. Parent deactivation during awaited token installation is denied by final admission.
10. A changed external identity during that await is denied.
11. A newer authorization row during that await is denied.
12. Foreign and absent tenant contexts cannot see or admit the row; the restored original context remains usable.

The final run's captured public data digests, table flags/ACLs, columns, constraints, policies and migration head were unchanged. Source hashes were unchanged. The private schema and role were removed and no earlier probe objects remained. Network guards blocked all non-designated access except a narrowly scoped Windows `socketpair` allowance needed to initialize worker event loops; 61 loop self-pipe connections were allowed in the final run and zero unexpected network attempts occurred. No live provider resources or customer credentials were used.

## Preserved first harness attempt

The first attempt failed before adapter SELECT execution because the strict network guard also blocked Windows' loopback connection while creating an asyncio worker-loop self-pipe. [baseline-result.json](baseline-result.json), [baseline.txt](baseline.txt) and [initial-probe.py](initial-probe.py) retain that harness failure. Its owned schema/role were still removed and captured public/source state was unchanged. The guard was corrected only for the current thread while inside the actual `socket.socketpair` call; arbitrary loopback destinations remain disallowed outside that initialization. This failed attempt is not counted as a product failure or passing SQL control.

## Limits

Private `LIKE` tables preserve canonical columns/defaults/checks/indexes, but do not copy the complete tenant foreign-key graph, triggers, grants or public RLS policy; the explicit connector foreign key and private tenant policy are narrower. This is not full production-schema parity, deployment acceptance, live OAuth/provider behavior, HTTP/browser authorization acceptance or proof of atomicity between the final local admission and a later remote operation. Creation time remains stable under canonical writers rather than database-immutable. No plan gate or feature-completion status is promoted by this evidence.
