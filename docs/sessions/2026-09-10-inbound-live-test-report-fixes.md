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
