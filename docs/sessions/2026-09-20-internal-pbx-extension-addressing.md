# 2026-09-20 — Internal PBX extensions become a second kind of inbound address

Owner brief: provision extensions 940003–940006 on four tenant accounts plus
940007 on a MicroSIP softphone, and make inbound and outbound work for them.
Four blockers were named up front. This is what the code actually said, and
what changed.

## Baseline before any change

Production `2f34c72e`, all units active, **0 live calls, 0 queued dialer jobs**.
`origin/main` had moved to `513c87f0` but only with frontend commits, so the
deployed backend matched the code under review. Alembic head `0045`.

| Fact | Value |
|---|---|
| `blaze-pbx-940001` / `940002` | created 2026-09-09, **now `is_active=false`, unregistered** (they were registered on 09-11) |
| `blaze-pbx-940003` (`73d882aa`) | created 2026-09-20 09:28, inactive, credentials stored |
| Reviewed carrier inventory | `{"150001": "+442046132300"}` — nothing else |
| Generated dialplan | one mapped account + fail-closed `_.` catch-all |
| Tenant 1845a165 | campaign `2953b876` "dojo" **running, outbound** |

## The four blockers, verified

1. **`invalid_did`** — `inbound_router.normalize_did` requires 7–15 digits, so
   `940003` returns `None`. Both `inbound_router.resolve_inbound_route` and
   `inbound_admission.admit` call that one function, so there is a single
   chokepoint, not two. Confirmed.
2. **Outbound rejects six digits** — the same 7–15 rule via `is_strict_e164`,
   reached through `normalize_phone_number`. Confirmed.
3. **No dialplan mapping** — `render_inbound_dialplan` emits a line only for an
   account present in the reviewed inventory AND holding an active same-tenant
   DID assignment. An extension satisfies neither, so it fell to the catch-all,
   which passed the bare digits where a DID was expected. Confirmed.
4. **Activating an own trunk moves outbound** — `trunk_resolver.choose_outbound_route`
   treats **any** active non-platform trunk as the tenant's "own trunk" and picks
   the most recently updated one. Tenant 1845a165 routes on the shared platform
   endpoint today; activating 940003 would have won that comparison and moved a
   **running** outbound campaign onto a PBX extension with no PSTN path and no
   presentable caller-ID. Confirmed, and the most dangerous of the four.

## Why extensions did not go into the existing DID tables

Two hard facts made reuse wrong, not merely awkward:

- `inbound_did_assignments.canonical_did` carries
  `CHECK (canonical_did ~ '^\+[1-9][0-9]{6,14}$')`. Storing an extension there
  means widening the one constraint whose job is to keep public numbers strict.
- `phone_number_id` is `NOT NULL` with an FK to `tenant_phone_numbers`, and
  `trunk_resolver._select_caller_id` reads **every** `tenant_phone_numbers` row
  of a tenant to choose an outbound caller-ID. An `ext:` row parked there could
  surface as a presented caller-ID.

## What was built

**An explicit address vocabulary.** `app/domain/services/telephony/inbound_address.py`.
A DID is canonically `+<7-15 digits>` (byte-identical to before); an extension
is canonically `ext:<3-8 digits>`. They cannot collide because a DID always
starts with `+`.

**The tag is authority, not syntax.** Only the reconciler's generated dialplan
mints `ext:`, and only for an extension that passed review. The preserved
catch-all now passes untrusted input through `${FILTER(0-9+,${EXTEN})}`, so a
caller who escapes a colon into the Request-URI user part
(`sip:ext%3A940003@…`) cannot forge the tag — the colon is stripped before
Stasis, the bare digits match no binding, and admission fails closed. There is
a test for exactly that attack.

**A dedicated binding table.** Migration `0046_inbound_extension_bindings`
creates `inbound_extension_assignments`, mirroring the DID table's isolation
guarantees: tenant-paired composite FKs to campaigns/configs/trunks, forced RLS
with the repository's canonical policy, and a **global** (not per-tenant) unique
index on the extension. Global is deliberate — a DID list is per tenant, a
carrier account namespace is not, and `tenant_sip_trunks` has no unique index on
`auth_username`, so without it two tenants could each create a 940005 trunk and
both claim the extension. Additive and reversible; the deployed code is
unaffected by the revision landing before it.

**Ownership proven by the trunk.** An extension has no phone-number row, so the
router and admission both pin `st.auth_username = <extension>`. A tenant cannot
bind an extension it does not hold a trunk for.

**Blocker 4 fixed by construction.** A trunk that declares
`metadata.role = "extension"` is excluded from own-trunk outbound selection. An
extension is an address, not a PSTN route. Deliberately dialling one still works
through the explicit campaign-level assignment, which outranks own-trunk
resolution. The flag defaults absent, so no existing trunk changes behaviour.

**Provisioning.** `scripts/bind_inbound_extension.py` writes both required facts
in one transaction under the tenant's own RLS context, with an explicit
`tenant_id` predicate on every statement. It refuses rather than repairs, and
defaults to `paused` so the rendered dialplan can be reviewed before going live.

## Files

| File | Change |
|---|---|
| `app/domain/services/telephony/inbound_address.py` | new — the address vocabulary |
| `app/domain/services/telephony/inbound_router.py` | extension branch in `normalize_did`; `_lookup_active_extension_bindings` |
| `app/domain/services/telephony/inbound_admission.py` | binding lookup FROM becomes a two-source CTE |
| `app/domain/services/telephony/trunk_resolver.py` | `is_internal_extension` excluded from own-trunk selection |
| `scripts/reconcile_pjsip_configs.py` | extension assignments joined; `_extension_route_from_row` |
| `telephony/asterisk/conf/talky-inbound.conf` | catch-all filters untrusted input |
| `Alembic/versions/0046_inbound_extension_bindings.py` | new table |
| `scripts/bind_inbound_extension.py` | new — provisioning |
| 3 new test files | 57 tests |

## Not done, and why

- **940004, 940005, 940006, 940007 cannot be created.** Their SIP passwords were
  never supplied in this session. 940003 already holds stored credentials (it was
  created 2026-09-20 through the authenticated API), so it is the only one that
  can be provisioned without new input.
- **940005 and 940006 need tenant provisioning first.** AllState COnstructions.us
  (`45022490`) and Blaze DigiTel (`73d2dafd`) each fail every inbound readiness
  gate: `subscription_status=inactive`, no `tenant_inbound_controls` row, no
  concurrency policy, 30 minutes allocated, and no inbound campaign at all.
- **Outbound to an extension (blocker 2) is deliberately not wired yet.** The
  route exists (campaign-level trunk assignment), but the destination-number
  validation change should follow the inbound path landing and being proven on a
  real call, not precede it.
- **The carrier delivery path is still unproven for extensions.** On 2026-09-11 a
  packet capture showed the carrier pinging 940001 and 940002 with OPTIONS while
  they were registered, which is good evidence it will deliver INVITEs to a
  registered extension — but no INVITE for an extension has ever been observed.

## Verification

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
  -> 8994 passed, 8 skipped in 434.02s   (0 failed)
ruff check app/ scripts/ --select F --extend-ignore F401,F841 -> All checks passed!

Rolled-back rehearsal against the LIVE production database (BEGIN ... ROLLBACK),
the same pattern used for migration 0041. Proves what fake-connection unit tests
cannot: that the SQL parses and plans against the real schema.
  CREATE TABLE / 4x CREATE INDEX / ALTER TABLE x2 / DO   -> applied
  EXPLAIN router extension lookup                        -> Limit (cost=37.84..37.85)
  EXPLAIN admission lookup, EXTENSION address            -> Limit (cost=43.99..52.19)
  EXPLAIN admission lookup, DID address (regression)     -> Limit (cost=43.99..52.19)
  relrowsecurity | relforcerowsecurity                   -> t | t
  policy                                    -> inbound_extension_assignments_tenant_isolation
  ROLLBACK

Two repository guards refused the change until the new facts were declared, and
were satisfied rather than loosened:
  test_rls_set_local_invariant -> inbound_extension_assignments added to the
    RLS table inventory
  test_setup_asterisk_contract -> ${FILTER(0-9+,${EXTEN})} declared in
    DIALPLAN_VARS with the value Asterisk substitutes

End-to-end chain verified by reading, not assumed:
  carrier INVITE -> PJSIP endpoint (context from-talky-inbound)
  -> exten => 940003 -> Stasis(talky_ai,inbound,ext:940003,${CONTEXT})
  -> asterisk_adapter args[1] -> called_did
  -> normalize_did -> "ext:940003" -> extension branch -> binding
```
