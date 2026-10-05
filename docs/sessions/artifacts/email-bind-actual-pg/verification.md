# Email authorization binding: actual PostgreSQL verification

On 2026-10-06, the final probe passed **15 actual-SQL controls** against the isolated loopback PostgreSQL 16.1 instance. Application files were unchanged during execution. This is a helper/service SQL check with synthetic account admission and send ports, not provider delivery or production qualification.

The tested source was an uncommitted, explicitly frozen candidate over `39407989342b98fdcb0c586a15aab4fbd259f986`. Canonical LF SHA-256 values were identical before and after:

| File | SHA-256 |
| --- | --- |
| `backend/app/services/email_service.py` | `bf72801178da5386f07e4bcaaafd7e3e6d5d2eaa68a5eb1f81378590980b545d` |
| `backend/app/services/connector_resolver.py` | `83e811c7469d8409d4700a0ac0752a6aa3fc4cd73d6fa5dea4a6946c0a7f1552` |
| `backend/app/core/db_utils.py` | `e58351113da50a0821e8327b06a14271c97778075b74786838f25d4ba5a4fb3a` |
| `backend/app/core/postgres_adapter.py` | `012685e51c160e0f1ee13ea0f5c391e7ee1c0e6e27f9e3ec5dda0a2dbc93e074` |

## Boundary and isolation

The probe used only `postgresql://talky@127.0.0.1:55434/cp04_acceptance_test`. Public migration head was `0061_dnc_runtime_contract`. The owner connection read public metadata/digests and created one UUID-named private schema and role. A private `assistant_actions` table copied canonical columns, defaults, checks and indexes with `LIKE public.assistant_actions INCLUDING ALL`; it copied no public data. Foreign keys, triggers and the public RLS policy were not copied. This is therefore **not a fully migrated fixture or complete production constraint graph**.

The helper connections used a dedicated login role with no superuser, bypass-RLS, database/role creation or inherited-role privileges. They had only the private schema in `search_path` and only private table grants. A separate strict tenant-only policy was enabled and forced. The role had zero write privileges on public tables. Before/after public row count/digest, columns, constraints, indexes, policy and ACL matched; the public table had zero rows. Private schema and role cleanup was verified. No prior probe schema/role remained from the first attempt. The server was left running for other authorized work.

Only the designated loopback PostgreSQL socket was permitted after asyncio loop initialization; **zero external network attempts** occurred. Connector selection, the current-account admission function and its unused compatibility-client facade, encryption, template validation and connector send were synthetic. Actual `EmailService` create/bind/status SQL and `acquire_with_tenant` executed through asyncpg under the restricted role. The actual identity-shape helper was used with explicit synthetic tenant, connector and authorization-row IDs. No fake external provider-account ID was supplied.

## Observed controls

- A pending row became running atomically with the JSONB authorization proof; unrelated input fields survived. SQL NULL input became an object. The real acknowledgement was `UPDATE 1`.
- Foreign tenant, absent row and each nonpending phase (`running`, `cancelled`, `completed`, `unknown`) produced actual `UPDATE 0`, raised, and left saved rows unchanged. Foreign and unscoped reads saw no tenant rows.
- An injected exception after the actual bind UPDATE but **before transaction commit** rolled it back, including its phase, and a later operation reused the connection successfully. This proves local transaction rollback; it does not simulate an unknown remote commit or lost network acknowledgement after commit.
- The actual service sent nothing when that bind failed. A row changed to cancelled or competing-running before binding also caused zero sends; failure cleanup did not overwrite that row or its input. These are deterministic pre-bind interleavings, not an exhaustive concurrent scheduling test.
- A separate connection observed the committed running row and original authorization inside the synthetic send callback. One synthetic accepted send then wrote its synthetic message receipt through the actual status SQL. This is not evidence of recipient delivery.
- A real PostgreSQL division-by-zero fault injected at the completed-receipt write yielded an unknown result and a persisted unknown receipt. When both post-send status writes failed, the durable row remained running with its original authorization proof; the returned unknown result retained the synthetic message ID. Each case made one synthetic send attempt in that invocation. No cross-invocation durable no-resend claim is made.

The 15 cases are enumerated in [final-result.json](final-result.json); [final.txt](final.txt) includes expected error logs from the deliberately failed writes. Logs and results contain synthetic IDs only.

## Reproduction and first attempt

The exact command/environment is saved in [command.json](command.json). Run the script only after the service owner explicitly freezes the source and reserves the disposable database slot:

```powershell
& 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe' docs/sessions/artifacts/email-bind-actual-pg/probe.py --confirm-source-frozen --output docs/sessions/artifacts/email-bind-actual-pg/final-result.json
```

The script refuses to overwrite existing result evidence. Choose a fresh result filename for another authorized run.

[initial.txt](initial.txt) preserves the first attempt's evidence-writer failure: PostgreSQL's internal `pg_constraint.contype` character value was returned as bytes and could not be JSON-serialized. All case code and cleanup had run, but the result artifact was not written, so that attempt is not counted as a completed pass. [initial-probe.py](initial-probe.py) preserves its exact source. The final probe casts that metadata field to text and adds explicit zero-residue checks. No application change was made between attempts. Final execution exited 0; final probe Ruff F and syntax checks passed.

## Limits

These checks do **not** validate the reviewed-account selector's new SQL or credential refresh/current-account transaction, the compatibility `.table()` bind branch, canonical tenant/account foreign keys, a live provider, an HTTP authorization route, crash/restart recovery, or delivery semantics. Prior inbox-health PostgreSQL evidence is separate and does not validate those new selector paths. No application files, public objects or provider resources were changed by this task.
