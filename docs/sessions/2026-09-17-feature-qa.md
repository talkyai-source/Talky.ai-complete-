# Feature-by-feature QA — 17 September 2026

Status: this read-path/automated QA pass is complete; product acceptance is NOT all-green. Backend, frontend and Admin checks finished, and authenticated desktop read paths were inspected. No deployment, customer calls/messages, payments or saved configuration changes. An existing recording was played briefly and paused.

## Plan and evidence rules

| Step | Feature | Completion evidence |
|---|---|---|
| 1 | Release identity and operational health | Fresh server SHA/schema/services; distinguish local fixes from live code |
| 2 | Authentication and navigation | Actual browser session/account and reachable screens; no account mutation |
| 3 | Dashboard, billing and analytics | UI numbers compared with account-scoped read-only backend data |
| 4 | Outbound campaigns, contacts and Test Agent | Read/configuration pathways and backend contracts; no customer dialing |
| 5 | Inbound campaigns, DID assignment and PBX | Ownership, availability, connection status and routing boundaries |
| 6 | Calls, transcripts, summaries and recordings | Existing artifacts and persistence consistency; no forced generation/backfill |
| 7 | AI Options and voice/STT/LLM/TTS configuration | Config source and actual recent runtime evidence; no paid provider probes |
| 8 | Connectors, reminders and external actions | Capability/health contracts and failure/concurrency safeguards |
| 9 | Security and data isolation | Tenant predicates/RLS evidence and relevant automated checks |
| 10 | Final report | Per-feature pass/issue/not-tested, evidence, impact and release blockers |

Historical test totals are not new QA results. Safe local automated checks may run against a frozen deployed-source tree; production inspection remains read-only. Changes are not authorized by this QA request.

## Outcome

The service processes are healthy, but this is **not an all-features acceptance pass**. Three targeted regression checks fail on the deployed backend source. Additional live data/configuration issues remain: tenant-dependent billing visibility, an unfinished dialer job, an unprotected backup table, inactive synthetic monitoring, and an inbound DID assigned to another account.

The DID denial is a correctly enforced ownership boundary, not evidence that the boundary should be removed. This account has three inbound base drafts, but no inbound configuration or DID assignment of its own. It is not ready to receive production inbound calls under this account's configuration.

### Release and scope

- Account inspected: AllStateEstimation, `allestateestimation@gmail.com`.
- Tenant: `1845a165-08aa-4554-bcec-2d31ac523662`. This is not the older pilot tenant from previous reports.
- Production HEAD: `2f34c72ecef26827768180019a97b989e4537269`.
- Production checkout: clean at observation.
- Database revision: `0045_refresh_session_binding`.
- Fresh observations: approximately 01:46–01:59 PKT, 17 September 2026; server timestamps are 16 September UTC.
- Backend tests use an isolated worktree of the deployed commit, not the unrelated edits in the shared checkout.
- Local remediation code discussed in earlier reports is **not deployed** merely because it exists in another worktree.
- Production data inspection used read-only transactions and account predicates. Cross-account DID inspection returned only ownership booleans, not another customer's identity.

## Step 1 — Runtime, deployment and media gateway

**Result: health checks pass at observation; monitoring gap remains.**

| Check | Actual observation | Meaning / limit |
|---|---|---|
| API, voice worker, dialer, reminder, gateway, Asterisk | All six active | Running processes, not proof of successful new calls |
| API health | healthy, initialized, Redis enabled | Basic application health passes |
| Readiness | ready, not draining, zero active sessions | Idle readiness passes |
| Deep health | database and Redis OK | Dependency connectivity passes |
| Python/gateway process restarts | Five checked units reported zero restarts | Does not exclude application-level failures |
| Synthetic inbound timer | enabled, inactive, no last-trigger timestamp | Automated inbound assurance is not running |
| Gateway ready | ready, protocol 2, PCMU | Advertised media capability, not a new audio test |
| Gateway build label | `f9fccd893fa72290e87381293531d6affb76dd98` | Different from checkout label |
| Gateway source comparison | No diff in `services/voice-gateway-cpp` between gateway SHA and deployed SHA | Source content matches for this directory; not a reproducible-binary hash proof |

Gateway counters: 16 sessions started and stopped, zero active sessions; 25,138 callback batches delivered, zero failed/dropped; 50,268 input packets and 23,392 output packets; zero reported invalid packets, jitter overflows or timeout events. There were 165 interrupted TTS segments and 250 dropped TTS frames. These counters alone do not distinguish intentional interruption from audible loss.

The platform-wide fixed-signature scan of the previous 24 hours found four `telephony_audio_gap` messages in the API journal. It found no matches for the selected SQL-binding, event-emission, summary-error, traceback or unknown-origination signatures. This is a bounded signature scan, not an exhaustive clean-log certificate. The account's last real call was September 15, so low traffic limits the reassurance of a quiet log.

Evidence: [fresh health and counters](evidence/2026-09-17-health-qa.log), [corrected database and journal checks](evidence/2026-09-17-feature-database-corrected.log).

## Step 2 — Authentication, navigation and visible UI

**Result: initial browser blocker recovered; desktop read paths inspected, mutation/end-to-end flows not accepted.**

Chrome initially exposed the login page and timed out. A later retry recovered an authenticated billing page. Settings confirmed business `AllStateEstimation`, tenant prefix `1845a165`, profile name Khadija Haseeb, and Tenant Admin role. No password was reused, browser storage extracted or authentication boundary bypassed.

The following routes loaded and were inspected through actual browser controls, approximately 02:04–02:16 PKT:

| Screen | Visible result | Boundary of verification |
|---|---|---|
| Dashboard | Outbound performance, 24/5,000 minutes used, queue size 1 | No new traffic or live-call status transition generated |
| Billing | Conflicting balance/plan figures, detailed below | No purchase or plan changes |
| Settings/Profile | Correct business/tenant; 4,976 minutes remaining | No profile save |
| Settings/Telephony | Local PBX active; one registered/active trunk, two inactive | No reachability Test, activation, edit or delete |
| Campaigns | Two outbound rows only: AI Dental assist draft and dojo active | No start/pause/stop |
| Campaign detail | 16 stored calls, one active contact, 43 knowledge sections | No Test Agent connection or knowledge query |
| Contacts | Upload target dropdown contains only AI Dental assist and dojo | No upload/add/delete |
| Inbound | No inbound configurations; tenant admission enabled | No activation or DID reassignment |
| New inbound wizard | Form loads; wrong outbound-persona default is visible | No fields submitted or campaign created |
| AI Options | Cerebras 120B selected; Groq 20B offered; Flux and Hera shown | No save, synthesis preview, benchmark or LLM request |
| Analytics | Outbound view loads; selecting inbound yields no data | No claim that different date windows have identical totals |
| Recordings | Existing recordings listed; one loaded, played and paused | No download, deletion or audio-quality judgment |
| Call History | Saved calls render; DID filter and answered count defects reproduced | No notes/classifications/forms saved; no summary generation |
| Connectors | Gmail/calendar Connected; other inspected providers Disconnected | No refresh/reconnect/send/sync |
| Security | Security controls and current device load; MFA not enabled, no passkeys | No password, credential, session or MFA changes |

Desktop screenshots showed no obvious overlapping cards in the viewed inbound wizard and call-history viewport. This is not a complete responsive/accessibility audit: narrow/mobile widths, all popovers and the number-routing step remain untested. Login/logout/refresh correctness and the exact Vercel deployed commit were not verified.

Opening the new inbound page added a `draft` identifier to its URL automatically. No form input or submission was performed. The existence of that browser draft identifier is not claimed as a saved backend campaign; QA did not clear browser draft state.

The live Security page explicitly says IP allow-list enforcement and its tenant API-key/voice-security surfaces are not available. These must not be advertised as enabled capabilities. MFA being disabled is an account setting, not proof its implementation fails.

## Step 3 — Dashboard, analytics and billing

**Result: direction filtering exists; billing endpoint path remains defective.**

Fresh source inspection found the deployed dashboard active-campaign and performance queries filter outbound, with performance excluding test calls. Analytics uses its common call-population filter. The older blanket finding that these queries mix all directions is therefore not repeated as a current defect. The recovered browser also showed separate outbound/inbound analytics and only outbound campaigns in its campaign list.

The same deployed minutes calculator was executed against the same account in two read-only database contexts:

| Connection context | Allocated | Used | Remaining |
|---|---:|---:|---:|
| No tenant GUC | 5,000 | 0 | 5,000 |
| Tenant-scoped | 5,000 | 24 | 4,976 |

This demonstrates a real RLS-sensitive read. The top-up status path in `backend/app/api/v1/endpoints/billing_topups.py` calls this calculator through a bare connection acquisition; the deployed common quota service itself already uses tenant-aware acquisition. Do not generalize this finding to every quota gate.

The independent monthly duration sum is 1,481 seconds. Its mathematical ceiling is 25 minutes, while the quota implementation uses integer whole minutes, 24. **That rounding difference is not itself reported as a bug.** The reproduced defect is zero versus the correctly scoped value.

No purchase, subscription change, top-up, invoice generation or payment callback was exercised.

### Additional browser-confirmed billing inconsistency

The live Billing page shows Free Trial, 30 included minutes, 24 used and 6 remaining. On that same page, Top up minutes shows 5,000 remaining and 0/5,000 used. Settings, Dashboard and campaign detail instead show 24/5,000 used and 4,976 remaining. There are therefore two distinct mismatches: usage visibility on top-up balance, and the plan allocation used by Billing versus the other account surfaces. The first is reproduced through tenant-context probes; the second needs the subscription/allocation source reconciled before declaring a single authoritative displayed balance.

The health badge reads `Degraded` across the inspected screens while server readiness is healthy and the public same-origin `/api/v1/health` returned `status=ok`. Frozen source's badge recognizes only the literal `ok`, while server root health says `healthy`; the actual live client routing/response was not captured. This is a confirmed display-versus-probe discrepancy, **not a fully proven root cause or proof of a provider outage**.

Evidence: [account database probes](evidence/2026-09-17-account-database-qa.log).

## Step 4 — Outbound campaigns, contacts, jobs and Test Agent

**Result: data boundaries pass for the inspected account; a job-state integrity issue and database helper defect remain.**

- Non-deleted campaign population: one outbound draft, one outbound running; three inbound drafts.
- No current non-terminal account calls.
- Zero call/campaign tenant or direction mismatches in the check.
- Zero leads or dialer jobs found under inbound/other-tenant campaigns in the check.
- Previous seven days: six real answered outbound calls, one real voicemail call, one failed real call, and two completed test calls.
- No new call or Test Agent session was started by QA.

### Failing database update reproduction

`Database.update()` in `backend/app/core/db.py:424` shifts positional placeholders with repeated string replacement. With six SET columns and four ownership predicates, it emits:

```sql
WHERE id=$7::uuid AND tenant_id=$8::uuid
AND campaign_id=$9::uuid AND lead_id=$70::uuid
```

The final placeholder must be `$10`, not `$70`. The replacement for `$1` collides with an already shifted `$10`. This was reproduced using the real helper with a mocked database connection, so it proves malformed SQL construction without mutating production.

Failing test: `test_six_column_update_preserves_four_ownership_placeholders`.

### Existing job needs reconciliation

Call `bb8e6dc2-466b-4ff0-b58a-04a5ef007346` is ended, with call-side attempt number 2. Its job `e520b2a2-ccce-4837-b34c-bf2f66463dc8` is still `retry_scheduled`, attempt 1, `last_error=tenant_gap`, with no outcome or completion timestamp. This is an unresolved persistence inconsistency, not proof that another call has already been dialed.

Four other ended calls have cancelled jobs with explicit `campaign_stopped` or `removed_from_campaign` reasons. Those are not automatically classified as unsafe mismatches.

Completion criterion: repair the update helper with collision-safe placeholder handling, retain tenant/campaign/lead ownership predicates, prove concurrent/stale completion safety, then reconcile the affected job through a controlled path. None of those production changes were made in this QA run.

### Additional call-history defects reproduced in the browser

1. **DID semantics are wrong.** The DID dropdown contains the outbound contact number and `browser-test`. `distinctDidOptions()` in `Talk-Leee/src/components/calls/call-panels.tsx:214` reads all `to_number` values without a direction check; its comment incorrectly assumes outbound calls have no destination. `dashboard-api.ts:708` also fills absent `called_did` from `to_number` without checking direction. These presentation mappings blur destination and inbound DID semantics even though the database direction checks are clean. Existing tests cover missing `to_number`, not a realistic populated outbound destination.
2. **Answered group totals ignore actual outcomes.** The dojo group header displays three answered, but more than three visible rows say Answered. `classifyCall()` in `Talk-Leee/src/app/calls/page.tsx:81` counts statuses `answered`/`completed` and excludes real terminal status `ended`, even when `outcome=answered`. Conversely, a completed browser test can count as answered without an answered outcome. `groupByCampaign()` consumes that classification at line 604. Row presentation and group aggregation therefore disagree.

The page also exposes repeated hourly “Campaign is not running” events while dojo itself is active. Events can refer to a different campaign, so this is reported as noisy/unclear context rather than proof the active campaign is paused.

A local read-only probe transpiled/executed those exact source functions (not a rewritten imitation). It returned `answered=false` for `ended + answered`, `answered=true` for `completed + no outcome`, and both an outbound destination and `browser-test` as DID options. See [UI contract probe](evidence/2026-09-17-ui-contract-probe.log). These are supplementary reproductions, not cases silently added to the canonical frontend test total.

## Step 5 — Inbound setup, DID assignment and PBX

**Result: inbound setup is blocked by real ownership/configuration state.**

The account has one verified phone number. The database shows that number already has a current assignment, but not to this tenant. Invoking the deployed `InboundCampaignService.did_availability()` with a read-only pool returned:

```json
{
  "available": false,
  "reason": "reassignment_required",
  "owned_by_current_tenant": false,
  "conflicting_assignment_id": null
}
```

This is stronger than inferring a conflict from an error message: it is the actual deployed service result. The response deliberately withholds the other tenant's assignment identifier.

The three inbound campaign base rows are drafts. This account has **zero** `inbound_campaign_configs` rows and **zero** owned inbound DID assignments. A base campaign row alone does not mean the inbound setup has been completed.

The live inbound list confirms “No inbound campaigns yet.” Its new-campaign wizard visibly defaults to **Lead Generation — Outbound calls**. At the frozen source, `CampaignWizard` initializes `personaType` to `lead_gen` regardless of its inbound prop (`Talk-Leee/src/components/campaigns/campaign-wizard.tsx:75`), and the inbound page reuses that wizard with `direction=inbound` (`.../app/inbound-campaigns/new/page.tsx:93`). This is a confirmed direction-confusing default/copy issue. No campaign was submitted, and no claim is made that this switches the stored direction to outbound.

Resolution requires an explicit, audited ownership/reassignment decision and completion of inbound configuration. Removing the conflict guard, assigning the same number twice, or treating verification as inbound-routing ownership would create a cross-tenant risk.

### PBX evidence

| Trunk prefix | Active | Direction | Reported registration |
|---|---|---|---|
| `1c12e670` | Yes | both | registered |
| `6b01062c` | No | both | inactive |
| `8b5fa0f1` | No | both | inactive |

The registration updater timestamps were approximately five seconds old at the query. This is recent updater evidence, not a direct SIP transaction test. Direct Asterisk CLI inspection as `admins` was denied access to its configuration/control socket; that permission failure is not evidence that Asterisk is down.

Legacy shared-route fallback remains in deployed resolver source. Stronger PBX grant/reconciliation changes in other local worktrees are not assumed live. No route was changed and no config regeneration, reload, SIP registration or transfer was triggered.

Evidence: [actual availability service and metadata](evidence/2026-09-17-artifact-service-qa.log), [trunk/configuration evidence](evidence/2026-09-17-feature-database-corrected.log).

## Step 6 — Recordings, transcripts and summaries

**Result: recording files exist and one browser playback works; summary contract is inconsistent.**

The rolling 30-day account population was 218 real outbound calls and 17 tests. Sixty real calls had transcripts; five had summaries; 55 had transcripts without summaries. Nine test calls had transcripts, one had a summary, and eight had transcripts without summaries.

Thus 63 transcript-bearing calls lacked summaries at observation. This is an artifact gap, **not proof of 63 failed provider requests**: the summary endpoint can generate lazily. QA deliberately did not call that endpoint because doing so could write data and incur provider usage.

The summary prompt asks for object entries in `objections` and `action_items`, but `_SUMMARY_SCHEMA_PROPERTIES` declares every list as an array of strings. The targeted test parses the actual prompt example and compares it with the actual submitted schema; it fails on `objections` (`string` versus `dict`).

Root cause: deriving item types from empty-list defaults loses the object structure. This contradiction can interfere with constrained model output and downstream rendering, but no new provider request was issued to quantify that effect.

Failing test: `test_summary_prompt_example_matches_submitted_schema`.

Recording storage metadata describes 141 objects as `storage_provider=s3`, while their bucket is `local`. A full account-scoped filesystem metadata check found:

- 141 files exist.
- 141 files are nonempty.
- 141 sizes match database metadata.
- No missing or out-of-root files were observed.

This disproves a missing-file claim for the checked set. In the recovered authenticated browser, the newest 3:13 recording loaded its duration, switched to Pause while playing, advanced to 0:07 and was paused back to Play. This proves retrieval and playback controls for that sample, not all 141 recordings. Audio intelligibility, retention, backup restore and off-host durability remain unverified. The storage label should not be read as evidence these files are in an external S3 bucket.

Evidence: [artifact metadata](evidence/2026-09-17-artifact-service-qa.log), [call population](evidence/2026-09-17-feature-database-corrected.log), [failing test XML](evidence/2026-09-17-deployed-backend-qa.xml).

## Step 7 — AI Options, campaign prompts, STT/LLM/TTS and lead capture

**Result: configuration is present; one confirmation behavior fails; real speech acceptance remains untested.**

Tenant configuration currently selects Cerebras `gpt-oss-120b`, Deepgram with `stt_engine=deepgram_flux` and `stt_model=nova-3`, and Deepgram Aura-2 voice `aura-2-hera-en` in the cascaded pipeline. Flux engine and Nova fallback-model settings coexisting are not, on their own, evidence of contradictory wiring.

The running outbound campaign `2953b876` has a 6,767-character system prompt and an explicit ElevenLabs voice override. Its different voice from the tenant default is not automatically a generic-voice bug. QA has not proved that the next audible call uses the selected voice or follows every prompt instruction.

No current account configuration was found selecting Groq as its primary LLM. This run does not claim live Groq model availability, provider credentials, latency, failover or audio quality. It made no paid inference/synthesis/transcription requests.

### Failing email confirmation reproduction

The deployed `_agent_read_back_email()` rejects a full spoken address followed by:

> “Should I send the rewards card details there?”

The test includes the full preceding address readback. Its expected confirmation window is not opened because the narrow confirmation-question matcher does not recognize this wording. A later affirmative reply can therefore fail to confirm/persist the captured email even though the conversation sounds complete.

Failing test: `test_observed_full_email_readback_and_send_question_opens_confirmation`.

Production structured capture currently contains two unconfirmed `follow_up` entries and no confirmed email/phone entries in the inspected rows. That observation is consistent with incomplete capture but is not proof that every missing field was caused by this one matcher.

A safe fix must preserve the requirement that the complete address was actually read back. Simply making any “yes” confirm a pending address would be a regression.

## Step 8 — Email, calendar, CRM, reminders and assistant actions

**Result: configuration inspected; external functionality not accepted.**

- Gmail: one active connector; access and refresh credential fields present.
- Google Calendar: one active connector; access and refresh credential fields present.
- Both access tokens are past their stored expiry. With refresh credentials present, expiry alone does **not** prove the integrations are broken.
- No credentials were decrypted or printed; refresh and provider authorization were not exercised.
- HubSpot: six pending connectors; Salesforce: one pending connector. Pending is not connected.
- No reminder rows or recent assistant-action records were returned for this account.
- Direct local execution of the deployed voice-action dispatcher with an inert session returned `success=false`, `status=unavailable`, `confirmation_allowed=false` for all four actions: callback scheduling, email sending, form submission and transfer. These are deliberately unavailable capabilities, not successful deliveries hidden in another table.
- The native action-tool capability predicate also returns false for a Cerebras provider, even when the stub exposes a callable tool-streaming method: its provider-name allowlist contains Groq and Gemini. This proves that specific native-tool path is excluded for the account's configured primary provider; it does not prove ordinary Cerebras conversation is broken or that all textual intent handling is absent.

Empty tables are not proof of working scheduling, delivery, retries or exactly-once execution. Those require controlled test data and explicit external-action tests. No email, meeting, CRM write or reminder was sent/created by this QA run.

Evidence: [executed voice-action contracts](evidence/2026-09-17-voice-action-qa.log); source `backend/app/domain/services/voice_pipeline/action_tools.py:250` and `:379` at the frozen deployed commit. These probes execute only local deterministic code, not providers.

## Step 9 — Tenant isolation and security

**Result: inspected live relationships are clean; one table protection gap remains.**

The app role is neither superuser nor BYPASSRLS. The inspected account's call/campaign relationships and outbound lead/job relationships had zero cross-tenant/direction violations.

The tenant-table metadata inventory found `tenant_ai_configs_backup_20260907` with RLS disabled, FORCE RLS disabled and zero policies. The app role has SELECT and write privileges. This account has zero rows in that backup table.

This is a real defense-in-depth gap on a tenant-bearing table. It is not a demonstrated public API exploit or evidence that this account's data was disclosed. A production migration or removal of an obsolete backup needs controlled review; neither was performed here.

The full backend security suite ran as part of the canonical test invocation. Passing security tests does not erase this separately observed live-schema finding.

## Step 10 — Automated verification

### Backend: fresh complete run

```text
python -m pytest tests/unit tests/security -q --no-header -p no:cacheprovider -W ignore --tb=short -o junit_family=xunit1 --junitxml=<evidence XML>
3 failed, 8932 passed, 8 skipped in 440.17s (0:07:20)
```

The XML contains 8,943 cases, three failures, zero errors and eight skips: 8,213 unit cases and 730 security cases. All three failures are the added QA reproductions described above, not unexplained failures of an unchanged test. They intentionally exercise desired behavior that the deployed code fails. The existing collected suite passes, but the enlarged suite is **red**, not green.

The tests execute locally against frozen deployed source. They are not 8,932 production customer journeys and do not prove calls, payments or external integrations end-to-end.

- Canonical Ruff gate: `All checks passed!`
- Local Alembic head: `0045_refresh_session_binding (head)`.
- Production schema: the same revision.

Skipped checks must not be counted as accepted behavior:

| Skipped check | Reported reason |
|---|---|
| Two float32-to-telephony resampling checks | librosa required |
| Public-route inventory consistency | Stale allowlist entry for DELETE `/api/v1/rbac/roles/{role_id}/permissions/{permission_id}` |
| Groq caching-menu check for `openai/gpt-oss-120b` | Model not offered by the tested menu |
| PJSIP generated file permissions | POSIX-only mode assertion |
| Real recording compression check | ffmpeg unavailable locally |
| Two systemd notification socket checks | AF_UNIX unavailable on this Windows test environment |

The skipped route-inventory check is test-maintenance debt, not a demonstrated reachable unauthenticated endpoint. The skipped audio/platform checks require Linux/media-tool verification rather than being silently treated as passes.

### Admin: fresh complete run

```text
npm run lint
npm test
tests 13; pass 13; fail 0; skipped 0
npm run build
1764 modules transformed; built in 39.63s
ADMIN_QA_EXIT_CODES lint=0 test=0 build=0
```

Admin checks use deployed-source files with the existing local dependency installation, whose lockfile matches this frozen source. Local Node is v25.8.1, not the project's Node 22 CI baseline; this is not a substitute for that CI/runtime matrix. The successful build was not deployed.

### Customer frontend: fresh complete run

```text
npm run typecheck
npm run lint
npm test
tests 468; suites 3; pass 466; fail 0; skipped 2
duration_ms 713828.0099
FRONTEND_QA_EXIT_CODES typecheck=0 lint=0 test=0
```

Its dependency installation uses the frozen source's lockfile (`npm ci --ignore-scripts`); previous days' green totals are not substituted. Evidence: [complete frontend output](evidence/2026-09-17-frontend-qa.log).

The two skips are database-backed session tests: absolute expiry/idle timeout/binding/rotation, and role/scope-change rotation with usage/billing mapping. They are not verified by this local frontend run. The canonical customer-frontend gate does not include a production build, and no customer-frontend build or Vercel deployment was performed.

The green frontend suite does not contradict the live UI failures: it lacks realistic coverage for the specific direction/terminal-outcome combinations reproduced above. The supplementary source-function probe is intentionally reported separately.

## Prioritized follow-up and pre-mortem

| Priority | Work required | Risk if handled incorrectly | Acceptance proof required |
|---|---|---|---|
| High | Resolve DID ownership and complete inbound configuration | Cross-tenant routing or breaking the existing owner | Audited reassignment, availability result, controlled inbound call |
| High | Correct tenant context for billing status | False available balance; unsafe broad bypass | Same tenant totals through UI/API and scoped SQL; tenant-isolation tests |
| High | Protect backup tenant table | Accidental broad access through future queries | Migration checks, app-role cross-tenant denial, rollback plan |
| High | Fix positional SQL rewriting and reconcile stale job | Duplicate/omitted calling or lost completion | Failing-then-passing helper test, stale-worker/concurrency tests, inspected job state |
| Medium | Align summary prompt/schema/consumer | Provider rejection or malformed rendering | Schema contract, parser/UI tests, controlled real summary |
| Medium | Correct email confirmation recognition | Captured address silently lost, or overly broad false confirmation | Full-readback positive tests plus partial/wrong-address negative tests |
| Medium | Make DID filters direction-aware | Caller destinations mistaken for inbound numbers | Realistic outbound/test rows excluded; actual inbound DID retained |
| Medium | Correct answered group aggregation | Tests counted as real answers; real answers omitted | Ended/answered, voicemail, failed and test cases agree between rows and totals |
| Medium | Fix inbound wizard persona defaults/copy | Receptionist setup starts with outbound assumptions | New inbound defaults and prompt preview tested without changing stored direction |
| Medium | Reconcile Billing plan allocation with account quota | User sees 6, 4,976 and 5,000 as competing balances | One authoritative plan/usage contract across all account screens |
| Investigate | Resolve the persistent Degraded badge discrepancy | False operational alarm, or real degradation hidden behind a basic health probe | Capture the badge's actual response and test normalization/error/loading states |
| Medium | Restore synthetic monitor scheduling | Inbound outage unnoticed | Timer execution evidence and alert delivery without calling customers |
| Medium | Validate local recording backup/retrieval | Files lost despite an “uploaded” label | Authenticated playback and isolated restore test |
| Acceptance | Complete browser and real-call QA | Declaring readiness from idle health only | Logged-in desktop/mobile checks, safe test destinations, artifact/billing inspection |

## Not done and why

- No fixes, deployment, migrations, config reloads or data reconciliation: this request was QA.
- No real inbound/outbound calls, transfer, microphone tests or speech-quality assessment: those require controlled destinations and a live acceptance run. One existing recording was played and paused without judging its intelligibility.
- No payment, email, calendar, CRM or provider activity: these have external effects and require controlled acceptance cases.
- No automatic DID reassignment: another account currently owns the assignment.
- No full authenticated write-flow/mobile UI acceptance or Vercel revision verification. The initial browser blocker recovered and desktop read paths were inspected as recorded above.
- No inference that quiet logs or idle readiness establish production readiness.

The next acceptance phase needs controlled test destinations and the unresolved defects fixed, followed by write-flow and real-call acceptance. This report distinguishes observed failures from untested features so that neither gets hidden behind a large green test count.
