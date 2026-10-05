# OP06: inbound transfer capability and public wording

Status: containment corrected locally; F23 and OP06 acceptance remain incomplete.

Worktree: `tmp/production-ready-op06-20261005`, branch `codex/production-ready-op06-20261005`, based on `ba0c0eb8d4716392303263fc211d1b36a722f056`. No production changes, provider operations, telephone calls, database starts, remote pushes or capability enablement were performed.

Application/test commit: `266af72652916267d44cbada49c118c63c05c1ce`.

## Reproduced problem and bounded repair

The shared voice action context previously treated an approved campaign transfer destination and a connected Asterisk/FreeSWITCH adapter as sufficient to advertise `transfer_call`. In a synthetic incoming-call context, the actual capability method returned this action even while the production inbound runtime capability was false. This let the shared prompt/tool/response guard offer an action which final inbound admission would refuse. It did not demonstrate a bypass of the final provider-effect boundary.

`backend/app/domain/services/voice_pipeline/action_execution.py` now loads the authoritative call direction and existing admitted route snapshot. Incoming-call capability requires the existing scoped runtime check, pinned enabled destination policy, non-self E.164 destination, active admitted usage reservation, and the current platform transfer switch. Malformed/missing policy and failed configuration reads do not expose the capability. The connected-adapter requirement remains. The final effect path still re-reads context and invokes existing transfer admission, which retains its attempt/hop limits, leases, idempotency, deadline and settlement checks. No new transfer implementation or admission worker was added.

The call's direction governs this decision even if a campaign row has a different direction. Existing outbound capability, including its provider-specific extension destinations, remains unchanged. The code-owned `CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE` constant remains false. Positive tests use only the existing exact tenant/config staging environment contract with synthetic identifiers; they never execute a transfer.

Capability text describes available actions at context preparation. It is not a live subscription to policy changes or a reservation of future capacity. Direct tool invocation rechecks current policy; an earlier cached offer does not authorize a later transfer after the runtime or platform gate closes.

## Existing surfaces and unchanged controls

- `inbound_campaign_service.py` already rejects new transfer configuration when unavailable and supplies tenant/config-scoped capabilities to the dedicated inbound UI.
- `inbound_admission.py` already rejects an unavailable after-hours transfer before Answer.
- `inbound_transfer.py` already blocks new inbound child-leg work before leases/PBX effects. Outbound transfers deliberately retain their separate existing behavior.
- The dedicated inbound campaign form disables unavailable transfer choices and distinguishes conversational AI message intake from live transfer. A saved request does not arrange a callback.
- Legacy white-label settings still contain a partner-controlled transfer field. This review did not prove that field enables the controlled inbound runtime; the production legacy API containment and final transfer gates remain separate. Its broader lifecycle is not claimed complete here.

## Public copy changes

Only existing transfer/handoff assertions in three sitemap-mounted pages changed. Page layout, icons, array shapes, offers, providers and unrelated marketing content were preserved.

| Page | Previous claim | Replacement |
| --- | --- | --- |
| `Talk-Leee/src/app/ai-voice-agent/page.tsx` | Transfer calls when needed; hands off/connects callers to the team; human handoff feature; incoming-call transfer FAQ | Record caller requests for review; explicitly state human handoff for incoming calls is currently unavailable; a request does not arrange a callback |
| `Talk-Leee/src/app/use-cases/customer-services-support/page.tsx` | Complex conversations transfer with context; requests go straight to the team; human handoff/support FAQ | Saved requests and available call details for review; the incoming-call handoff limitation is explicit |
| `Talk-Leee/src/app/use-cases/automated-lead-qualification/page.tsx` | Leads routed to a live representative mid-conversation | Human review of captured lead details and requests, without a callback promise |

The wording does not declare every legacy outbound transfer unavailable, and does not sell the staging proof window as a released incoming-call feature.

## Local evidence

The new capability controls use the real context, capability, traditional tool selector, shared action instructions and response guard with synthetic SQL/adapter seams. They are not actual PostgreSQL/RLS, model-semantic, carrier or acoustic acceptance. The separately attributed database-fixture follow-up below exercises actual PostgreSQL receipt paths only.

- Initial new module: **17 failed, 2 passed**. Sixteen failures exposed forbidden/stale transfer capabilities; one positive control exposed the absent platform lookup. The two passing controls were disconnected adapter and preserved outbound capability. See `artifacts/op06/capability-initial.txt`.
- First repaired module: **19 passed**; see `capability-final.txt`.
- Eight-module regression initially recorded **252 passed, 1 failed**. The isolated failure was an unchanged baseline fake SQL dispatcher: the direct-permission query's nested `tenant_users` membership check incorrectly matched the role branch before its outer `user_permissions` relation. The test fixture now matches the outer relation first. Real CP08 membership SQL/policy was not altered. The red run and isolated reproduction remain in `affected-regression.txt` and `inbound-permission-baseline.txt`.
- Corrected eight-module regression: **253 passed**, no skips, 38 existing datetime deprecation warnings, **21.13 seconds**; see `affected-regression-final.txt`.
- Final new module additionally tests direct tool invocation after a cached staging offer becomes unavailable. See `capability-final-21.txt` for the final **21-case** result. This overlaps the broader run and must not be added to its pass count.
- Targeted ESLint covers only the three changed public pages using existing exact-lock Next 16.3.8 dependencies. No new frontend build/render or browser acceptance is claimed.
- Ruff F rules and `git diff --check` cover the bounded source/test delta.
- Independent review by the LLM agent found no material backend gate defect. Its remaining same-page handoff implication was corrected before the final lint.

Exact commands, versions, files and result attribution are recorded in `artifacts/op06/validation-manifest.json`. Earlier runs are preserved rather than relabeled as final passes.

## Database-fixture compatibility follow-up

Commit `3f3de0958fdc8b02dcd1bf062d8f9c22d1aa77f6` changes only the existing action-receipt integration fixture. Its deliberately minimal `calls` table needed the six admission/direction fields now read by the shared action context. The added column types and defaults match the canonical migration; no application, permission or production schema changed. Other minimal CRM/lead fixture schemas do not call this context and were left unchanged.

The seven-case action-receipt module passed on the retained loopback PostgreSQL test database: **7 passed, 0 skipped**, three existing datetime warnings, **4.16 seconds**. Cases cover durable duplicate/conflict handling, uncertain/cancelled effects, callback outbox replay, changed policy, actual voice email confirmation and replay, dashboard call preview/queue, and workflow child previews. Provider and queue effects remain synthetic. Each run owns an isolated minimal schema; this is not a full-migration or RLS claim. The public schema remained at `0059` and the exclusive test slot was released after fixture cleanup. See `artifacts/op06/action-receipts-postgres.txt` and the manifest for the exact command.

## Acceptance still required

This patch does not complete F23. Keep the production capability closed until the already-planned allowlisted two-leg carrier proof covers answer/busy/no-answer/rejection, audible media on both legs, caller/recipient hangup at each phase, hard deadlines, duplicate events, worker restart, owned-leg cleanup and retry-safe settlement. No carrier route, authorized destination, live audio or operator sign-off was inferred from these tests. An excluded incoming-call transfer offer is containment, not feature completion or permission to lift the feature freeze.
