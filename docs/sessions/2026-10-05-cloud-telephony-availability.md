# Cloud telephony selection and production admission

The Settings/API selection now matches the existing production containment of the legacy Twilio and Vonage bridges. A production tenant cannot activate either cloud selection. An already-saved cloud selection stays visible and retains its credentials, but campaign start, manual redial, and new SIP origination refuse it rather than silently using Asterisk. This does not implement or qualify cloud origination.

Source: `8f5d7731bd4f1b919109130f442381e8d17d5bf4`, based on `add699c4f7bf1fbf603f078e862f7229a04406c8`. Worktree: `tmp/cloud-availability-20261005`. No deployment, provider request, actual call, PostgreSQL operation, credential inspection or account change was performed.

## Reproduced baseline

The [offline probe](artifacts/cloud-availability/cloud-availability-before.py) invokes the actual activation endpoint function, campaign service, manual-redial service and final SIP endpoint, with synthetic database, queue and PBX ports. Network connect/DNS are blocked after the local event loop starts. It does not exercise authentication middleware or claim real provider acceptance. See the [result](artifacts/cloud-availability/cloud-availability-before.json) and [failed assertions](artifacts/cloud-availability/cloud-availability-before.txt).

For each of Twilio and Vonage, under `ENVIRONMENT=production` with the existing bridge flag explicitly enabled:

| Boundary | Observed before repair |
| --- | --- |
| Activate selection | Wrote the cloud pointer even though the bridge was disabled in production. |
| Campaign start | Reported success and enqueued one synthetic job. |
| Manual redial | Preview was eligible; request queued one job. |
| Existing durable intent at final call endpoint | Called the synthetic Asterisk adapter once; did not read the selected provider. |
| Direct user call negative control | Retained existing 403 rejection with no originate call. |

All eight cloud admission expectations failed. The probe can be rerun in a checkout of the baseline revision by copying it to `<checkout>/tmp/cloud-availability-before.py` and running it from that checkout with the recorded Python/overlay environment; its paths deliberately resolve relative to that scratch location. The focused regression module exercises the repaired behavior.

## Repair and retained contracts

- [Provider availability](../../backend/app/domain/services/telephony/provider_availability.py) separates credential-check results from the supported selection policy. It reads the production tenant selection using the existing tenant-scoped connection helper. Missing, failed or invalid selection reads are unavailable; they are never interpreted as a usable default. Cancellation remains cancellation.
- [Credential API](../../backend/app/api/v1/endpoints/telephony_providers.py) adds an `availability` map for both cloud cards, including providers with no saved credentials. Entries carry `activation_allowed`, `qualification_only`, `reason_code` and `reason`. Existing credentials and the selected pointer remain factual data. Production activation returns typed 422 `cloud_telephony_unavailable` before any pointer update. Outside production, activation requires the existing explicit provider bridge flag and is described only as qualification.
- The existing credential checks remain available. New results record `check_scope`: `provider_account` for Twilio's account lookup, `sdk_initialization` for Vonage's existing local SDK construction. A successful check does not establish calling readiness. Old stored results have no inferred scope. A real adapter timeout, using a synthetic account client, verifies the legitimate nullable `status_code` contract.
- [Campaign/redial readiness](../../backend/app/domain/services/telephony/outbound_readiness.py) rejects a production cloud selection before the old `sip_not_required` shortcut. [CampaignService](../../backend/app/domain/services/campaign_service.py) retains temporary lookup failure as 503 across its error boundary, including start. Readiness failures do not dispatch jobs or reset contacts.
- [Final call admission](../../backend/app/api/v1/endpoints/telephony_bridge.py) preserves existing terminal/provider-ID receipt replay before the new selection read. New work checks the current selection before trunk resolution, provider warmup and origination. Existing SIP/none route and caller-ID checks remain authoritative.
- [Worker handling](../../backend/app/workers/dialer_worker.py) treats typed selection lookup503 as an existing same-job deferral: original durable call identity, queue payload and attempt are retained. A known unsupported cloud422 is [nonretryable](../../backend/app/workers/retry_policy.py), including the legacy retry mode for this code only. Existing provider-absence CAS must succeed before a failed job/lead is settled; otherwise the original identity remains parked as uncertain. Explicit configuration correction and campaign restart can retry the failed contact. Other retry-policy behavior is unchanged.
- [Client DTO validation](../../Talk-Leee/src/lib/telephony-api.ts) and [Settings](../../Talk-Leee/src/components/settings/telephony-providers-section.tsx) show unavailable or qualification-only state separately from saved credentials. Missing legacy availability cannot enable activation; malformed booleans reject the response. Existing cloud selections need attention. Copy distinguishes “Provider account verified” from “Local configuration checked; provider was not contacted,” and does not promise default/cloud routing or imply clearing selection disables all calls.

## Verification

The [manifest](artifacts/cloud-availability/verification.json) records the exact commands, modules, interpreter/dependency overlay, source hashes and artifact hashes.

| Evidence | Result |
| --- | --- |
| [Initial actual-method tests](artifacts/cloud-availability/cloud-tests-red.txt) | 16 failed, 6 preservation controls passed before repair. |
| [Selection lookup retry reproduction](artifacts/cloud-availability/cloud-worker-red.txt) | Two failures: generic503 incorrectly marked the original intent not-originated and scheduled retry. |
| [Known configuration retry reproduction](artifacts/cloud-availability/cloud-config-retry-red.txt) | Two failures: smart and legacy policies retried the unsupported selection. |
| [Rendered Settings baseline](artifacts/cloud-availability/cloud-frontend-red.txt) | Six behavior/wording expectations failed. |
| [Campaign-start error mapping](artifacts/cloud-availability/cloud-start-status-red.txt) | Expected503 became500; one adapter timeout control passed. |
| [Nullable status regression](artifacts/cloud-availability/cloud-null-status-red.txt) | A valid failed check with null status hid the entire Settings view. |
| [Final backend batch](artifacts/cloud-availability/cloud-backend-final.txt) | 202 passed across nine modules; 97 existing deprecation warnings. Includes 37 new focused cases. |
| [SIP/auth compatibility](artifacts/cloud-availability/cloud-sip-auth-compatibility.txt) | 66 passed across two modules; three existing deprecation warnings. |
| [Final rendered frontend batch](artifacts/cloud-availability/cloud-frontend-final.txt) | 16 passed: nine new cloud controls and seven existing SIP controls. Existing Node/DOM harness; no real browser or acoustic claim. |
| [Scoped ESLint](artifacts/cloud-availability/cloud-frontend-lint.txt), [TypeScript](artifacts/cloud-availability/cloud-frontend-typecheck.txt) | Passed. No dependency changes or Next build claimed. |
| [Backend CI F gate](artifacts/cloud-availability/cloud-backend-ci-lint.txt), [new-file full F check](artifacts/cloud-availability/cloud-backend-new-lint.txt) | Passed. |

An initial full-F lint over existing touched modules reported nine inherited F401/F841 findings. A [baseline comparison](artifacts/cloud-availability/cloud-lint-baseline-comparison.json) confirms the candidate has the same diagnostic multiset as the parent source; the [unfiltered output](artifacts/cloud-availability/cloud-backend-lint.txt) is retained. No unrelated cleanup was made. The test fixture for the existing final-call controls now supplies an explicit saved SIP selection for the new database read.

Independent read reviews by the backend reviewer, frontend/API reviewer and root found no additional material defect in the final bounded delta. They did not independently run these tests. The only edit after the final backend batch was indentation inside the new helper's explanatory docstring; executable behavior was unchanged.

## Limits

This is production containment and truthful configuration reporting, not cloud call implementation, provider certification, live-account validation, human qualification or proof of what a caller heard. Explicit nonproduction opt-in remains a qualification surface; it does not establish a supported cloud campaign routing path. Saved cloud credentials are not deleted or migrated automatically.

The production selection check is a current admission read. It is not an atomic transaction with the later remote origination, and it does not promise cancellation of an already-originated call if configuration changes afterward. Missing receipt/provider evidence is never converted into proof of no call. These local tests do not close the wider AG01/OP11 or production acceptance packages.
