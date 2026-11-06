# F2-12 — current HTTP route-reference inventory (6 Nov 2026)

QA02 reported 139 of 478 routes without a detected caller and referenced `callers.out`. That original file was not found in the available reports, current source, or the inspected historical Git trees. The exact historical 139 cannot be reconstructed from the aggregate. The owner/Claude must supply the original artifact to recover that historical list.
This is a separate current snapshot, not a reconstruction or a claim that the historical count decreased.

Captured HTTP method/path entries: **485**; no matching static path reference from the bounded scan: **134**; source files scanned: **1,406**. The other 351 entries have candidate path references, not necessarily actual callers.
A matching path literal is only a candidate, not proof of a live caller or matching HTTP method. No match is not proof of non-use.
**At least six of the 134 have source-confirmed dynamically composed requests, listed below. Keep them.** The table preserves the raw scan output and is not a deletion list.
The captured artifact contains GET/POST/DELETE/PUT/PATCH/HEAD entries and no WebSocket entries. WebSocket completeness is not claimed; seven WebSocket declarations exist in source and require separate mounted-runtime inventory. Counts include hidden/framework HTTP endpoints and count methods separately.
Tests/docs, generated builds, navigation links and Python route declarations are excluded. Dynamic composition, wrapper dispatch, provider callbacks and external operator clients require owner evidence.
Non-Python strings use a forward-only lexical scan, not a language parser: template expressions become placeholders; regex/shell interpolation and nested language semantics are not resolved. Files over 2 MiB and literals over 8,192 characters are reported as unresolved; ignored build/dependency directories are pruned before traversal. The file-count limit is 10,000. These are explicit scan limitations, not proof that the source itself has syntax errors.
Unresolved source locations: **15**, listed below. No route or implementation was removed.

The input is `tmp/qa-round3-20261106/item42-mounted-routes.json`, emitted by the existing mounted OpenAPI test fixture with synthetic dependencies and no lifespan/provider startup. The bounded scan saved every captured route and candidate source file/line in `item42-current-reference-inventory.json`, alongside this Markdown snapshot. These are local QA evidence artifacts, not a production traffic census. Handler line numbers belong to this captured snapshot and may move in later commits.

## Confirmed dynamic source consumers missed by the literal scan

| Captured route | Consumer evidence | Recommendation |
|---|---|---|
| `DELETE /api/v1/tenant-ai-credentials/{credential_id}` | `Talk-Leee/src/components/settings/provider-keys-section.tsx:82–84` concatenates the shared prefix and credential ID; disable selects DELETE. | Retain the provider-key disable action; no retirement. |
| `POST /api/v1/tenant-ai-credentials/{credential_id}/check` | Same component, lines 82–84, adds `/check` and selects POST. | Retain the item34 key-check action; no retirement. |
| `GET /api/v1/admin/support/tickets` | `Admin/frontend/src/lib/api.ts:1858` interpolates the optional query string into the path template. | Retain the staff queue client; verify mounted UI use separately if needed. |
| `POST /api/v1/inbound-campaigns/{config_id}/activate` | `Talk-Leee/src/lib/inbound-api.ts:595–619` sends the `activate` action through the shared lifecycle request. | Retain; protected inbound workflow, no change authorized here. |
| `POST /api/v1/inbound-campaigns/{config_id}/deactivate` | Same shared lifecycle method and the `deactivate` action. The client `pause` method delegates to deactivate. | Retain; the separate backend `/pause` alias is not proven called by that delegation. |
| `POST /api/v1/inbound-campaigns/{config_id}/archive` | Same shared lifecycle method and the `archive` action. | Retain; protected inbound workflow, no change authorized here. |

These examples demonstrate why a static miss must not be called an unused route. Other dynamic consumers may remain unidentified; this manual review is not exhaustive.

## Raw scan entries with no matching static path reference

| Method | Route | Handler source | Recommendation |
|---|---|---|---|
| GET | /api/v1/admin/abuse/alerts | backend/app/api/v1/endpoints/abuse_monitoring.py:746 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/abuse/events | backend/app/api/v1/endpoints/abuse_monitoring.py:151 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/abuse/events/{event_id} | backend/app/api/v1/endpoints/abuse_monitoring.py:232 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/abuse/events/{event_id}/resolve | backend/app/api/v1/endpoints/abuse_monitoring.py:282 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/abuse/rules | backend/app/api/v1/endpoints/abuse_monitoring.py:474 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/abuse/rules | backend/app/api/v1/endpoints/abuse_monitoring.py:532 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/admin/abuse/rules/{rule_id} | backend/app/api/v1/endpoints/abuse_monitoring.py:707 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PUT | /api/v1/admin/abuse/rules/{rule_id} | backend/app/api/v1/endpoints/abuse_monitoring.py:629 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/abuse/statistics | backend/app/api/v1/endpoints/abuse_monitoring.py:377 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/actions | backend/app/api/v1/endpoints/admin/actions.py:316 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/audit/logs/export | backend/app/api/v1/endpoints/audit_logs.py:220 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/audit/stats/events-by-type | backend/app/api/v1/endpoints/audit_logs.py:322 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/audit/stats/failed-logins | backend/app/api/v1/endpoints/audit_logs.py:359 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/audit/verify-integrity | backend/app/api/v1/endpoints/audit_logs.py:426 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PATCH | /api/v1/admin/billing/packages/{code} | backend/app/api/v1/endpoints/admin/billing_packages.py:122 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/billing/reconciliation | backend/app/api/v1/endpoints/billing_topups.py:406 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/call-limits/status | backend/app/api/v1/endpoints/call_limits.py:556 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/calls/history | backend/app/api/v1/endpoints/admin/calls.py:495 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/connectors | backend/app/api/v1/endpoints/admin/connectors.py:96 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/dnc | backend/app/api/v1/endpoints/call_limits.py:476 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/dnc | backend/app/api/v1/endpoints/call_limits.py:416 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/admin/dnc/{entry_id} | backend/app/api/v1/endpoints/call_limits.py:528 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/emergency/request | backend/app/api/v1/endpoints/emergency_access.py:46 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/emergency/requests | backend/app/api/v1/endpoints/emergency_access.py:199 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/emergency/{request_id} | backend/app/api/v1/endpoints/emergency_access.py:228 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/emergency/{request_id}/approve | backend/app/api/v1/endpoints/emergency_access.py:81 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/emergency/{request_id}/deny | backend/app/api/v1/endpoints/emergency_access.py:110 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/emergency/{request_id}/review | backend/app/api/v1/endpoints/emergency_access.py:273 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/admin/emergency/{request_id}/session | backend/app/api/v1/endpoints/emergency_access.py:172 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/emergency/{request_id}/session | backend/app/api/v1/endpoints/emergency_access.py:137 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/feedback | backend/app/api/v1/endpoints/admin/media.py:532 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/inbound/assignments | backend/app/api/v1/endpoints/admin/inbound.py:139 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/inbound/campaigns | backend/app/api/v1/endpoints/admin/inbound.py:155 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/inbound/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve | backend/app/api/v1/endpoints/admin/inbound.py:93 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/incidents | backend/app/api/v1/endpoints/admin/health/incidents.py:17 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/media-deletions | backend/app/api/v1/endpoints/admin/media.py:235 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/partners/{partner_id}/limits | backend/app/api/v1/endpoints/call_limits.py:279 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PUT | /api/v1/admin/partners/{partner_id}/limits | backend/app/api/v1/endpoints/call_limits.py:325 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/recordings | backend/app/api/v1/endpoints/admin/media.py:425 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/secrets/expiring | backend/app/api/v1/endpoints/secrets.py:440 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/secrets/platform/secrets | backend/app/api/v1/endpoints/secrets.py:354 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/secrets/platform/secrets | backend/app/api/v1/endpoints/secrets.py:367 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/secrets/platform/secrets/{secret_id}/rotate | backend/app/api/v1/endpoints/secrets.py:399 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/secrets/tenants/{tenant_id}/secrets | backend/app/api/v1/endpoints/secrets.py:121 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/secrets/tenants/{tenant_id}/secrets | backend/app/api/v1/endpoints/secrets.py:71 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/admin/secrets/tenants/{tenant_id}/secrets/{secret_id} | backend/app/api/v1/endpoints/secrets.py:219 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/secrets/tenants/{tenant_id}/secrets/{secret_id} | backend/app/api/v1/endpoints/secrets.py:143 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/compromise | backend/app/api/v1/endpoints/secrets.py:256 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/rotate | backend/app/api/v1/endpoints/secrets.py:163 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/security-events/alerts/open | backend/app/api/v1/endpoints/security_events.py:423 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/security-events/alerts/overdue | backend/app/api/v1/endpoints/security_events.py:458 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/security-events/events/{event_id} | backend/app/api/v1/endpoints/security_events.py:227 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PATCH | /api/v1/admin/security-events/events/{event_id} | backend/app/api/v1/endpoints/security_events.py:307 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/security-events/events/{event_id}/escalate | backend/app/api/v1/endpoints/security_events.py:491 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/security-events/events/{event_id}/resolve | backend/app/api/v1/endpoints/security_events.py:377 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/support/tickets | backend/app/api/v1/endpoints/support/admin.py:28 | Retain: source-confirmed dynamic Admin client request; see evidence above. |
| POST | /api/v1/admin/suspensions/bulk-suspend | backend/app/api/v1/endpoints/suspensions.py:346 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/suspensions/tenants/{tenant_id}/restore | backend/app/api/v1/endpoints/suspensions.py:216 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/suspensions/tenants/{tenant_id}/status | backend/app/api/v1/endpoints/suspensions.py:235 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/suspensions/tenants/{tenant_id}/suspend | backend/app/api/v1/endpoints/suspensions.py:191 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/suspensions/users/{user_id}/history | backend/app/api/v1/endpoints/suspensions.py:177 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/suspensions/users/{user_id}/restore | backend/app/api/v1/endpoints/suspensions.py:129 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/suspensions/users/{user_id}/status | backend/app/api/v1/endpoints/suspensions.py:154 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/suspensions/users/{user_id}/suspend | backend/app/api/v1/endpoints/suspensions.py:97 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/admin/tenants/{tenant_id}/archive | backend/app/api/v1/endpoints/admin/tenants.py:679 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/tenants/{tenant_id}/call-limits | backend/app/api/v1/endpoints/call_limits.py:124 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PUT | /api/v1/admin/tenants/{tenant_id}/call-limits | backend/app/api/v1/endpoints/call_limits.py:163 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/usage/breakdown | backend/app/api/v1/endpoints/admin/usage.py:209 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/admin/usage/summary | backend/app/api/v1/endpoints/admin/usage.py:145 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/assistant/actions/{action_id} | backend/app/api/v1/endpoints/assistant_ws.py:935 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/auth/legal-acceptance | backend/app/api/v1/endpoints/auth/profile.py:106 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/billing/cancel | backend/app/api/v1/endpoints/billing.py:445 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/calls/{call_id}/legs | backend/app/api/v1/endpoints/calls.py:1942 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/campaigns/{campaign_id}/jobs | backend/app/api/v1/endpoints/campaigns.py:1489 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/clients/ | backend/app/api/v1/endpoints/clients.py:55 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/clients/ | backend/app/api/v1/endpoints/clients.py:92 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| DELETE | /api/v1/clients/{client_id} | backend/app/api/v1/endpoints/clients.py:196 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/clients/{client_id} | backend/app/api/v1/endpoints/clients.py:154 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/connectors/authorize | backend/app/api/v1/endpoints/connectors.py:663 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/connectors/{connector_id}/refresh | backend/app/api/v1/endpoints/connectors.py:1020 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/contacts/bulk | backend/app/api/v1/endpoints/contacts.py:572 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/dnc/ | backend/app/api/v1/endpoints/dnc.py:124 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/dnc/ | backend/app/api/v1/endpoints/dnc.py:137 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/dnc/bulk-import | backend/app/api/v1/endpoints/dnc.py:171 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/dnc/caller-opt-out | backend/app/api/v1/endpoints/dnc.py:216 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/dnc/check | backend/app/api/v1/endpoints/dnc.py:246 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| DELETE | /api/v1/dnc/{entry_id} | backend/app/api/v1/endpoints/dnc.py:260 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/inbound-campaigns/{config_id}/activate | backend/app/api/v1/endpoints/inbound_campaigns.py:331 | Retain: source-confirmed shared lifecycle request; see evidence above. |
| POST | /api/v1/inbound-campaigns/{config_id}/archive | backend/app/api/v1/endpoints/inbound_campaigns.py:368 | Retain: source-confirmed shared lifecycle request; see evidence above. |
| POST | /api/v1/inbound-campaigns/{config_id}/deactivate | backend/app/api/v1/endpoints/inbound_campaigns.py:349 | Retain: source-confirmed shared lifecycle request; see evidence above. |
| POST | /api/v1/inbound-campaigns/{config_id}/pause | backend/app/api/v1/endpoints/inbound_campaigns.py:349 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/meetings/availability | backend/app/api/v1/endpoints/meetings.py:106 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| DELETE | /api/v1/meetings/{meeting_id} | backend/app/api/v1/endpoints/meetings.py:335 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/meetings/{meeting_id} | backend/app/api/v1/endpoints/meetings.py:260 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| PUT | /api/v1/meetings/{meeting_id} | backend/app/api/v1/endpoints/meetings.py:294 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/rbac/roles/{role_id} | backend/app/api/v1/endpoints/rbac/roles.py:63 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/rbac/roles/{role_id}/permissions | backend/app/api/v1/endpoints/rbac/roles.py:212 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/rbac/roles/{role_id}/permissions | backend/app/api/v1/endpoints/rbac/roles.py:96 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/rbac/roles/{role_id}/permissions | backend/app/api/v1/endpoints/rbac/roles.py:143 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/rbac/tenant-users | backend/app/api/v1/endpoints/rbac/tenant_users.py:67 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/rbac/tenant-users | backend/app/api/v1/endpoints/rbac/tenant_users.py:130 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| DELETE | /api/v1/rbac/tenant-users/{tenant_user_id} | backend/app/api/v1/endpoints/rbac/tenant_users.py:414 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| PATCH | /api/v1/rbac/tenant-users/{tenant_user_id} | backend/app/api/v1/endpoints/rbac/tenant_users.py:264 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| GET | /api/v1/rbac/users/me/tenants | backend/app/api/v1/endpoints/rbac/users.py:47 | Owner review of operator/support/RBAC contract and authorization; document supported use or approve a separate retirement. |
| POST | /api/v1/sessions/verify | backend/app/api/v1/endpoints/sessions.py:305 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /api/v1/telephony/sip/codec-policies | backend/app/api/v1/endpoints/telephony_sip/codec_policies.py:95 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/codec-policies | backend/app/api/v1/endpoints/telephony_sip/codec_policies.py:142 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| PATCH | /api/v1/telephony/sip/codec-policies/{policy_id} | backend/app/api/v1/endpoints/telephony_sip/codec_policies.py:307 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/codec-policies/{policy_id}/activate | backend/app/api/v1/endpoints/telephony_sip/codec_policies.py:670 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/codec-policies/{policy_id}/deactivate | backend/app/api/v1/endpoints/telephony_sip/codec_policies.py:695 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/quotas/status | backend/app/api/v1/endpoints/telephony_sip/quotas.py:21 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/route-policies | backend/app/api/v1/endpoints/telephony_sip/route_policies.py:98 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/route-policies | backend/app/api/v1/endpoints/telephony_sip/route_policies.py:146 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| PATCH | /api/v1/telephony/sip/route-policies/{policy_id} | backend/app/api/v1/endpoints/telephony_sip/route_policies.py:324 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/route-policies/{policy_id}/activate | backend/app/api/v1/endpoints/telephony_sip/route_policies.py:678 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/route-policies/{policy_id}/deactivate | backend/app/api/v1/endpoints/telephony_sip/route_policies.py:703 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/activate | backend/app/api/v1/endpoints/telephony_runtime/activate.py:41 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/compile/preview | backend/app/api/v1/endpoints/telephony_runtime/preview.py:22 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/concurrency/leases/acquire | backend/app/api/v1/endpoints/telephony_concurrency.py:145 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/concurrency/leases/expire | backend/app/api/v1/endpoints/telephony_concurrency.py:278 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/concurrency/leases/{lease_id}/heartbeat | backend/app/api/v1/endpoints/telephony_concurrency.py:235 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/concurrency/leases/{lease_id}/release | backend/app/api/v1/endpoints/telephony_concurrency.py:189 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/runtime/concurrency/status | backend/app/api/v1/endpoints/telephony_concurrency.py:311 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/runtime/metrics/activation | backend/app/api/v1/endpoints/telephony_runtime/metrics.py:20 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| POST | /api/v1/telephony/sip/runtime/rollback | backend/app/api/v1/endpoints/telephony_runtime/rollback.py:46 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/runtime/versions | backend/app/api/v1/endpoints/telephony_runtime/versions.py:20 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| GET | /api/v1/telephony/sip/trunks/campaign-assignment | backend/app/api/v1/endpoints/telephony_sip/trunks.py:1615 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| PUT | /api/v1/telephony/sip/trunks/campaign-assignment | backend/app/api/v1/endpoints/telephony_sip/trunks.py:1676 | Retain pending provider/bridge/operator contract confirmation; external/dynamic clients are not established by repository literals. |
| DELETE | /api/v1/tenant-ai-credentials/{credential_id} | backend/app/api/v1/endpoints/tenant_ai_credentials.py:282 | Retain: source-confirmed provider-key disable request; see evidence above. |
| POST | /api/v1/tenant-ai-credentials/{credential_id}/check | backend/app/api/v1/endpoints/tenant_ai_credentials.py:232 | Retain: source-confirmed provider-key check request; see evidence above. |
| DELETE | /api/v1/tenant-phone-numbers/{did_id} | backend/app/api/v1/endpoints/tenant_phone_numbers.py:203 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| POST | /api/v1/tenant-phone-numbers/{did_id}/verify | backend/app/api/v1/endpoints/tenant_phone_numbers.py:157 | Owner review of purpose and external/dynamic consumers; retain until a separately approved retirement or caller repair. |
| GET | /docs/oauth2-redirect | Installed FastAPI: fastapi/applications.py:1141 | Framework documentation helper; retain with Swagger configuration, not an application dead-code candidate. |
| HEAD | /docs/oauth2-redirect | Installed FastAPI: fastapi/applications.py:1141 | Framework documentation helper; retain with Swagger configuration, not an application dead-code candidate. |

## Explicit lexical omissions

| Source | Location | Scan limitation |
|---|---|---|
| `backend/deploy/prometheus/voice_alerts.yml` | 117 | Quoted literal not closed under the simple scanner; remaining source not classified. |
| `backend/scripts/build_voice_gateway_release.sh` | 7 | Shell syntax resembles an unterminated block comment to the generic scanner. |
| `deploy_to_server.sh` | 99 | Large quoted remote script exceeds the 8,192-character literal limit. |
| `Talk-Leee/src/lib/contact-upload-csv.ts` | 67 | Quoted literal not closed under the simple scanner; remaining source not classified. |
| `Talk-Leee/src/lib/email-utils.ts` | 29 | Same lexical limitation. |
| `Talk-Leee/src/server/api-security.ts` | 331 | Same lexical limitation. |
| `telephony/scripts/assert_canary_ingress.sh` | 204 | Same lexical limitation. |
| `telephony/scripts/canary_stage_controller.sh` | 618 | Same lexical limitation. |
| `telephony/scripts/verify_day2_asterisk_first_call.sh` | 110 | Same lexical limitation. |
| `telephony/scripts/verify_day3_opensips_edge.sh` | 100 | Same lexical limitation. |
| `telephony/scripts/verify_day4_cpp_gateway.sh` | 69 | Same lexical limitation. |
| `telephony/scripts/verify_day5_asterisk_cpp_echo.sh` | 256 | Same lexical limitation. |
| `telephony/scripts/verify_day6_media_resilience.sh` | 122 | Same lexical limitation. |
| `telephony/scripts/verify_day7_stt_streaming.sh` | 265 | Same lexical limitation. |
| `telephony/scripts/verify_day8_tts_bargein.sh` | 358 | Same lexical limitation. |

## H42 — three stale auth metadata references corrected

`Talk-Leee/src/lib/backend-endpoints.ts:4–6` now describes `POST /auth/logout-all`, `GET /sessions/active` and `DELETE /sessions/{session_id}`. Export names remain stable. The actual active session UI already used canonical paths; this corrects stale metadata, not a claim that a broken live logout flow was repaired.

`api.ts` still declares an apparently unconsumed `verifyOtp(email,token)` request to `/auth/verify-otp`. The current signup verification endpoint accepts a different payload and returns a different stage result; no blind rename or removal was made. Owner approval and a caller/contract decision remain required. Old Next catch-all auth handlers remain gated by the existing `/auth` 503 refusal; they were not re-enabled or removed.

History checks found no `origin/main` changes since 4 Nov in the corrected metadata file. Three real HTTP-client/fake-fetch regression cases failed on the previous method/path values, then passed after correction:

```text
BEFORE: tests 3; pass 0; fail 3; duration_ms 25672.9862
AFTER:  tests 3; pass 3; fail 0; duration_ms 19635.4611
```

## Evidence and owner follow-up

The first scanner attempt was intentionally stopped after roughly four minutes without output (`item42-inventory-build.exit=-1`); it produced no inventory result. The bounded replacement passed its isolated harness:

```text
Ran 6 tests in 1.169s
OK
```

The bounded scan exited 0 and reported:

```json
{"routes":485,"no_static_reference":134,"methods":{"GET":230,"POST":189,"DELETE":25,"PUT":18,"PATCH":19,"HEAD":4},"unresolved":15}
```

Local receipts: `tmp/qa-round3-20261106/item42-helper-tests.txt`, `item42-inventory-bounded.txt`, `item42-frontend-before.txt`, `item42-frontend-after.txt`, with exit files. The accompanying inventory JSON records all 485 captured entries, candidate file/line references and unmatched source paths. Tooling is ignored QA evidence; the committed artifact is this reviewed report, not a permanent route-retirement gate.

Owner follow-up: supply the original `callers.out` if the historical 139 are still required; review dynamic clients, external integrations and deployed traffic; resolve the 15 lexical omissions and WebSocket inventory separately if complete usage evidence is needed. Only then propose a separately approved caller repair or retirement. No production access, route deletion, provider call or release approval is implied.
