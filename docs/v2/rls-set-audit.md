# RLS `SET` → `SET LOCAL` Audit — and a larger discovery

**Ticket:** TKT-009 · **Deliverable:** this document · **Date:** 2026-07-24
**Verdict on fix 13 (PgBouncer): 🔴 DEFER — and the reason is not the one the ticket anticipated.**

---

## Executive summary

TKT-009 was written to answer one question: *is every RLS session variable scoped safely enough that
PgBouncer transaction pooling can be introduced without leaking tenant context between requests?*

The answer is **no**, on a much larger scale than the ticket assumed — the known suspect was 1 site,
the real count is **42**. But the audit also surfaced something that changes the framing entirely:

> ### 🔴 The production database role is `rolsuper = true, rolbypassrls = true`.
> **Row-level security is defined but not enforced.** 64 policies exist and `calls`, `campaigns` and
> `leads` all have `relrowsecurity = true` — and Postgres skips every one of them for a superuser or a
> `BYPASSRLS` role. The application connects as exactly such a role.

Everything else in this document has to be read through that fact.

| | Finding |
|---|---|
| **Sites audited** | ~70 across the backend |
| **UNSAFE** | **42** — 3 bare session `SET`, **36** via one shared helper, 3 `SET LOCAL` with no transaction |
| **SAFE** | ~25, using the two canonical transaction-scoped helpers |
| **N/A (dedicated connection)** | **0** — none exist; every path is pooled |
| **PgBouncer verdict** | **DEFER.** The ticket's own gate — "do not start TKT-010 if any site remains UNSAFE" — fails 42 times over |
| **Bigger finding** | **RLS is not in force at all** (F-23). Tenant isolation currently rests entirely on application-layer `WHERE tenant_id = …` filters |

---

## Part 1 — The headline: RLS is defined, not enforced

### Evidence

```sql
SELECT current_user AS role, rolsuper, rolbypassrls, rolcreaterole
FROM pg_roles WHERE rolname = current_user;
```
```
{'role': 'talkyai', 'rolsuper': True, 'rolbypassrls': True, 'rolcreaterole': True}
```

```sql
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
WHERE relname IN ('calls','campaigns','leads','tenants','tenant_ai_configs') AND relkind='r';
```
```
calls              relrowsecurity=True   relforcerowsecurity=False
campaigns          relrowsecurity=True   relforcerowsecurity=False
leads              relrowsecurity=True   relforcerowsecurity=False
tenant_ai_configs  relrowsecurity=False  relforcerowsecurity=False
tenants            relrowsecurity=False  relforcerowsecurity=False

total RLS policies defined: 64
```

### What this means

Postgres does not apply row-level security to a superuser, nor to any role with `BYPASSRLS`. The
application's runtime role is **both**. Therefore:

1. **All 64 policies are inert.** They are correct, they are deployed, and they never execute.
2. `relforcerowsecurity = false` means even the *table owner* bypasses RLS — and since the role is
   superuser it is effectively owner everywhere. There is no second line here either.
3. **`tenants` and `tenant_ai_configs` have RLS not even enabled.** For those two tables the isolation
   story is application-layer only, by design or by omission — either way, undocumented.
4. Tenant isolation in production today rests **entirely** on application-layer `WHERE tenant_id = $1`
   filtering. That work is real and was hardened deliberately (the tenant defence-in-depth commits),
   but it is the *only* layer, not the second one.

This corroborates a comment the code already contains — `backend/app/core/db.py` notes that
`readonly=True` is "a no-op against today's superuser pool." Somebody knew. It never became a finding.

### Why this makes the 42 sites *more* dangerous, not less

The instinct is to relax: if bypass is already global and permanent, a leaked `app.bypass_rls = 'on'`
changes nothing. That instinct is wrong, in a specific and dangerous way.

**The 42 unsafe sites are inert only while the role stays over-privileged.** The moment anyone does
the obviously-correct thing — drop `SUPERUSER`/`BYPASSRLS` so the 64 policies start working — all 42
become live simultaneously. And that change will be made by someone who believes they are *increasing*
security, on a system whose tests pass either way.

That is the worst possible ordering: a security improvement that silently arms 42 latent
cross-tenant leaks. **The 42 sites must be fixed *before* the role is de-privileged**, and the role
must be de-privileged before PgBouncer, and PgBouncer last. That ordering is the main output of this
audit.

### Sequencing

```
1. Fix all 42 UNSAFE sites (transaction-scope every RLS GUC)
2. Add the AST regression test so no new one appears
3. Verify tenant isolation still holds with the app-layer filters
4. THEN de-privilege the role → the 64 policies come alive
5. Re-verify isolation, now with two enforced layers
6. ONLY THEN consider PgBouncer (TKT-010)
```

Skipping from 3 to 6 is what the original plan implied. Skipping to 4 without 1 is actively harmful.

---

## Part 2 — The inventory

### Category A · Bare session-level `SET` on a pooled connection — **UNSAFE**, 3 sites

**A1 — `backend/app/workers/dialer_worker.py:846-870`**, `DialerWorker._acquire_db`:
```python
@asynccontextmanager
async def _acquire_db(self):
    pool = self._db_pool
    async with pool.acquire() as conn:
        await conn.execute("SET app.bypass_rls = 'on'")
        await conn.execute("SET app.current_tenant_id = '00000000-0000-0000-0000-000000000000'")
        yield conn
```
No `LOCAL`, no `conn.transaction()`. The pool is proven shared — `initialize()` reuses
`container.db_pool` when the container is up. **14 methods** of the dialer use this context manager,
so every one of them returns a connection to the pool still carrying **RLS bypass**.

This is the suspect the ticket named, confirmed exactly as described.

**A2 — `backend/app/domain/services/event_emitter.py:98-105`**, `cleanup_expired_events_loop`.
Same shape, same shared pool (wired at `main.py` from `container.db_pool`).

**A3 — `backend/app/domain/services/billing_service.py:374-387`**, `_claim_webhook_event`.
Same shape; traced `Client(pool)` → `get_db_client` → `get_db_pool` → `container.db_pool`.

### Category B · The systemic one — `apply_tenant_rls_context()`, **UNSAFE**, 36 call sites

This is what the previous pass missed, and it is ten times the surface area of the named suspect.

`backend/app/core/tenant_rls.py:13-30`:
```python
await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
await conn.execute("SELECT set_config('app.current_user_id',   $1, false)", ...)
await conn.execute("SELECT set_config('app.current_request_id',$1, false)", ...)
```

**The third argument of `set_config` is `is_local`.** `false` means session-scoped — precisely
equivalent to a bare `SET`, and precisely what this ticket exists to eliminate. It is invisible to a
`grep` for `SET app.`, which is why the ticket's own plan step explicitly says to include
`set_config`. That instruction earned its place.

Every one of the 36 sites authenticates a specific tenant and then leaves **that tenant's id** set at
session scope on a connection handed back to a shared pool:

| File | Sites |
|---|---|
| `endpoints/telephony_sip/trunks.py` | 12 |
| `endpoints/telephony_concurrency.py` | 5 |
| `endpoints/telephony_sip/route_policies.py` | 4 |
| `endpoints/telephony_sip/codec_policies.py` | 4 |
| `endpoints/telephony_runtime/activate.py` | 4 |
| `endpoints/telephony_runtime/rollback.py` | 3 |
| `endpoints/telephony_sip/quotas.py` · `telephony_runtime/{versions,preview,metrics}.py` | 1 each |

Three sub-cases, all equally broken:

- **No transaction at all** (the common case, e.g. `trunks.py:165-204` `list_sip_trunks`).
- **Called *before* `async with conn.transaction():` opens** (e.g. `route_policies.py:155-157`) — the
  session-level `set_config` has already auto-committed in its own implicit transaction before the
  explicit one begins. The transaction is irrelevant to it.
- **Called *inside* an open transaction** (`trunks.py:964-981`) — still no difference, because a
  session-level `set_config` survives commit and rollback alike.

**A naming hazard worth fixing on its own.** There are two helpers with near-identical names and call
shapes, one safe and one not:

| Helper | Location | Scope | Verdict |
|---|---|---|---|
| `acquire_with_tenant()` | `core/db_utils.py` | `SET LOCAL` in a transaction | ✅ canonical |
| `apply_tenant_rls_context()` | `core/tenant_rls.py` | `set_config(…, false)` | ❌ unsafe |

A future author reaching for "the RLS context helper" has a coin-flip chance of picking the wrong
one. Renaming the unsafe one during the fix is cheap insurance.

### Category C · `SET LOCAL` with no open transaction — **UNSAFE**, 3 sites

Subtle, and arguably the most interesting finding. `SET LOCAL` outside an explicit transaction block
applies only to the implicit single-statement transaction it runs in — and is discarded the moment
that statement auto-commits, i.e. **before the next `execute`/`fetch` on the same connection**.

- `endpoints/telephony_bridge.py:901-906` — `_verify_call_ownership`
- `endpoints/telephony_bridge.py:971-978` — `hangup_calls_for_campaign`
- `domain/services/provider_cost_ledger.py:123-127` — `_flush_once`

In each, the `SET LOCAL` and the query that needs it are two separate calls with no enclosing
`conn.transaction()`. **The bypass never reaches the query.** So either the query silently returns
nothing under RLS — masked by a broad `except Exception` — or it "works" only because the pooled
connection still carries session-level bypass left behind by a Category A or B caller.

Read that again: **the correctness of these three code paths currently depends on the order in which
the pool hands out connections, and on another function's bug.** Today that is masked a second time
by the superuser role. Fix the role without fixing these, and `_verify_call_ownership` — a
**security check** — starts failing in a way that depends on traffic patterns.

### Category D · SAFE — ~25 sites

The two canonical primitives, both correct:

`backend/app/core/db_utils.py:59-101` — `acquire_with_tenant()`:
```python
async with acquire_cm as conn:
    async with conn.transaction():
        if tenant_id is not None:
            _validate_uuid(str(tenant_id))
            await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
        else:
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            await conn.execute(f"SET LOCAL app.current_tenant_id = '{_NIL_UUID}'")
        yield conn
```
and `core/db.py:40-68` `_apply_rls_context`, backing `get_db()` / `get_read_db()`.

Correct users include `cleanup_worker`, `call_transcript_persister`, `call_service`
(`_handle_call_status_pooled`), `recording_service`, `telephony/recording.py`, the
`telephony_providers` endpoints, `postgres_adapter`, and several scripts.

Two of them contain comments that describe this exact hazard better than the ticket does —
`recording_service.py:619-623` calls a pooled connection's inherited value *"an inherited-context
lottery"*. The knowledge existed in the codebase. It just never became an audit.

**Dead but correct:** `core/security/tenant_isolation.py` has `set_tenant_context_in_db()` and a
`tenant_context()` manager, both correctly using `SET LOCAL` — with **zero call sites** anywhere.
Either wire it up or delete it; leaving correct-looking helpers unused invites someone to adopt one
later without its transaction contract.

### Category E · N/A — dedicated, non-pooled connection: **0 sites**

The ticket required this be proven rather than assumed. It cannot be proven for anything here.

The only candidate is `postgres_adapter.py`'s per-call `asyncpg.connect(...)`. It is already SAFE via
its transaction wrap, and it would not qualify anyway: **once PgBouncer is in front, a fresh
`asyncpg.connect()` is a new client connection to PgBouncer**, which multiplexes it onto a shared
server-side pool in transaction mode. There is no such thing as a dedicated physical connection in
this architecture. Every site must be judged as pooled.

---

## Part 3 — A test that locks the bug in

`backend/tests/unit/test_tenant_rls.py:26-30` asserts that the emitted SQL ends in `, false)` — it
**tests for the session-level behaviour and passes**. Any fix to `tenant_rls.py` breaks this test, and
that break is not a regression; it is the evidence the fix landed.

Worth stating in the fix commit, because a green test asserting the wrong invariant is exactly the
kind of thing that gets "fixed" back.

Related: `infra/pgbouncer/pgbouncer.ini`'s header comment claims *"SET (without LOCAL) … we don't use
them on the hot path."* That comment is **false** — the dialer's hot path is Category A1. The config
was written against an intended design, not the code.

---

## Part 4 — Remediation plan

### Step 1 — Fix Category B at the root (36 sites, one edit)
Change `apply_tenant_rls_context()` to `set_config(…, true)` **and** require an open transaction. The
call sites mostly do not have one, so the honest fix is to convert the helper into an async context
manager that opens the transaction itself — mirroring `acquire_with_tenant()` — and migrate the 36
sites to it. Rename it in the same change to remove the coin-flip hazard.

### Step 2 — Fix Category A (3 sites)
Convert to `acquire_with_tenant(pool, None)`, which already implements exactly the dialer's intent.
**Do not remove the bypass** — the dialer legitimately needs cross-tenant reads. Change its *scope*,
not its existence.

### Step 3 — Fix Category C (3 sites)
Wrap each in `async with conn.transaction():` so the `SET LOCAL` actually covers its query. Expect
behaviour changes here — these paths may be silently returning empty today.

### Step 4 — Regression test (AST)
Model it on `tests/unit/test_no_domain_api_imports.py`, which is the working precedent for locking an
invariant permanently. Its mechanism: walk every `.py` under a root, `ast.parse()` the source, walk
the tree, match node types, and parametrise per-file so failures name the offending file. It also
keeps an explicit allowlist with a second test asserting no allowlist entry has gone stale.

For this invariant, flag:
- any `.execute(...)` whose first argument is a string literal matching `SET\s+app\.` **without** `LOCAL`
- any `set_config(...)` whose third argument is a literal `false`

Regexing the *extracted string literal* is fine — the AST has already isolated real SQL from comments
and docstrings, which is the property that makes this robust where a raw grep is not.

Detecting "`SET LOCAL` with no enclosing transaction" (Category C) needs enclosing-scope analysis and
is a stretch goal. The string check alone would have caught Categories A and B — 39 of the 42.

### Step 5 — Then, and only then
De-privilege the role, verify isolation with the policies actually live, and re-open TKT-010.

---

## Verdict on fix 13 (PgBouncer): **DEFER**

The ticket's gate is unambiguous: *"Do not start TKT-010 if any site remains UNSAFE."* 42 remain.

Three independent reasons, any one sufficient:

1. **42 unsafe sites.** Under transaction pooling, tenant context and RLS bypass both leak between
   unrelated requests on the same server connection.
2. **The role change must come first.** Introducing PgBouncer while RLS is unenforced buys nothing —
   there is no isolation to preserve. Doing it after the role fix, but before the 42, is worse still.
3. **PgBouncer is not installed** (verified on the host: no binary, no container), so this was never
   a config tweak. It is a new production dependency in the path of every query, on a 2 vCPU box.

**What would flip this to PROCEED:** all 42 fixed, the AST regression test green, the role
de-privileged, and `tests/security/test_idor_tenant_scoping.py` still passing with the 64 policies
actually enforcing.

---

## Checklist — TKT-009

- [x] Every `SET`/`set_config` site found, including non-obvious ones — **`set_config(…, false)` was
      the non-obvious one, and it was 36 of the 42**
- [x] Each classified SAFE / UNSAFE / N/A with reasoning
- [x] `docs/v2/rls-set-audit.md` written
- [ ] All UNSAFE sites converted — **NOT DONE.** 42 sites across 12 files is a multi-day change with
      real behaviour risk in Category C; scoping it as a follow-up rather than rushing it into a
      buffer day is the deliberate call
- [ ] Regression test added — designed above, not yet written
- [ ] Tenant-isolation tests still green — unchanged and green, but see F-23: they are exercising
      application-layer filters, **not** RLS
- [x] **Written verdict on fix 13: DEFER**
- [ ] Peer-reviewed — outstanding

## Test cases

| # | Test | Expected | Result |
|---|---|---|---|
| 1 | Tenant-isolation suite | all green | 🟢 green — but see F-23 for what it actually proves |
| 2 | Dialer bypass under transaction scope | still reads what it needs | ⬜ blocked on the fix |
| 3 | Bypass state after the transaction commits | **cleared** | ⬜ blocked on the fix |
| 4 | Regression test vs. a reintroduced session-level SET | fails | ⬜ designed, not written |
| 5 | Full suite | ≥ baseline | 🟢 3548, unchanged — nothing was modified |

Test 3 was called "the whole point of the ticket". It cannot pass yet, and saying so is the honest
outcome. **The audit half of this ticket is complete; the remediation half is scoped and deferred.**

---

## Findings raised

| ID | Sev | Finding |
|---|---|---|
| **F-23** | 🔴 **Critical** | **RLS is defined but not enforced.** The runtime role is `rolsuper=true, rolbypassrls=true`; all 64 policies are inert. Tenant isolation rests entirely on application-layer filters. Also: the app runs as a Postgres **superuser** — with `CREATEROLE` — which is a least-privilege failure in its own right, compounding F-10 (all services run as OS root). |
| **F-24** | 🟠 High | **42 RLS session-variable sites are unsafe under connection pooling** — 3 bare `SET`, 36 via `apply_tenant_rls_context()`, 3 `SET LOCAL` outside any transaction. Currently masked by F-23; **de-privileging the role without fixing these first arms all 42 at once.** |
| **F-25** | 🟠 High | Three `SET LOCAL`-without-transaction sites (Category C) are **already incorrect today**, independent of pooling. `_verify_call_ownership` is a security check whose bypass never reaches its query; it works only by inheriting another function's leaked session state. |
| **F-26** | 🟡 Med | `tests/unit/test_tenant_rls.py` **asserts the buggy session-level behaviour as correct**. A green test locking in the defect. |
| **F-27** | 🟡 Med | Two near-identically-named RLS helpers, one safe (`acquire_with_tenant`) and one not (`apply_tenant_rls_context`). Coin-flip for the next author. |
| **F-28** | ⚪ Low | `infra/pgbouncer/pgbouncer.ini`'s comment claims bare `SET` is not used on the hot path. It is — the dialer. Config written against intent, not code. |
| **F-29** | ⚪ Low | `core/security/tenant_isolation.py` contains correct-but-unused RLS helpers with zero call sites. Wire up or delete. |
| **F-30** | ⚪ Low | `tenants` and `tenant_ai_configs` have `relrowsecurity=false` — no RLS even in principle. Deliberate or omission, undocumented either way. |
