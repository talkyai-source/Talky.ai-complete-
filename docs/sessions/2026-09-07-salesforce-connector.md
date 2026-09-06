# 2026-09-07 — Salesforce connector (CRM sync + agent callbacks)

Branch: `main` (worktree). Deployed: **no** — prod needs a Salesforce Connected App
(`SALESFORCE_CLIENT_ID` / `SALESFORCE_CLIENT_SECRET` in `/opt/talky/backend/.env`)
before the card even appears; see "Rollout" below.

## What existed before

* `connectors` framework (OAuth state + PKCE, encrypted `connector_accounts`,
  type-keyed dashboard cards) with HubSpot as the only CRM.
* The dashboard already rendered a greyed-out "Salesforce" card gated on the
  backend advertising a `salesforce` item in `GET /connectors/status`.
* `app/services/crm_sync_service.py` was **dead code**: no callers, read a
  `connectors.encrypted_tokens` column that does not exist, called
  `find_contact_by_email` which no provider implements. No call was ever
  synced to any CRM.

## What was built

### Backend

| Area | Change |
|---|---|
| `infrastructure/connectors/crm/salesforce.py` (new) | OAuth web-server flow with PKCE against `SALESFORCE_LOGIN_URL` (prod / sandbox / My Domain); persists `instance_url` + identity URL via new `BaseConnector.config_from_tokens` / `apply_config` hooks; identity probe (`fetch_account_identity`) proves the token before the connection goes green; REST v60: SOQL by e-mail (Contact → unconverted Lead), SOSL by phone digits, Lead creation (LastName/Company fallbacks), call logging as a completed `Task` (subtype Call, duration, direction, disposition; retries without `TaskSubtype` if the org rejects it), `update_call_log` PATCH, `list_people` for imports, `probe`. Salesforce sends no `expires_in`, so tokens are stamped with a 15-minute TTL (`SALESFORCE_ACCESS_TOKEN_TTL_SECONDS`) and refreshed proactively. |
| `infrastructure/connectors/base.py`, `crm/base.py` | Optional hooks above + `CRMProvider.update_call_log` (default no-op). |
| `api/v1/endpoints/connectors.py` | Card keys: `CARD_PROVIDERS` maps `salesforce → ("crm","salesforce")` beside `crm → ("crm","hubspot")`. `/status` groups rows per card (Salesforce advertised only when the server has Connected App creds or the tenant has rows). `/{type}/authorize` and `/{type}/disconnect` speak card keys and are **provider-scoped** (disconnecting HubSpot no longer deletes Salesforce rows and vice versa). OAuth callback: generic identity check + config persistence for non-Gmail providers, `external_account_id` stored, de-dup of older active connectors scoped to (type, provider). Frontend redirect carries the card key. |
| `services/connector_resolver.py` | `provider=` filter, `list_active_connector_providers`, `connectors.config` handed to `apply_config`, config write-back after refresh. |
| `services/crm_sync_service.py` (rewritten) | Logs every finished call into **every** active CRM (HubSpot and/or Salesforce). Two hooks, idempotent across them: (1) `CallService.handle_call_status` after durable terminal settlement — every outbound call, answered or not; (2) `call_transcript_persister._safe_generate` after the AI summary — amends the existing Task with headline/next step, or creates the log for inbound calls (settled in the fenced inbound lifecycle, which has no CRM hook). Per-provider record ids live in `leads.custom_fields.crm_ids`; `leads.crm_contact_id` keeps the newest for legacy readers; `calls.crm_call_id/crm_synced_at` recorded. One forced token refresh + retry on a 401. All SQL via `acquire_with_tenant` **and** explicit `tenant_id` predicates. |
| `domain/services/call_service.py` | `_schedule_crm_sync` (fire-and-forget) after the durability check, before event logging. Never awaited, never raises. |
| `services/scripts/call_transcript_persister.py` | `run_crm_sync(reason="summary")` after `generate_and_store` (also when summarising fails). |
| `api/v1/endpoints/salesforce.py` (new, prefix `/connectors/salesforce`) | `GET/PUT /settings` (callback campaign — validated with `require_owned_outbound_campaign`, so an inbound campaign is a 409 — plus log/create/inbound toggles and callback priority), `GET/POST /webhook-token` (reveal / rotate; needs `connectors:update`), `POST /test` (identity + one query), `POST /import` (Leads/Contacts with a phone → `ingest_lead_records` into a named contact list, Salesforce ids stamped). **Public webhooks**: `POST /callback-requests/{tenant_id}/{token}` (JSON from Flow HTTP callout / Apex / Zapier; `X-Talky-Token` header also accepted) and `POST /outbound-message/{tenant_id}/{token}` (SOAP Outbound Message; org id checked; `<Ack>true</Ack>`; DTD/oversize refused). Both resolve the tenant from the URL, set the RLS context, compare a SHA-256 token hash in constant time. A callback request creates or revives the lead in the campaign's "Salesforce callbacks" list, refuses `do_not_call`, then dials through the same `start_campaign(list_id=…, allow_running=True)` path the dashboard's "Call this list" button uses; paused/completed campaigns store the lead without dialing. |
| `api/v1/routes.py` | Salesforce router registered. |
| `tests/unit/test_endpoint_auth_audit.py` | The two webhooks listed as token-verified public routes. |
| Docs / env | `backend/.env.example`, `backend/docs/day_twenty_four_connectors.md`. |

### Frontend (Talk-Leee)

* Salesforce card now honest ("Log every call as a Salesforce Task…") and enabled when the backend advertises it.
* New `components/connectors/salesforce-settings.tsx`: connection details + Test, sync toggles, callback campaign (outbound only, via `useOutboundCampaigns`) and priority, webhook URLs with Reveal/Rotate/Copy, a setup guide (Flow HTTP callout body, Outbound Message fields, what happens next), and an Import section.
* `backend-endpoints.ts`, `backend-api.ts` (`backendApi.salesforce.*`), `models.ts` (zod schemas), `api-hooks.ts` (query + mutations with notifications), dev proxy stubs.

### Tests added

`test_salesforce_connector.py` (19), `test_salesforce_endpoints.py` (22), `test_connectors_card_keys.py` (11),
`test_crm_sync_service.py` (rewritten, 19), `test_crm_sync_hooks.py` (8);
`salesforce-settings.test.ts` (7), `backend-api.salesforce.test.ts` (3).

## Verification

Real output, run in this worktree after the last edit:

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8836 passed, 7 skipped in 658.88s (0:10:58)

ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

cd Talk-Leee && npm run typecheck && npm run lint && npm test
typecheck exit=0 · lint 0 errors 0 warnings · tests 381, pass 379, fail 0, skipped 2
```

Two failures surfaced on the first full run and were fixed before the final one:
the CRM sync spelled outcome strings that only `call_status.py` may define
(guard `test_outcome_sets_have_exactly_one_definition`; now keyed by
`CallOutcome`), and the endpoint tests leaked the RLS tenant contextvar into
the IDOR suite (autouse `clear_tenant_context`).

## Rollout (not done here)

1. Create a Salesforce Connected App (OAuth: web server flow, PKCE required,
   scopes `api id refresh_token offline_access`, callback
   `https://<api-host>/api/v1/connectors/callback`).
2. Add `SALESFORCE_CLIENT_ID` / `SALESFORCE_CLIENT_SECRET` (and
   `SALESFORCE_LOGIN_URL=https://test.salesforce.com` for a sandbox) to prod
   `.env`; restart `talky-api`. No migration: settings live in
   `connectors.config` (JSONB, already present).
3. Connect from the Connectors page, choose a callback campaign, generate the
   webhook token, wire a Flow/Outbound Message, place one test callback and one
   real call; check the Task on the Lead/Contact.

## Known limits / follow-ups

* XML parsing uses the stdlib parser (no `defusedxml` dependency allowed); DTDs and
  bodies > 256 KB are refused before parsing.
* Inbound calls are logged only from the summary hook (needs a transcript); an
  inbound call with no speech is not logged. Outbound no-answer calls are logged.
* HubSpot sync now runs for the first time through the same service; HubSpot
  contacts still require an e-mail (its API is e-mail keyed).
* Salesforce picklists: Task `Status=Completed`, `Priority=Normal`, Lead
  `LeadSource=Other` are the standard values; orgs that removed them will see a
  logged provider error (`invalid_request`) per call, not a crash.
