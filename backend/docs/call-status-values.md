# Call status, outcome, inbound and dialer block values

Written from the code on 2026-11-17 (Day 5, D5-21). `tests/unit/test_day5_call_list.py` fails when a value exists in the code but not in this file, so add a row here when you add a value there. Every value below is in backticks.

## 1. `calls.status`

`calls.status` is free text: no CHECK constraint exists on it in the Alembic migrations (the CHECKs in `0022_inbound_calling_foundation.py` belong to other tables). The vocabulary is whatever the writers below put there. A finished call normally ends as `ended` (or `completed` for legacy rows) with the result in `calls.outcome`.

| Value | Meaning | Written by | Source |
|---|---|---|---|
| `answered` | Remote picked up | record_call_state (sets answered_at); call_transcript_persister insert | app/domain/services/call_status.py `CallState`; app/services/scripts/call_transcript_persister.py |
| `busy` | Terminal: remote busy | terminal settlement paths | `TERMINAL_CALL_STATUSES`; `CallStatus` |
| `canceled` | Terminal: US spelling of cancelled (kept so both count as terminal) | no writer found; guards only | `TERMINAL_CALL_STATUSES` |
| `cancelled` | Terminal: cancelled before it connected | dialer worker cancel paths | `TERMINAL_CALL_STATUSES`; app/workers/dialer_worker.py |
| `completed` | Legacy terminal value (and recording-only rows) | older rows; telephony/recording.py insert | app/domain/services/call_status.py `CallState`; app/domain/services/telephony/recording.py |
| `dialing` | Originate accepted, channel created | record_call_state (also sets started_at) | app/domain/services/call_status.py `CallState` |
| `ended` | Hangup received; the result is in `calls.outcome` | record_call_state (sets ended_at); telephony lifecycle teardown | app/domain/services/call_status.py `CallState`, `TERMINAL_CALL_STATUSES` |
| `failed` | Terminal failure | record_call_state / terminal settlement | `CallState`, `TERMINAL_CALL_STATUSES`, `CallStatus` |
| `in_call` | Media flowing, AI conversation in progress | record_call_state | app/domain/services/call_status.py `CallState` |
| `in_progress` | Test-agent call in progress | campaign test WebSocket insert | app/api/v1/endpoints/campaign_test_ws.py; app/domain/models/call.py `CallStatus` |
| `initiated` | Row created, nothing dialed yet | dialer worker insert (outbound); inbound admission insert | app/workers/dialer_worker.py; app/domain/services/telephony/inbound_admission.py; `CallState`, `CallStatus` |
| `no_answer` | Terminal: rang out | terminal settlement paths | `TERMINAL_CALL_STATUSES`; `CallStatus` |
| `queued` | Job picked up, originate not sent | record_call_state | app/domain/services/call_status.py `CallState` |
| `rejected` | Terminal: carrier or callee declined | terminal settlement paths | `TERMINAL_CALL_STATUSES` |
| `ringing` | Remote side is ringing | record_call_state | app/domain/services/call_status.py `CallState`; app/domain/models/call.py `CallStatus` |
| `termination_pending` | Hangup requested, not yet confirmed; transient | telephony termination (mark_termination_pending_and_load_context) | app/domain/services/call_status.py `_TERMINATION_PENDING_STATUS` |

## 2. `calls.outcome`

Outcome decides whether a call connected (never the status). Class comes from `app/domain/services/call_outcomes.py` (`answered` / `failed`); anything else is "other". Note: the website's own failed-call set (`classifyCall`) differs from the backend's.

| Value | Meaning | Class | Source |
|---|---|---|---|
| `agent_hung_up` | AI agent ended after answer | answered | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `answered` | Picked up and ran | answered | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `busy` | Remote busy | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `cancelled` | We hung up before answer | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `customer_hung_up` | Callee ended after answer | answered | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `failed` | Uncategorised failure | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `goal_achieved` | Campaign goal met (call_repository.mark_goal_achieved) | answered | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `goal_not_achieved` | Answered, goal not met | answered | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `network_failure` | Carrier or SIP infrastructure error | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `no_answer` | Rang out, no pickup | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `rejected` | Carrier or callee declined | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `unreachable` | Routing failed, no circuit | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |
| `voicemail` | Voicemail detected | failed | app/domain/services/call_status.py `CallOutcome`; app/domain/services/call_outcomes.py |

## 3. Inbound codes

Three code families travel on the inbound surface. The tenant-controls `reason` and the campaign `last_error` are NOT in these tables: they are free text (section 5).

### 3a. Readiness check keys

Each `readiness.checks[].key` returned with an inbound campaign. Source: `app/domain/services/inbound_campaign_service.py` `_readiness`.

| Key |
|---|
| `after_hours_complete` |
| `after_hours_message_valid` |
| `after_hours_runtime_supported` |
| `ai_providers_configured` |
| `billing_quota_configured` |
| `business_hours_valid` |
| `campaign_active` |
| `campaign_direction` |
| `campaign_prompt` |
| `concurrency_policy_configured` |
| `did_not_quarantined` |
| `did_unambiguous` |
| `did_verified` |
| `max_call_duration_valid` |
| `pipeline_mode_valid` |
| `platform_inbound_enabled` |
| `platform_recording_enabled` |
| `platform_settlement_enabled` |
| `platform_transfer_enabled` |
| `qualification_runtime_supported` |
| `recording_account_setting` |
| `recording_consent` |
| `recording_notice_supported` |
| `tenant_active` |
| `tenant_inbound_enabled` |
| `timezone_valid` |
| `transfer_runtime_supported` |
| `transfer_trunk_bidirectional` |
| `trunk_ready` |
| `voice_configured` |

### 3b. Admission and routing reason codes (calls and rejected calls)

Returned as the admission reason on a call and on the rejected-inbound-calls list. Union of the codes the admission service denies with (`app/domain/services/telephony/inbound_admission.py`) and the bounded vocabulary `_REASONS` (`app/infrastructure/metrics/inbound_metrics.py`).

| Code | In admission denials | In metrics vocabulary |
|---|---|---|
| `admission_dependency_unavailable` | yes | yes |
| `after_hours_action_unsupported` | no | yes |
| `after_hours_closed` | no | yes |
| `ai_config_missing` | yes | no |
| `allowed` | no | yes |
| `ambiguous_did` | yes | yes |
| `assignment_inactive` | no | yes |
| `base_campaign_inactive` | no | yes |
| `billing_not_configured` | no | yes |
| `campaign_inactive` | yes | yes |
| `campaign_not_inbound` | yes | yes |
| `concurrency_policy_missing` | no | yes |
| `did_not_verified` | yes | yes |
| `duplicate_replay` | no | yes |
| `finalized` | no | yes |
| `global_inbound_disabled` | yes | yes |
| `incomplete_binding` | no | yes |
| `insufficient_minutes` | yes | yes |
| `invalid_after_hours_action` | yes | no |
| `invalid_after_hours_message` | yes | no |
| `invalid_did` | yes | yes |
| `invalid_max_call_duration` | yes | no |
| `invalid_provider` | yes | yes |
| `invalid_provider_call_id` | yes | yes |
| `invalid_reservation` | yes | yes |
| `invalid_schedule` | no | yes |
| `invalid_timezone` | no | yes |
| `invalid_transfer_destination` | yes | no |
| `max_active_calls_reached` | no | yes |
| `missing_recording_consent` | yes | no |
| `other` | no | yes |
| `provider_backend_identity_mismatch` | yes | no |
| `released` | no | yes |
| `reservation_failed` | no | yes |
| `reversed` | no | yes |
| `routed` | no | yes |
| `routing_dependency_unavailable` | no | yes |
| `settlement_disabled` | no | yes |
| `stale_provider_replay` | yes | no |
| `subscription_inactive` | no | yes |
| `tenant_conflict` | yes | yes |
| `tenant_inactive` | yes | yes |
| `tenant_inbound_disabled` | yes | yes |
| `transfer_destination_not_approved` | yes | no |
| `transfer_disabled` | yes | no |
| `transfer_policy_disabled` | yes | no |
| `transfer_runtime_unavailable` | yes | no |
| `transfer_staging_scope_mismatch` | yes | no |
| `transfer_trunk_not_bidirectional` | yes | no |
| `trunk_not_ready` | yes | yes |
| `unknown_did` | yes | yes |
| `unsupported_recording_dtmf_opt_out` | yes | no |
| `voicemail_intake_message_required` | yes | no |

### 3c. API error codes

The `code` field of an error from the inbound campaign API (`app/domain/services/inbound_campaign_service.py`, `code="..."`). The website maps them in `Talk-Leee/src/lib/inbound/inbound-types.ts`.

| Code |
|---|
| `already_decided` |
| `already_quarantined` |
| `assignment_archived` |
| `assignment_quarantined` |
| `assignment_state_changed` |
| `assignment_state_conflict` |
| `assignment_workflow_required` |
| `campaign_archived` |
| `campaign_change_forbidden` |
| `campaign_direction_conflict` |
| `campaign_not_inbound` |
| `campaign_terminal` |
| `config_already_exists` |
| `controls_missing` |
| `did_assignment_conflict` |
| `did_not_verified` |
| `expected_version_required` |
| `extension_not_a_did` |
| `extension_not_on_trunk` |
| `extension_trunk_not_registering` |
| `four_eye_required` |
| `idempotency_in_progress` |
| `idempotency_mismatch` |
| `idempotency_race` |
| `inbound_admin_outcome_unconfirmed` |
| `inbound_reassignment_read_unavailable` |
| `inbound_runtime_restart_required` |
| `incomplete_after_hours` |
| `invalid_cursor` |
| `invalid_did` |
| `invalid_idempotency_key` |
| `invalid_identifier` |
| `invalid_limit` |
| `invalid_name` |
| `invalid_reason` |
| `invalid_status` |
| `invalid_timezone` |
| `missing_recording_consent` |
| `not_found` |
| `not_quarantined` |
| `not_ready` |
| `pause_before_archive` |
| `pause_before_edit` |
| `reassignment_not_found` |
| `reassignment_pending` |
| `same_tenant` |
| `source_did_not_verified` |
| `target_campaign_changed` |
| `target_campaign_mismatch` |
| `target_campaign_not_inbound` |
| `target_campaign_occupied` |
| `target_campaign_terminal` |
| `target_config_archived` |
| `target_config_changed` |
| `target_config_mismatch` |
| `target_did_not_verified` |
| `target_trunk_ambiguous` |
| `target_trunk_not_ready` |
| `transfer_platform_disabled` |
| `transfer_runtime_unavailable` |
| `transfer_staging_scope_mismatch` |
| `trunk_not_ready` |
| `unsupported_recording_notice` |
| `version_conflict` |

## 4. Dialer block-reason codes

`BlockCode` in `app/domain/services/dialer/block_reasons.py`; the customer title is `_TITLES` in `app/domain/services/dialer/block_state.py`. A code with no entry in `_TITLES` shows the fallback title `Calls are paused`. Self-clearing codes need no operator action; schedule codes mean the campaign is waiting, not finished.

| Code | Customer title | Kind |
|---|---|---|
| `campaign_not_running` | Campaign is not running |  |
| `out_of_minutes` | Out of plan minutes |  |
| `metering_unavailable` | Plan minutes could not be checked |  |
| `schedule_day_not_allowed` | Waiting for the next calling day | schedule wait |
| `schedule_outside_window` | Waiting for the calling window to open | schedule wait |
| `schedule_invalid` | Calling hours are invalid |  |
| `lead_cooldown` | Contact was called recently |  |
| `daily_lead_cap` | Daily call limit reached |  |
| `invalid_phone_number` | Contact has an invalid phone number |  |
| `contact_permission_missing` | Permission to call not confirmed |  |
| `max_concurrent_calls` | Waiting for a free call slot |  |
| `batch_capacity` | Waiting for the current batch | self-clearing |
| `call_gap` | Pacing between calls | self-clearing |
| `tenant_gap` | Taking turns with another campaign | self-clearing |
| `platform_gap` | Spacing out call starts | self-clearing |
| `platform_at_capacity` | All lines are busy | self-clearing |
| `destination_busy` | Number already on a call | self-clearing |
| `call_guard_blocked` | Blocked by safety rules |  |
| `call_guard_throttled` | Call rate limit reached | self-clearing |
| `call_guard_queued` | Queued behind the rate limiter | self-clearing |
| `dnc` | Number is on a Do-Not-Call list |  |
| `voice_pipeline_unavailable` | Voice provider not ready |  |
| `caller_id_not_verified` | Caller ID not verified |  |
| `originate_failed` | Call could not be placed |  |
| `line_not_ready` | Phone line not ready |  |
| `telephony_unavailable` | Calling service not connected |  |
| `unknown` | Calls are paused (fallback) |  |
| `testing_mode_schedule_bypassed` | TESTING MODE: schedule bypassed |  |

## 5. Free-text fields that have no code

* Inbound `tenantControls.reason` (`GET/PATCH` inbound tenant controls): the operator's own note, stored in `tenant_inbound_controls.reason` and returned verbatim by `InboundCampaignService.get_tenant_controls`. The system does not generate it, so there is no code to return.
* Inbound campaign `last_error` (`InboundCampaignService`, read from `inbound_did_assignments.last_error`, `app/api/v1/schemas/inbound_campaigns.py`): no code in `backend/app` writes this column, so it is always null today; there is no sentence or code to convert.
* Dialer job `last_error`: written as a code-like string by many paths (for example `contact_phone_changed` in `campaigns.py`); `GET /calls/issues` already returns the stable `reason_code` and `block_code` for it.
