# Admin remaining limitations — 27 October 2026

**Follow-up:** [billing-compatible Admin release](2026-10-27-admin-billing-release-compatibility.md)
records the subsequently changed production source/schema, preserved billing
merge and fresh restore. Its live observations supersede the baseline below;
the earlier results remain historical evidence.

This continues the [live monitoring repair](2026-10-27-admin-monitoring-live.md)
under the existing feature freeze. It records completed work and the remaining
coordinated-release dependency; it does not certify the whole Admin product.

## Completed and live

**PBX visibility:** the dashboard process intentionally has telephony ownership
disabled. Its former probe only looked for an adapter in that same process, so
it could not observe the healthy call-owning process's configured PBX. The new
fallback performs bounded read-only requests to configured Asterisk ARI information
and gateway health endpoints. It never creates/connects an adapter, acquires call
ownership, starts a call or repairs Redis state. ARI authentication is isolated
from the gateway request; redirects and inherited proxies are disabled, response
bodies and time are bounded, and gateway protocol/codec compatibility is checked.
Missing configuration remains unknown and failed reads remain down.

Full-release source `902f10bcf604d8f7e54c389125b7ffbbb5997d2e` passed 80 focused
tests: 51 new PBX cases, 23 existing monitoring cases and six gateway cases, with
zero failures/skips. The exact three source/test files were copied into the
production-based maintenance release `5470521ace51762863c14fca70f2c94780e6becc`.
The existing bounded maintenance command pins the prior `80f011d4` unit as rollback,
preserves the live environment/data directory, and restarts only API-web. It also
sets the process's displayed version to its eight-character source commit.

Before activation, actual configured ARI/gateway reads and import/schema checks
passed. After activation, all six direct and public monitoring reads passed again,
as did 12 anonymous-access rejections, login/logout and retired-token rejection.
Seven protected call/worker/service identities were unchanged. Public Telephony
reported Operational with a measured 26 ms **control probe**, not audio latency;
the version returned `5470521a`. The protected browser independently displayed
the measured status/timestamp and correct version without monitoring alerts.
No rollback was required. Server receipt SHA-256:
`873b6423a84da1724dbb79346fe3c2752c718dd15d503d3cb51b80d33b907d56`.
PBX browser manifest SHA-256:
`71c2e4a783cc728a82d8b13d972a00e6ec9cd09cbfbf62c4105e5c013d1130fa`.

**Narrow-screen interaction:** the same deployed frontend was checked at 360 px.
The heading was clear, the document did not overflow horizontally, and both
focused ArrowRight key presses and emulated Chromium touch swipes reached the
worker table's Last Heartbeat column. This extends the earlier 1280/768 px
programmatic-scroll proof. Real physical-device gestures and unrelated pages are
not covered. No further frontend change was necessary.

## Live compatibility audit

A privacy-limited audit covered 14 initial page/tab views and a 16-area matrix.
Only approved reads and normal authentication were used. No business mutations,
provider inspection/audio requests, customer messages or calls were initiated.
Evidence retains statuses, shapes, counts and error categories, not customer rows.

| Area | Observed problem | Root cause and required repair |
|---|---|---|
| Users and roles | Operation discovery returns 500 without CORS; directory rows lack review tokens | Old `/users/{user_id}` treats `operations` as a user ID. The compatible static routes, review/prepared-operation protocol and 0075/0076 migrations are in the full candidate. Returning an empty operation list would hide the defect. |
| Inbound history | Default All filter returns 400; old response lacks cursor metadata | The old service supports individual statuses and `{items,total}`; the current UI requires All, bounded pages, `has_more` and `next_cursor`. Omitting All would select pending only and still fail the contract. Release the matching route/service. |
| Connector totals | Lists load but totals are unavailable | Old responses omit the all-connectors summary. Deploy the aggregate contract; visible-page counts cannot substitute for platform totals. |
| Media-deletion history | Authenticated discovery returns 404 | The old backend lacks history/receipt/recovery routes. Deploy the matching media operation protocol; readable recording lists do not prove deletion recovery. |
| Inbound controls | Exact GET returns 200 without the Admin CORS header | Nginx sends this route to the unchanged call owner on port 8000. Its in-memory CORS settings and control implementation need the coordinated release. Mutating controls must retain their live-owner checks. |
| Website enquiries | Empty live list passes; populated-row compatibility remains broken in source | Old SELECTs omit the revision required by the current UI. Release migration 0074 and the matching revisioned handlers; an empty cohort is not populated-flow acceptance. |

The first five are observed live failures. The enquiry finding is a confirmed
source mismatch that the empty live cohort did not exercise. Other initial lists
loaded; that does not establish detail, mutation, recovery, MFA, cross-role or real
provider behavior. The inventory is bound by SHA-256
`711cd1616e8458228aa1d0551b5b2d0366a263e6e2dacbde291230aea1f4a113`;
the 16-area matrix by
`940cec44dfb3c59116f41e65d19060d5ef646f7ae17c83b16ca5ba433c5243a0`.

## Provider observations and coherent release

The production call owner still runs `2739eea`. It contains no provider-observation
producers. The dashboard reader cannot recover output/termination evidence that
was never published. The full candidate already instruments 12 STT/TTS/LLM
adapters, credential resolution, voice orchestration, assistant voice and the
Realtime runtimes. Shared Redis retains bounded credential-scoped observations
for ten minutes. Idle periods, connection-only attempts, cancellation, partial
output and unavailable storage must not be presented as provider-wide success.
Existing aggregate Prometheus timings do not supply the missing credential and
completion evidence. No paid probe loop or synthetic successful record was added.

Deploying the complete Admin API alone would leave call termination and inbound
mutations on old code. The existing restore smoke tests current imports and a few
schema reads, not concurrent old-writer/new-schema compatibility. New web startup
also introduces hold-expiry work. Therefore full Admin acceptance, provider hooks
and the port-8000 fallback require a coordinated compatible backend/schema/gateway
release. A second API process or removal of fallback is not treated as completion.

`backend/scripts/observe_release_quiescence.py`, committed in
`a7fdd7278e704e472f6b196e2fab69d64588f8b1`, now obtains the four relevant counters
without changing traffic: gateway sessions, all ARI legs, Redis lease/set
consistency and database live-call states. It accepts an explicit environment
file, rejects unsupported topology/incomplete database destinations, bounds reads
and returns only counts, timestamps and fixed errors. Its 24 offline tests pass.
It does not freeze traffic, alter leases, approve deployment or generate a drain
manifest. Even four zero results are a non-atomic observation.

The committed observer was run against the live service's explicit environment
at 2026-10-26 21:32:19 UTC. All four reads completed: gateway sessions 0, ARI legs
0, Redis leases 0 with matching empty active set, and live database calls 0.
It correctly reported `traffic_freeze_verified=false` and did not authorize
deployment. The call checkout and the dashboard unit remained unchanged.
Observation SHA-256:
`0bfabba6cfeac55366e0843c9637abdbddbc4d4bdefcb0c6966a4d590c5d4eff`.

The complete source candidate `a7fdd7278e704e472f6b196e2fab69d64588f8b1`
subsequently passed all **14 CI checks**, including the backend, both frontends,
full voice/security suite, native gateway/sanitizer checks, schema/secret guards
and container build/security scan. These are source checks, not production
activation or end-user/provider acceptance. The recorded checks are in
[main CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40404987542),
[voice CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40404987583)
and [native gateway CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40404987667).
The final check receipt hashes to
`d482bdbcb9bbabbb500dfef3fb6afe111c5c7a4c94cab47b63a55373f7432eec`.
Later evidence-only commits do not change that tested runtime source.

The next activation remains subject to the existing executable
[drain contract](../DEPLOYMENT.md#production-drain-manifest-contract): verified
ingress/outbound freeze, four fresh zero counts, topology/change references and
two distinct telephony-owner/release-manager attestations. The user has authorized
deployment; the missing material is that concrete release record. Owner identities
and existing approval references have been requested while independent work proceeds.
After that record is available, run the coordinated release, repeat exact-route
CORS/API checks, publish its immutable commit through the existing `APP_VERSION`
environment setting, then perform designated account, voice and reversible business
journeys. No owner approval or traffic-freeze claim has been fabricated.

Vercel protection remains enabled. Authoritative supplier spending/finance,
designated provider/media/PBX effects, recovery/load and policy/customer acceptance
remain in the wider CP09 plan. This work does not close those gates or lift the freeze.
