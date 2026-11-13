# Privileged write audit contract

Round 3 item 04 is **partial**. This change adds required transactional audit to 32
mounted writes listed below. Existing central-audit routes remain inventoried.
The 62 known legacy gaps below remain open; a passing inventory guard does not
mean those gaps were fixed.

## Required boundary

- Enter the business transaction, acquire `AuditLogger.lock_transaction` before
  authority, target-row and idempotency locks, then write the business change and
  `log_in_transaction` receipt on that same connection. Audit failure must roll
  back the business change. A buffered `log` after commit is not this contract.
- Record the authenticated actor, authoritative target tenant/resource, and
  actual before/after state. Global resources use the intentional global scope.
  Never copy plaintext credentials or encrypted credential envelopes into audit
  state. SIP snapshots also mask nested credential fields in JSON metadata.
- Do not invent a change receipt for replay, missing target or unchanged state.
  Cache invalidation follows successful commit. Existing permission checks,
  role ceilings, idempotency and external compensation remain in place.
- Explicit tenant predicates apply to changed customer-data queries. Membership
  before-state reads use the authenticated tenant; only platform administrators
  intentionally bind null scope for cross-tenant targeting.
- Provider probes and external configuration changes cannot be made atomic with
  PostgreSQL. Probe I/O remains outside the receipt transaction. SIP audit
  insertion precedes external config sync/removal, so an audit refusal performs
  no external mutation. Existing compensation does not cover every possible
  transaction-commit failure; no distributed-atomicity claim is made.

## Newly covered writes

Paths below omit `/api/v1`. These are 32 handlers, not 32 new features.

| Surface | Exact writes | Behavioral proof |
|---|---|---|
| Role permissions (2) | POST and DELETE `/rbac/roles/{role_id}/permissions` | `test_privileged_mutation_audit.py` |
| Tenant memberships (3) | POST `/rbac/tenant-users`; PATCH and DELETE `/rbac/tenant-users/{tenant_user_id}` | Same file; `test_tenant_membership_before_state_scope.py` |
| Abuse administration (4) | POST `/admin/abuse/rules`; PUT and DELETE `/admin/abuse/rules/{rule_id}`; POST `/admin/abuse/events/{event_id}/resolve` | `test_privileged_mutation_audit.py` |
| AI credentials (2) | POST `/tenant-ai-credentials/`; DELETE `/tenant-ai-credentials/{credential_id}` | `test_privileged_mutation_audit.py` |
| Provider configuration (4) | PUT and DELETE `/telephony/providers/{provider}`; POST `/telephony/providers/{provider}/test`; POST `/telephony/providers/activate` | `test_telephony_provider_audit.py` |
| SIP trunks (8) | POST `/telephony/sip/trunks`; PATCH and DELETE `/telephony/sip/trunks/{trunk_id}`; POST `/telephony/sip/trunks/{trunk_id}/activate`, `/deactivate`, `/test`; PUT `/telephony/sip/trunks/pool-assignment`, `/campaign-assignment` | `test_sip_privileged_audit.py`; `test_sip_additional_write_audit.py` |
| Codec policies (4) | POST `/telephony/sip/codec-policies`; PATCH `/telephony/sip/codec-policies/{policy_id}`; POST the same target's `/activate` and `/deactivate` | `test_sip_privileged_audit.py` |
| Route policies (4) | POST `/telephony/sip/route-policies`; PATCH `/telephony/sip/route-policies/{policy_id}`; POST the same target's `/activate` and `/deactivate` | `test_sip_privileged_audit.py` |
| Webhook configuration (1) | POST `/webhooks/secure/admin/configure` | `test_webhook_transactional_audit.py` |

The provider probe can still race a concurrent credential rotation: its saved
result can describe the older credential. This pre-existing issue is separate
from receipt atomicity and remains open.

Intentional API change: if the provider probe finishes but its required status
and audit transaction cannot persist, the endpoint returns 503 instead of the
former best-effort 200. Pool acquisition failure retains the standard
`Retry-After` header. The external probe runs exactly once; a failed persistence
step does not retry provider I/O.

## Guard and evidence

[`test_privileged_audit_route_inventory.py`](../tests/unit/test_privileged_audit_route_inventory.py)
walks the actual mounted routes, including hidden/nested routes and inherited
dependencies. It distinguishes administrative role/permission requirements from
ordinary tenant CRUD by inspecting captured dependency values. Unknown/new
permissions default to privileged. The explicit `AUDITED` entries name the exact
handler, reachable transaction helpers and an existing behavioral test file.
This is a wiring guard, not proof that every branch committed in PostgreSQL.

`EXCEPTIONS` means an individually justified non-mutating/rejected operation.
`LEGACY_GAPS` means an individually recorded unfinished implementation or
verification task. Neither list inherits a URL prefix. A new alias, new
privileged handler, changed handler identity, stale entry, empty reason or
unreachable declared audit helper fails the guard. Fixing a gap requires its
behavioral regression and changing its disposition; it must not be silently
called audited.

Root-run receipts for this worker's owned changes:

| Tranche | Failing behavior before change | Focused result after change |
|---|---|---|
| RBAC, abuse, AI credentials | 32 failed, 3 passed in 14.54s | 35 passed in 10.64s |
| Provider writes | 8 failed in 11.10s | 8 passed in 8.90s |
| SIP first tranche | 27 failed in 11.38s | Combined SIP result below |
| SIP additional writes | 12 failed in 13.33s | 41 passed in 9.77s, including 2 extra nested-mask cases |
| Membership before-state tenant predicate | 2 failed, 2 passed in 17.24s; 403 occurred only after foreign-row read | Combined membership 4 + inventory 5: 9 passed in 47.45s |
| Mounted inventory | 1 failed, 2 passed in 46.76s; broad discovery exposed uncovered routes | Same 9-case result; explicit legacy debt remains |

These are transactional fake-database, synthetic provider/config-callback and
actual handler/mount tests. They are not actual PostgreSQL, Asterisk or provider
validation. Root owns broader compatibility/Ruff results and the combined item04
receipt, including legal acceptance and webhook tests. Legal text, configured
versions and enabling mandatory acceptance remain owner decisions.

## Exact legacy gaps

These 62 exact routes are grandfathered **as debt**, not exempted from audit
requirements. Their handler identities and matching technical reasons are pinned
in `LEGACY_GAPS` in the guard. Followup should preserve each existing state
machine and add failed-audit/failed-commit proof before changing its disposition.
All paths omit `/api/v1`.

| Method and route | Remaining boundary / followup |
|---|---|
| PUT `/admin/calls/pause` | `admin.base.set_pause_all_calls`: Redis pause precedes swallowed buffered audit; needs durable intent/outcome design. |
| POST `/admin/calls/pause` | `admin.base.pause_all_calls`: legacy alias shares that Redis mutation and buffered audit; same intent/outcome contract required. |
| POST `/admin/calls/{call_id}/terminate` | `admin.calls.terminate_call`: external hangup/settlement precedes buffered audit; audit intent and confirmed outcome separately. |
| POST `/admin/incidents/{incident_id}/acknowledge` | `admin.health.incidents.acknowledge_incident`: `_transition` records actor/time but no chained before/after receipt. |
| POST `/admin/incidents/{incident_id}/resolve` | `admin.health.incidents.resolve_incident`: same SQL transition lacks central receipt and rollback regression. |
| PATCH `/admin/contact-enquiries/{enquiry_id}` | `contact_enquiries.change_contact_enquiry_status`: authority/CAS/handled_by exist; immutable before/after receipt does not. |
| POST `/admin/actions/{action_id}/cancel` | `admin.actions.cancel_action`: actor is written in action payload, without a chained state-change receipt. Preserve reservation/session checks. |
| POST `/admin/actions/{action_id}/recover-acknowledgement` | `admin.actions.recover_saved_acknowledgement`: atomic `assistant_action_resolutions` evidence exists; central chain integration remains open. Preserve recovery/session/CAS rules. |
| DELETE `/admin/recordings/{recording_id}` | `admin.media.delete_admin_recording`: durable deletion saga exists; central receipt is buffered and swallowed after storage/SQL work. |
| DELETE `/admin/feedback/{feedback_id}` | `admin.media.delete_admin_feedback`: deletion intent/outcome exists; central receipt uses post-operation `_safe_audit`. |
| POST `/admin/feedback/{feedback_id}/transcription/retry` | `admin.media.retry_admin_feedback_transcription`: retry can queue before `_safe_audit`; needs atomic outbox/audit intent. |
| POST `/admin/audit/logs/export` | `audit_logs.export_audit_logs`: buffered access receipt follows query; durable fail-closed export receipt remains open. |
| PUT `/admin/tenants/{tenant_id}/call-limits` | `call_limits.update_tenant_call_limits`: write commits before buffered audit; add same-transaction before/after receipt. |
| PUT `/admin/partners/{partner_id}/limits` | `call_limits.update_partner_limits`: advisory-serialized upsert accepts AuditLogger but does not call it. |
| POST `/admin/dnc` | `call_limits.add_dnc_entry`: admin alias omits optional `audit` argument to `DNCService.add`; completed tenant DNC work did not cover this alias. |
| DELETE `/admin/dnc/{entry_id}` | `call_limits.remove_dnc_entry`: admin alias directly deletes without before-state/audit; tenant DNC work did not cover it. |
| POST `/dnc/bulk-import` | `dnc.bulk_import`: retained, user-marked-done DNC design uses business batches then buffered summary; no single atomic audit transaction. |
| PUT `/recordings/policy` | `recordings.put_recording_policy`: policy upsert precedes `_safe_audit`; protected consent/retention workflow needs dedicated owner proof. |
| POST `/billing/cancel` | `billing.cancel_subscription`: Stripe and local-state changes precede central buffered receipt; needs external intent/outcome design. |
| POST `/billing/portal` | `billing.create_portal_session`: external portal session precedes buffered access receipt; SQL cannot roll back Stripe. |
| POST `/calls/{call_id}/hangup` | `calls.hangup_live_call`: `calls:delete` is administrative authority; external workflow lacks central before/after receipt. |
| POST `/admin/billing/packages` | `admin.billing_packages.create_package`: same-transaction central audit already exists; dedicated failed-audit rollback proof was not located in this pass. **Verification debt.** |
| PATCH `/admin/billing/packages/{code}` | `admin.billing_packages.update_package`: same-transaction before/after audit exists; dedicated failed-audit rollback proof remains unverified. **Verification debt.** |
| POST `/admin/secrets/tenants/{tenant_id}/secrets` | `secrets.create_secret`: manager creation/access-log transaction completes before endpoint buffered central log. Add masked central receipt without breaking envelope semantics. |
| POST `/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/rotate` | `secrets.rotate_secret`: rotation/access accounting precedes buffered central log; same-transaction masked receipt remains open. |
| DELETE `/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}` | `secrets.revoke_secret`: revocation completes before buffered central log. |
| POST `/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/compromise` | `secrets.mark_secret_compromised`: compromise state completes before buffered central log. |
| POST `/admin/secrets/platform/secrets` | `secrets.create_platform_secret`: platform secret creation completes before buffered central log. |
| POST `/admin/secrets/platform/secrets/{secret_id}/rotate` | `secrets.rotate_platform_secret`: platform rotation completes before buffered central log. |
| POST `/admin/emergency/request` | `emergency_access.request_emergency_access`: emergency request records plus buffered endpoint log lack central transactional receipt. Review break-glass lifecycle. |
| POST `/admin/emergency/{request_id}/approve` | `emergency_access.approve_emergency_request`: approval state and buffered central receipt are separate. |
| POST `/admin/emergency/{request_id}/deny` | `emergency_access.deny_emergency_request`: denial state and buffered central receipt are separate. |
| POST `/admin/emergency/{request_id}/session` | `emergency_access.create_emergency_session`: session issuance and buffered central receipt are separate. |
| DELETE `/admin/emergency/{request_id}/session` | `emergency_access.terminate_emergency_session`: termination and buffered central receipt are separate. |
| POST `/admin/emergency/{request_id}/review` | `emergency_access.complete_emergency_review`: lifecycle review and buffered central receipt are separate. |
| POST `/admin/security-events/events` | `security_events.create_security_event`: event creation exits its transaction before buffered log. |
| PATCH `/admin/security-events/events/{event_id}` | `security_events.update_security_event`: event update exits its transaction before buffered log. |
| POST `/admin/security-events/events/{event_id}/resolve` | `security_events.resolve_security_event`: event resolution exits its transaction before buffered log. |
| POST `/admin/security-events/events/{event_id}/escalate` | `security_events.escalate_event`: event escalation exits its transaction before buffered log. |
| POST `/telephony/sip/runtime/activate` | `telephony_runtime.activate.activate_runtime_policy`: runtime-event/idempotency transactions surround external apply/verify; central chained intent/outcome is absent. |
| POST `/telephony/sip/runtime/rollback` | `telephony_runtime.rollback.rollback_runtime_policy`: specialized runtime rollback events do not provide central chained before/after receipt. |
| POST `/telephony/sip/runtime/concurrency/leases/acquire` | `telephony_concurrency.acquire_concurrency_lease`: operational lease admission has no central audit boundary. |
| POST `/telephony/sip/runtime/concurrency/leases/expire` | `telephony_concurrency.expire_stale_concurrency_leases`: operational expiration has no central audit boundary. |
| POST `/telephony/sip/runtime/concurrency/leases/{lease_id}/heartbeat` | `telephony_concurrency.heartbeat_concurrency_lease`: TTL heartbeat has no central receipt; design bounded audit policy before adding global chain work. |
| POST `/telephony/sip/runtime/concurrency/leases/{lease_id}/release` | `telephony_concurrency.release_concurrency_lease`: operational release has no central audit boundary. |
| PATCH `/admin/inbound/controls` | `admin.inbound.patch_platform_inbound_controls`: specialized transactional inbound evidence exists, central chain integration does not; protected ingress owner review required. |
| POST `/admin/inbound/assignments/{assignment_id}/quarantine` | `admin.inbound.quarantine_inbound_assignment`: specialized inbound ledger, not central chained receipt. |
| POST `/admin/inbound/assignments/{assignment_id}/unquarantine` | `admin.inbound.unquarantine_inbound_assignment`: specialized inbound ledger, not central chained receipt. |
| POST `/admin/inbound/reassignments` | `admin.inbound.request_inbound_reassignment`: specialized reassignment evidence, not central chained receipt. |
| POST `/admin/inbound/reassignments/{request_id}/approve` | `admin.inbound.approve_inbound_reassignment`: specialized approval evidence, not central chained receipt. |
| POST `/admin/inbound/reassignments/{request_id}/cancel` | `admin.inbound.cancel_inbound_reassignment`: specialized cancellation evidence, not central chained receipt. |
| POST `/admin/inbound/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve` | `admin.inbound.resolve_inbound_billing_hold`: protected billing-hold evidence/approval workflow needs owner review for central chain integration. |
| POST `/tenant-phone-numbers/` | `tenant_phone_numbers.register_phone_number`: business state and `inbound_audit_events` already commit together; central chain integration remains open. |
| POST `/tenant-phone-numbers/{did_id}/verify` | `tenant_phone_numbers.verify_phone_number`: transactional DID/proof evidence exists; preserve carrier/ingress contract in central-chain followup. |
| DELETE `/tenant-phone-numbers/{did_id}` | `tenant_phone_numbers.revoke_phone_number`: transactional DID-revocation evidence exists; central-chain integration remains open. |
| POST `/ai-options/config` | `ai_options.config.save_config`: privileged `tenants:update` configuration lacks central receipt; protected voice/provider owner followup. |
| POST `/ai-options/voices/clone` | `ai_options.clone.clone_voice`: privileged provider resource creation lacks central intent/outcome receipt. |
| DELETE `/ai-options/voices/cloned/{clone_id}` | `ai_options.clone.delete_cloned_voice`: privileged provider resource deletion lacks central intent/outcome receipt. |
| POST `/ai-options/voices/prefetch` | `ai_options.preview.prefetch_all_voice_samples`: privileged provider/cache generation lacks central operation receipt. |
| POST `/ai-options/benchmark` | `ai_options.benchmark.run_benchmark`: privileged billable benchmark has no central operation receipt; owner must define probe policy. |
| POST `/ai-options/test/llm` | `ai_options.testing.test_llm`: privileged billable LLM probe has no central operation receipt. |
| POST `/ai-options/test/tts` | `ai_options.testing.test_tts`: privileged billable TTS/cache probe has no central operation receipt. |

## Legitimate exceptions

These are distinct from debt: POST `/admin/actions/{action_id}/retry` always501
without queuing; PUT `/admin/alerts/settings` always409 without changing external
Alertmanager settings; POST `/admin/suspensions/bulk-suspend` always501 without
mutation; POST `/telephony/sip/runtime/compile/preview` only returns a compiled
preview; POST `/admin/secrets/validate-api-key` is a public authentication
verifier despite its URL, whose access accounting belongs to authentication.
The guard pins each exact handler and reason.
