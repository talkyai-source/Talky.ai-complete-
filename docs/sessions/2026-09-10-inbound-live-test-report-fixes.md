# 2026-09-10 — Inbound live-test report: what was real, what was stale, what changed

Input: the frontend tester's `Inbound_Testing_Issue_Analysis.pdf` (tenant CodeAlpha `5e666d8a`)
and the owner's screenshot of the Calls page (tenant AllStateEstimation `1845a165`) showing a
red banner `Call could not start — … pre_originate_warmup_timeout` over an inbound-test panel
reading "11 calls, 0 answered, 5 failed".

## Evidence first (prod DB + journal, read this morning)

| Claim | Fact | Verdict |
|---|---|---|
| Banner: "pipeline not ready within 5.0s — refusing to ring" is happening now | The newest `stream_events` row with that text is dated **2026-08-20**. `getRecentCallIssues` asked `/events?category=call&severity=critical&limit=8` with no `since`, so the page showed the newest critical event ever recorded as if it were live. | stale UI, real bug in the banner |
| Inbound calls fail | Two inbound calls on 2026-09-08 15:09:51 and 15:10:02 (tenant `790ca2db`) were fenced `inbound_handoff_fenced reason=lifecycle_handoff_cancelled` within 1 s of session start. The 07:12 call the same morning, same code, succeeded. Nothing in the log said which terminal event (parent hangup, ExternalMedia leg death, StasisEnd) caused the fence or with what hangup cause. | real, cause not yet identified — observability was missing |
| `tenant_active` blocker | `tenants.subscription_status` defaults to `inactive`; 11 of 12 tenants sit there. The only activation path was a Stripe checkout, and prod has no Stripe keys, so the Free Trial button could not activate anyone. | real platform gap |
| `tenant_inbound_enabled` blocker | Self-serve "Enable inbound" button exists on the Inbound campaigns page; `inbound:controls` is granted to `tenant_admin` in prod. The tester did not find it. | real for the tester, feature exists |
| `concurrency_policy_configured` blocker | `tenant_telephony_concurrency_policies` had one row in the whole database (`790ca2db`, hand-inserted 2026-08-30). No endpoint or UI ever created one. Every other tenant was blocked here. | real platform gap |
| Test agent on the inbound page does nothing useful | `campaign_test_ws.py:625-637` refuses non-outbound campaigns (`inbound_campaign_managed_separately`); `direction = Direction.OUTBOUND` is hardcoded in that WS. The button rendered, then failed after the click. | real, by design of the WS |
| Wizard review shows "Hello?" for an inbound campaign | `campaign-wizard.tsx` preview payload hardcoded `direction: "outbound"`. | real, cosmetic |

## Changes

Backend (`backend/`):
- `app/domain/services/billing_service.py` — `create_checkout_session` selects `price, minutes`;
  a plan with `price <= 0` is activated server-side by `activate_free_plan` (tenant →
  `subscription_status=active`, `plan_id`; minute allocation; audit) and returns
  `{checkout_url: success_url, activated: true, mock_mode: false}`, which the Plans page already
  follows as a redirect. Paid plans without Stripe keep the mock-mode response unchanged.
- `app/domain/services/inbound_campaign_service.py` — `create_campaign` calls
  `_ensure_default_concurrency_policy` after the `tenant_inbound_controls` insert: one
  `INSERT … WHERE NOT EXISTS (active policy)` named `inbound-default`, limit =
  `GREATEST(1, plan.concurrent_calls)`.
- `app/infrastructure/telephony/asterisk_adapter.py` — `_cancel_inbound_setup_for_terminal`
  logs `inbound_setup_terminal channel= event= cause= setup_inflight= handoff_pending=
  active_session=`; the ExternalMedia-leg path tags its event `…:external_media_leg`;
  `inbound_handoff_fenced` now carries `cause=`. This is the diagnosis the next fenced call
  will give us.
- `tests/unit/test_inbound_activation_gaps.py` — 7 tests (free plan activates; paid stays
  mock; `_plan_is_free` strictness; policy seeded / left alone; fence log content).

Frontend (`Talk-Leee/src/`):
- `lib/extended-api.ts` — `getRecentCallIssues` passes `since` = now − 24 h
  (`RECENT_CALL_ISSUE_WINDOW_MS`); the events endpoint already supported `since`.
- `components/calls/call-issues-banner.tsx` — shows the issue's age ("21 days ago") next to
  the title via `issueAge`, so a stale failure can never read as live again.
- `app/inbound-campaigns/[id]/page.tsx` — Test-agent button replaced by a note: the browser
  Test agent runs outbound campaigns; test an inbound line by calling its number.
- `components/campaigns/campaign-wizard.tsx` — review preview sends the campaign's real
  direction; for inbound the label reads "default opening" and says the exact greeting is set
  in step 2.
- `components/calls/call-issues-banner.test.ts` — age labels + 24 h window.

## Prod steps (owner runs; script uploaded to `/home/admins/probes/ops_inbound_0910.sh`)

1. `deploy_py.sh <sha> 283d21a6` (also carries the pending reconciler budget fix 4bcc2621).
2. Asterisk reconcile with the 90 s busy budget; read-back of context, registrations,
   `blaze-pbx-940001/2` status.
3. One-off SQL in the same run: `inbound-default` policy for tenants that already hold an
   inbound config but no active policy (today only `5e666d8a`), and `5e666d8a` on the free plan
   → `subscription_status=active` (exactly what the new free-plan flow does on click).
4. Then: the tester clicks **Enable inbound** on the Inbound campaigns page, and one live
   inbound call to the tenant's number produces the first `inbound_setup_terminal` line if it
   fences again.

## Not done / not verifiable here

- Root cause of the two 2026-09-08 fences is **not** identified; the change above makes the next
  one diagnosable. Needs a live inbound call after deploy.
- Test agent for inbound campaigns is a feature (the WS is outbound-only); not built.
- Other 10 inactive tenants are left as they are; they self-activate the free plan on click.

## Verification (this turn)

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q  -> 8925 passed, 8 skipped in 545.66s
ruff check app/ --select F --extend-ignore F401,F841                  -> All checks passed!
Talk-Leee: npm run typecheck -> clean · npm run lint -> 0 errors · npm test -> tests 468, pass 466, fail 0
Talk-Leee: npm run build -> exit 0, ✓ Compiled successfully in 62s, 69/69 static pages
```

## Deploy run 1 (owner, `ops_inbound_0910.sh 585ec451 283d21a6`)

- Backend deployed: prod HEAD `585ec451`, four units active, health/deep/workers 200, 0
  tracebacks. Seed applied: `5e666d8a` policy `inbound-default max=1`, subscription
  `inactive → active` (free plan). `inbound_enabled` still `false` — tester's button.
- **Reconcile blocked at the check stage**, before any file was touched:
  `trunk-44b41a0d… verified carrier route lacks a matching active assignment`.
  Cause, from prod rows: the AllStateEstimation inbound campaign config `2ab6f5b3` went
  `paused` (v9) at 2026-09-09 14:20:24, five minutes after its owner user logged in; pausing
  moves assignment `88af692d` (+442046132300 on trunk 44b41a0d, account 150001) to `paused`.
  The reconciler treated "verified carrier account with no active assignment" as a hard
  block, so one tenant pausing its campaign froze the platform-wide Asterisk reconcile and
  kept two other tenants' trunks (`blaze-pbx-940001/2`) at `missing_config`.
- Fix (`backend/scripts/reconcile_pjsip_configs.py`): the account stays on the fail-closed
  catch-all (admission denies the DID anyway without an active assignment) and is named in
  `CandidateSet.unrouted_verified_trunks` and the CLI JSON (`unrouted_verified_trunks`). The
  test that encoded the block now asserts the new invariant for both "no assignment" and
  "paused assignment"; a routed account is asserted not to be reported. Module: 50 passed.
- Second run script: `ops_reconcile_0910.sh <sha> 585ec451` (deploy + reconcile + read-back,
  no seed). The paused campaign is the owner's to resume from the Inbound page; the DID
  answers again the moment it is resumed and the reconcile has run.

## Deploy run 2 (owner, `ops_reconcile_0910.sh dca612a6 585ec451`) — converged

- prod HEAD `dca612a6`; reconcile `mode=apply`, exit 0: 5 files changed (shared `pjsip.conf`
  + 4 trunk files), 3 created (`extensions.d/talky-inbound.conf`, trunk files for
  `blaze-pbx-940001/2`); `route_count: 0`, `unrouted_verified_trunks: [trunk-44b41a0d…]`.
- Shared endpoint context is now `from-talky-inbound` on disk AND at runtime (first time the
  managed dialplan is live; the hand-written 08-30 block in `from-blazedigitel` is no longer
  what carrier calls hit). Managed context holds the fail-closed catch-all only.
- All 7 registrations up; `blaze-pbx-940001` / `blaze-pbx-940002` = `registered` in the DB.
- Consequence to know: the dialplan route `150001 → +442046132300` is rendered only while
  the tenant's DID assignment is active, and nothing re-renders on pause/resume. After the
  owner resumes campaign `2ab6f5b3`, `ops_reconcile_only.sh` must run once (uploaded; no
  deploy, no restart). Until then calls to +442046132300 hit the catch-all and admission
  denies them — the same outcome a paused campaign gives.
- Design note (owner decision, not done): rendering account→DID routes from the reviewed
  carrier inventory alone, and letting admission decide by assignment state, would make
  pause/resume need no reconcile at all. The reconciler's own rationale ("the inventory is
  the proof of the Request-URI") is compatible with that.
- Tester tenant `5e666d8a`: its assignment is for `+17789249977` on trunk `2e8f65f7`; that
  number is "verified" for 11 tenants and its account is not in the carrier inventory, so
  inbound to it can never route. A real DID on a reviewed carrier account is needed before
  CodeAlpha can receive a test call.

## Production-readiness audit (owner: "make sure incoming and outgoing … ready for production")

Read-only probe of the live box (`/home/admins/probes/readiness_probe.sh`) + journal + DB, then code.

### Healthy (evidence read this turn)
- Units: api / dialer / voice / reminder / gateway / asterisk / nginx / fail2ban / docker all
  `active`, 0 restarts. Workers heartbeat ≤ 21 s. `/health`, `/healthz/deep`, `/healthz/workers`
  all 200 in < 5 ms. Gateway `/ready` = build `ca8a136a`, protocol 2, PCMU.
- Alembic head on prod = code head (`0045_refresh_session_binding`). App role: not superuser,
  no BYPASSRLS; 84 tables FORCE RLS; the 6 "tenant_id without policy" relations are 5 views +
  one 3-row backup table (`tenant_ai_configs_backup_20260907`, no secrets).
- RBAC seeded (156 role_permissions, 19 tenant_users, `inbound:controls` granted ×3).
- Port 8000 is bound on 0.0.0.0 but filtered from the internet (connect timeout from outside);
  public path is Cloudflare → nginx :443 → 127.0.0.1:8000. Frontend on Vercel answers 200.
- Outbound: calls completed on Sep 7/8 (durations 15–212 s); one running campaign (`dojo`,
  1845a165, the team's own numbers); dialer 0 stuck jobs, reaper working; `outbound_calls_paused`
  false. Tenant rules honour DNC (`skip_dnc` = "skip leads on the DNC list").
- Inbound: Sep 3–8 calls answered daily except the two Sep 8 fences (now diagnosable); routing
  live via the managed dialplan; policies + controls in place for 790ca2db / 1845a165 / 5e666d8a.
- Disk 60 % of 47 GB, RAM 2.5 GB available of 3.9 GB, NTP synced, 0 pending security updates,
  Asterisk log rotating, fail2ban SIP jail active, TLS by Cloudflare + certbot timer.

### Found and fixed (code)
1. **8 identical 500s/day in talky-api** — `Session security middleware error:` +
   `Unhandled exception` every minute for an idle user. Traceback: `validate_session` →
   idle timeout → `_revoke_by_id` UPDATE → asyncpg `TimeoutError`. Cause: the middleware ran
   `call_next` INSIDE `acquire_with_tenant`'s transaction, so its uncommitted revoke held the
   row lock the endpoint's own `get_current_user` → `validate_session` then waited on. The 500
   also dropped the cookie-clearing header, so the browser looped with the dead cookie. Fix:
   validate, release the connection, then act; a lookup failure runs the request once and names
   the exception. Tests: `test_session_middleware_rls_bypass.py` (+2, 5 passed).
2. **No scheduled database backup.** Only hand-run pre-migration dumps existed, and the three
   Aug 28–30 `.dump` files are schema-only (identical 22 MB, 0 TABLE DATA — pg_dump without the
   RLS bypass). Added `deploy/db-backup.sh` + `systemd/talky-db-backup.{service,timer}`
   (02:30 UTC nightly, bypass GUC on the dump connection, refuses < 50 TABLE DATA entries or a
   dump under half the previous size, sha256, 30-day retention), enabled in
   `install-services.sh`. Guard tests: `test_db_backup_unit.py` (3 passed).

### Found, prepared as ops (owner runs `ops_prod_ready_0910.sh <sha> dca612a6`)
- `avahi-daemon` (mDNS on 0.0.0.0:5353), `cups`, `cups-browsed` running on the production box
  (desktop leftovers; `google-chrome` cron too) → stop + mask.
- journald at 3.5 GB with no cap → `SystemMaxUse=1G`.
- First verified backup run + timer enabled.

### Found, owner decision (not done)
- **Kernel reboot pending** (`/var/run/reboot-required`, running 6.17.0-20, two newer installed;
  uptime 150 days). Needs a call-free window: Asterisk, gateway and workers all restart.
- **No Stripe keys** → only the free plan can activate; paid plans are mock-mode.
- **No alerting sink** (`SENTRY_DSN` unset). `talky-healthwatch` writes CRITICAL journal lines
  every 2 min when workers are unhealthy, but nothing pages anyone.
- `talky-inbound-synthetic.timer` deliberately off (each probe is a billed 15 s call).
- Tenant 1845a165 rules: caller ID `+17789249977` (a placeholder), window 00:00–23:59 on
  Mon–Fri. Its `dojo` campaign is running against the team's own numbers (fine); any real list
  on this tenant would dial at any hour.
- `ufw` inactive; the repo's `scripts/firewall-setup.sh` models the OpenSIPS port plan (5080),
  not Asterisk (5060), so it must not be applied as-is. The external filter on :8000 exists
  (Hetzner side); UDP 4569 (IAX2) is open and unused → `noload => chan_iax2.so`.
- One order-dependent flaky test seen only under `-k` subsetting
  (`test_postgres_adapter.py::test_auth_get_user_uses_local_jwt_secret`), passes alone and in
  the full suite.

### Verification
```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q -> 8932 passed, 8 skipped in 594.72s
ruff check app/ --select F --extend-ignore F401,F841 -> All checks passed!
bash -n backend/deploy/db-backup.sh -> ok
```

## 2026-09-11 — both AllStateEstimation tenants provisioned (owner request)

Owner: "check all state estimation … have all minutes needed … inbound outbound fully active".
Read-back after `ops_allstate_0911.sh` (DB-only, no service touched):

| tenant | subscription | minutes | inbound_enabled | policy | recording | trunks | verified numbers |
|---|---|---|---|---|---|---|---|
| 790ca2db AllStateEstimation.co | active / free | 5000 (28 used 30 d) | true | inbound-default/10 | two_party | 4/4 registered | +442046132300, +17789249977 |
| 1845a165 AllStateEstimation (gmail) | **inactive → active** / free | **530 → 5000** (237 used 30 d) | true | **none → inbound-default/10** | one_party | 3/3 registered (incl. blaze-pbx-940001/2) | +17789249977 only |

The change to 1845a165 was applied by SQL because the hash-chained `audit_logs`
(`entry_hash` NOT NULL, chained) cannot be written by hand; this section is the record.
Platform switches: `outbound_calls_paused=false`, `inbound_enabled=true`, `inbound_settlement=true`.
SMTP: host/port/user/password/from address/from name all set on prod; no mail failures in 24 h.
Still open: 1845a165 has no real inbound DID (its only verified number is the shared placeholder);
the +442046132300 test call produced no SIP trace on the box — packet capture
(`sip_capture_root.sh`) pending.
