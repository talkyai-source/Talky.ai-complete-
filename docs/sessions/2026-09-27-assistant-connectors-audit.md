# Assistant, voice actions and connectors: issue-first audit

Date: 2026-09-27. Verdict: **not fully functional end to end**.

Scope: deployed revision `cad3f7d1`, dashboard assistant, live voice-action
contract, Gmail/Calendar/Drive, HubSpot and Salesforce. The target account was
resolved from the previously investigated call; the findings about connection
state apply to that account, not every tenant. “Sales Hub” was not found as a
separate product module, so both HubSpot and Salesforce were checked.

This was an investigation. No application fix, deployment, token refresh,
external email/SMS, calendar booking, CRM write, or test phone call was performed.
Production SQL ran inside read-only transactions. Credentials and customer
conversation contents are excluded from this report.

## Confirmed production account state

| Area | Evidence | Assessment |
|---|---|---|
| API / call services | `/health` healthy; API, dialer, voice worker, voice gateway and reminder worker active | Infrastructure up; does not establish feature correctness |
| Gmail | Connector and account both `expired` | Active-connector resolver will not select this mailbox |
| Google Calendar | Connector/account `active`, stored access token expired, refresh token present | Read path can attempt refresh; successful provider access not verified |
| Google Drive | Connector/account `active`, stored access token expired, refresh token present | Same limitation; badge alone is insufficient |
| Salesforce | One `pending` connector; no matching connected account | OAuth setup incomplete; not usable for CRM sync |
| HubSpot | No connector/account | Not connected. HubSpot client credentials were also absent in backend `.env`; running process environment could not be inspected due OS permissions |
| CRM results | 18 calls in previous seven days; zero with `crm_synced_at` | No recorded successful CRM sync in this window; consistent with no active CRM |
| Assistant actions | One completed `create_campaign` action in previous 30 days | Some recorded functionality, not evidence that messaging/booking works |

## Confirmed implementation issues

### P1 — live voice actions are unavailable

The deployed `voice_pipeline/action_tools.py` discards action arguments and
returns `unavailable` for callback scheduling, email sending, form submission
and transfer. These are advertised contracts without live executors in the
deployed revision. Connecting a mailbox or calendar does not wire these actions.

An isolated invocation reproduced `success=false`, `status=unavailable`, and
`confirmation_allowed=false` for callback, email and form actions. The local
transfer worktree has uncommitted transfer changes; inspection of `git show HEAD`
confirmed the deployed revision also lacks the transfer executor. Do not confuse
that local transfer work with a deployed feature.

### P1 — dashboard assistant promises actions its dispatcher blocks

`infrastructure/assistant/tools/dispatch.py:75` explicitly marks `send_email`,
`send_sms` and `report_issue` unsupported. Its default-deny policy also blocks
`check_availability`, `book_meeting`, `update_meeting`, `cancel_meeting`,
`schedule_reminder` and `execute_action_plan` because they have no policy.

Isolated dispatch of all nine returned `tool_authorization_policy_unavailable`.
The six meeting/workflow tools are also absent from `GROQ_TOOL_SCHEMAS`, so the
model is not offered them. The assistant system prompt nevertheless advertises
messaging/support submission. This is a capability mismatch, not a reason to
bypass authorization: define the intended permissions and expose only supported
actions.

### P1 — calendar booking bypasses token refresh

`services/meeting_service.py:79` selects expiry but does not use it, retrieves no
refresh token, decrypts the stored access token and installs it directly at
line 100. The assistant calendar-reading tool uses the central refreshing resolver;
meeting availability/booking uses this separate older path. With the account's
expired access token, meeting operations have no refresh recovery here.
No external calendar mutation was attempted.

### P1 — CRM record IDs are not stored per provider for calls

`services/crm_sync_service.py:252–287` uses the single `calls.crm_call_id` for
every provider. A settlement retry is skipped for all providers if that one ID
exists; summary updates send the same ID to each provider. A two-provider
isolated reproduction produced:

```
Salesforce update target: SALESFORCE_TASK_ID
HubSpot update target:   SALESFORCE_TASK_ID
```

Thus partial success cannot be reliably retried per destination, and summary
updates can target the wrong provider record. This is latent while no CRM is
connected, but would affect a tenant connecting both.

### P1 — HubSpot summary update reports success without updating

HubSpot does not override `CRMProvider.update_call_log`; the base implementation
returns `False` (`connectors/crm/base.py:100`). The sync service still appends
HubSpot to successful providers. Isolated summary sync returned:

```
success=true, updated_existing=false, providers=[hubspot]
```

When settlement created the initial HubSpot call record, the later AI summary
will not amend it through this path. Success reporting hides the omission.

### P2 — CRM delivery has no durable retry boundary in this path

`schedule_crm_sync` creates an in-memory task; the service uses in-process locks.
No durable CRM outbox or per-provider receipt is present in this implementation.
A restart can lose queued work; a provider success followed by failure saving
the local receipt can create duplicate activity on retry. These are code-path
failure risks, not a reproduced production incident in this audit.

### Capability gap — no complete Sales Hub assistant

The model tool schemas contain no HubSpot/Salesforce/deal/pipeline tools.
Existing CRM connectors implement contact lookup/creation and call/note logging;
that is narrower than managing Sales Hub deals, stages, tasks or sequences.
Do not describe connecting a CRM as enabling all of those capabilities.

## Verification and limits

- 179 focused assistant, authorization, connector, CRM, Salesforce and meeting
  tests passed against files matching the deployed revision. Deprecation warnings
  and one unawaited-coroutine warning in the no-event-loop CRM test were emitted.
- 50 voice-action contract tests passed in the existing transfer worktree;
  deployed action behavior was separately checked from its committed revision.
- Reproduced disabled dispatch, missing model schemas, missing CRM model tools,
  no-op HubSpot summary update and wrong cross-provider summary ID using isolated
  fakes without external side effects.
- No new authenticated browser conversation, successful live OAuth refresh,
  provider read, end-to-end calendar booking, delivery receipt or CRM write was
  exercised. Calendar/Drive are unverified rather than declared broken solely
  because their access tokens expired.
- The separate knowledge/lead-capture implementation remains local and incomplete;
  its passing component tests are not evidence of production deployment.

## Recommended repair order

1. Make the UI/model capability list match executable actions; preserve permission
   checks and show actionable connection failures.
2. Reconnect Gmail and complete the intended CRM connection; verify Calendar/Drive
   refresh and actual access through the central resolver.
3. Route meeting operations through that same resolver; wire voice and assistant
   actions to shared services with explicit authorization and durable receipts.
4. Track CRM contact/call IDs and delivery status per provider; implement HubSpot
   summary updates and durable retry without duplicate effects.
5. Verify one complete controlled workflow from request through provider receipt
   and Leads/Actions display before claiming end-to-end readiness.
