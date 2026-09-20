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
   `940003` returns `None`. Confirmed.

   **Correction (found by the adversarial trace, after the first commit):** the
   live Asterisk path never reaches `resolve_inbound_route`. It is
   StasisStart → `asterisk_adapter._extract_inbound_meta` → `_on_stasis_start`
   → `lifecycle._admit_inbound_call` → `InboundAdmissionService.admit`, which
   denies at its own `normalize_did` call before any query. `resolve_inbound_route`
   is reached only from `tenant_ai_config_resolver.resolve_ai_config_for_did`,
   whose sole callers are the Twilio and Vonage bridges. Both paths are fixed
   here, but admission — not the router — is the one that matters for a call
   arriving on the PBX.
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

## Second pass: an adversarial review of the committed design

A four-lens adversarial critique ran against the design after it landed as
`12db6420` and found five real defects. Four are fixed here; the two verdicts
that mattered were `needs-change`, not `sound`.

1. **A tenant could freeze every deploy** (major, fixed). The extension branch
   raised `UnsafeInboundMappingError` for conditions reachable through the
   ordinary trunk API — `PATCH /telephony/sip/trunks/{id}` replaces `metadata`
   wholesale and can change `direction`. Since `deploy_to_server.sh` runs the
   reconciler's `--check-only` as a preflight, any tenant clearing
   `metadata.role` by saving a form would have aborted the platform-wide
   candidate build and blocked every deploy. Those conditions are now **named,
   not raised**: `CandidateSet.unrouted_extension_trunks` reports them and the
   account stays on the fail-closed catch-all. Only genuine database-integrity
   violations still raise. This is the same lesson as 2026-09-10, when a paused
   campaign raising instead of being named froze the reconcile.

2. **A trunk that can never register would still have rendered a route**
   (blocker, fixed). `metadata.register` defaults **False**, and without it
   `pjsip_config_generator` emits no `[trunk-<id>-reg]` section, so Asterisk
   never REGISTERs and the carrier holds no contact to deliver an INVITE to —
   while `trunk_runtime` still reports the trunk ready. Both the reconciler and
   the binding script now refuse. (The existing 940001-940003 rows already have
   `register=true`, so this was latent, not live.)

3. **An extension could hijack a reviewed public carrier account** (major,
   fixed). `tenant_sip_trunks` has no unique index on `auth_username` and the
   extension CHECK `^[0-9]{3,8}$` admits `150001`, so any tenant could claim
   another tenant's real DID account. The binding script now refuses digits
   listed in `verified-carrier-account-dids.json` or already held by another
   tenant's active trunk, and the reconciler refuses to render one.

4. **Cross-carrier extension collision** (blocker, contained). Extension digits
   are unique only inside one carrier's namespace, and every inbound endpoint
   deliberately enters the same `[from-talky-inbound]` context because a shared
   source IP cannot identify a unique endpoint. With two carrier hosts in play,
   a second carrier's caller dialling 940003 would match the first tenant's
   route. Containment: the reconciler renders **no** extension route once more
   than one carrier host has an extension binding, and names why. Verified
   latent today — every trunk in the fleet is on `sip3.blazedigitel.com`. The
   structural fix (per-carrier dialplan contexts) is follow-up work.

5. **An extension caller was recorded as withheld** (major, fixed). A MicroSIP
   caller presents `940007`; `normalize_did` refuses it as a phone number and
   `_private_ani` therefore returned `None`, marking the call caller-withheld —
   recording that the caller hid their identity when they had not. It now keeps
   the identity in canonical tagged form.

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
  -> 9013 passed, 8 skipped in 610.33s   (0 failed)   [after the hardening pass]
ruff check app/ --select F --extend-ignore F401,F841                  -> All checks passed!
ruff check scripts/reconcile_pjsip_configs.py scripts/bind_inbound_extension.py -> All checks passed!

Beyond brief, not fixed: scripts/check_groq_api.py has 3 pre-existing F541
(f-string without placeholders). Untouched by this work; the canonical gate is
app/ only, so it is not a new failure.

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


## Third pass: one assignment table, not two (2026-09-21)

Building the next increment surfaced a defect in the shipped design that no test
could have caught, because it lives in a foreign key rather than in code.

**Three tables reference `inbound_did_assignments`:** `calls.assignment_id`
(composite, tenant-paired), `inbound_rejections.assignment_id`, and
`inbound_reassignment_requests`. Admission writes `assignment_id` on the `calls`
row for every admitted inbound call. An extension-addressed call would have been
routed, admitted, **answered**, and then failed the foreign key on INSERT,
because its assignment id lives in a different table. The caller would hear the
agent pick up and the call would die.

Patching around it meant a parallel nullable column plus a branch on every one of
those integrations, permanently. Folding the two kinds into one table instead
removes the problem and removes branching:

* `inbound_did_assignments` gains an `extension` column; `canonical_did` and
  `phone_number_id` become NULL-able.
* `inbound_assignment_address_kind_exactly_one` makes the discrimination
  structural — exactly one address column is set, and a DID row must still carry
  its verified phone-number row.
* The E.164 CHECK is **not** weakened. It becomes NULL-tolerant so an extension
  row can leave the column empty; a non-NULL value must still match
  `^\+[1-9][0-9]{6,14}$`.
* No `ext:` value goes anywhere near `tenant_phone_numbers`, so the outbound
  caller-ID path is untouched — the original reason for a separate table.
* `uq_inbound_active_extension` / `uq_inbound_live_extension` mirror the DID
  uniqueness rules and are global, because a carrier account namespace is global.

The router's extension lookup, the admission query and the reconciler all
collapse back to one table. The admission CTE is gone entirely.

Because the revision had never been applied to any database (production is on
`0045`), the correction was folded into `0046` rather than left as a
create-then-drop in the migration chain.

### Rehearsed against production data, rolled back

```text
existing rows the new CHECK must accept   -> 3 total, 3 with_did, 3 with_phone
ALTER TABLE x7, CREATE INDEX x2           -> applied
EXPLAIN router extension lookup           -> Limit (cost=30.81..30.82)
EXPLAIN admission, EXTENSION address      -> Limit (cost=22.96..31.16)
EXPLAIN admission, DID address            -> Limit (cost=22.96..31.16)   (unchanged)
INSERT both address columns set           -> ERROR: violates
                                             inbound_assignment_address_kind_exactly_one
INSERT extension-only row                 -> accepted
INSERT canonical_did = '940003'           -> ERROR: violates
                                             inbound_did_assignments_canonical_did_check
ROLLBACK
```

### Still not built

The inbound routing config itself is still DID-only: `create_campaign` requires
`did_number` and writes a `tenant_phone_numbers` row. Giving a config its own
extension address — so a tenant with no public number can have an inbound
campaign at all — is the next increment. This pass made that possible by fixing
the storage model underneath it; it did not deliver it.
