# Plan: Telephony provider integration (Twilio + Vonage + Local PBX) in Settings

## Context

Today the backend is wired to **one global telephony provider** chosen at process startup by env var (`TELEPHONY_PROVIDER` → Vonage adapter OR SIP/Asterisk adapter). The factory file itself notes: *"In the future this will be per-tenant from the database"* (`backend/app/infrastructure/telephony/provider_factory.py:35-68`). Today:

- No tenant can enter their own Twilio account_sid + auth_token through the UI.
- `tenant_sip_trunks` table + full CRUD endpoints (`POST /api/v1/telephony/sip/trunks` etc.) **already exist** (migration `20260224_add_tenant_sip_onboarding.sql`) but there is **no frontend** that reads or writes them.
- Settings page (`Talk-Leee/src/app/settings/page.tsx`) has Tabs (Profile, Security, Devices, Logout) — no telephony section.
- Vonage adapter exists; Twilio adapter does not.
- No "test connection" endpoint for any provider.

**User decisions taken during planning:**
- Providers in scope: **Twilio + Vonage + Local PBX/SIP trunks** (Telnyx/Plivo deferred).
- Selection model: **single active provider per tenant** — outbound calls for that tenant use that one provider.

**Intended outcome:** A new **Settings → Telephony** tab where a tenant admin can paste credentials for Twilio or Vonage, test them, save them, and/or add a local SIP trunk (the existing backend table) — then pick *one* to mark as active. The dialer worker resolves the active provider per-tenant on each call.

---

## What already exists (reuse, don't rewrite)

| Piece | File:line |
|---|---|
| `TokenEncryptionService` (Fernet, key rotation aware) | `backend/app/infrastructure/connectors/encryption.py:26-186` |
| `tenant_ai_credentials` pattern — per-tenant encrypted creds + provider enum + status | `backend/database/migrations/20260425_add_tenant_ai_credentials.sql:25-81` |
| `tenant_sip_trunks` + `tenant_route_policies` + `tenant_codec_policies` (CRUD already wired) | `backend/database/migrations/20260224_add_tenant_sip_onboarding.sql:26-110` + `backend/app/api/v1/endpoints/telephony_sip.py:37-100` |
| `TelephonyProviderAdapter` base class + factory | `backend/app/infrastructure/telephony/provider_factory.py:27-125` |
| `VonageProviderAdapter` (reads env today; will be refactored to accept per-tenant creds) | `backend/app/infrastructure/telephony/vonage_provider_adapter.py:30-120` |
| Settings page Tabs + Card layout | `Talk-Leee/src/app/settings/page.tsx:333+` |
| `notificationsStore.create()` toast pattern | `Talk-Leee/src/lib/notifications.ts:1` |
| Mutation pattern (TanStack Query + toast) | `Talk-Leee/src/lib/api-hooks.ts:78-109` |
| Dialer worker call-origination path | `backend/app/workers/dialer_worker.py:46-82` |
| `CredentialResolver` pattern (tenant DB → env fallback, never caches plaintext) | `backend/app/domain/services/credential_resolver.py:69-248` |

---

## Backend work

### 1. Schema — one new table

`backend/Alembic/versions/0005_tenant_telephony_credentials.py`:

```sql
CREATE TABLE tenant_telephony_credentials (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL CHECK (provider IN ('twilio','vonage')),
    label           TEXT,                            -- user-friendly tag
    credentials_encrypted BYTEA NOT NULL,            -- Fernet-encrypted JSON blob
    from_number     TEXT,                            -- E.164 caller ID
    status          TEXT NOT NULL DEFAULT 'inactive' CHECK (status IN ('active','inactive','failed')),
    last_tested_at  TIMESTAMPTZ,
    last_test_result JSONB,                          -- {ok, latency_ms, error?}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, provider)                     -- one row per provider per tenant
);
CREATE INDEX idx_ttc_tenant ON tenant_telephony_credentials (tenant_id);
ALTER TABLE tenant_telephony_credentials ENABLE ROW LEVEL SECURITY;
CREATE POLICY ttc_tenant_isolation ON tenant_telephony_credentials
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
```

And one new column on the existing `tenants` table to pin the active provider:

```sql
ALTER TABLE tenants ADD COLUMN active_telephony_provider TEXT
    CHECK (active_telephony_provider IN ('twilio','vonage','sip','none'))
    DEFAULT 'none';
```

`tenant_sip_trunks` stays as-is — when active provider = `sip`, the dialer reads the active row from that table (`is_active = true`).

### 2. Twilio adapter (new file)

`backend/app/infrastructure/telephony/twilio_provider_adapter.py` — implements `TelephonyProviderAdapter`:

- `__init__(account_sid, auth_token, from_number)` — accepts injected creds (not env). Lazy-imports `twilio.rest.Client`.
- `originate_call(destination, caller_id=None, webhook_url, ...)` — `client.calls.create(to=..., from_=..., url=webhook_url)`.
- `ping()` — `client.api.v2010.accounts(account_sid).fetch()` for the test-credentials endpoint.

Pattern mirrors `VonageProviderAdapter`.

### 3. Per-tenant resolution in the factory

`backend/app/infrastructure/telephony/provider_factory.py`:

- New method `create_for_tenant(tenant_id, db_pool)` that:
  1. Reads `tenants.active_telephony_provider`.
  2. If `twilio` or `vonage` — fetches the matching `tenant_telephony_credentials` row, decrypts via `TokenEncryptionService`, instantiates the adapter with those creds.
  3. If `sip` — fetches the active `tenant_sip_trunks` row, instantiates `SIPProviderAdapter` with trunk config.
  4. If `none` (or row missing) — falls back to global env adapter (current behavior) so platform-managed tenants still work.

- The global `create()` keeps working unchanged (used by webhook handlers that aren't tenant-scoped).

### 4. Dialer worker hook

`backend/app/workers/dialer_worker.py:70-82`:

- Replace one call to `provider_factory.create()` with `provider_factory.create_for_tenant(job.tenant_id, self.db_pool)`. That's the only place that needs to change.

### 5. New API endpoints

`backend/app/api/v1/endpoints/telephony_providers.py` (new file, mounted in `routes.py`):

```
GET  /api/v1/telephony/providers              # current active + list of saved creds (no secrets in body)
PUT  /api/v1/telephony/providers/{provider}   # upsert credentials  {credentials, from_number, label}
DELETE /api/v1/telephony/providers/{provider} # forget credentials
POST /api/v1/telephony/providers/{provider}/test   # decrypt + call adapter.ping() — returns {ok, latency_ms, error?}
POST /api/v1/telephony/providers/activate     # body {provider: twilio|vonage|sip|none} — flips tenants.active_telephony_provider
```

Existing endpoints kept as-is:
- `GET/POST/PUT/DELETE /api/v1/telephony/sip/trunks` (already shipped, just needs frontend)

All require `Depends(get_current_user)` with role gate (tenant_admin).

Response shapes return camelCase to match frontend conventions.

### 6. CSRF / origin check

These are POST/PUT/DELETE — already covered by Phase A CSRF middleware (verified once previously).

---

## Frontend work

### 1. New Settings tab

`Talk-Leee/src/app/settings/page.tsx` — add a fifth tab **"Telephony"** between "Devices" and "Logout":

```tsx
<TabsTrigger value="telephony">Telephony</TabsTrigger>
...
<TabsContent value="telephony">
  <TelephonyProvidersSection />
</TabsContent>
```

`TelephonyProvidersSection` lives in `Talk-Leee/src/components/settings/telephony-providers-section.tsx` (new file, ~400 lines):

**Layout (top to bottom):**

1. **Active provider banner** — shows the current `active_telephony_provider` with a colored badge ("Twilio • Active") or "No active provider — outbound calls disabled".
2. **Provider cards** — three side-by-side cards:
   - **Twilio**: account_sid + auth_token + from_number inputs, **Test** + **Save** buttons, "Make active" radio.
   - **Vonage**: api_key + api_secret + application_id + private_key (textarea) + from_number, same buttons.
   - **Local PBX (SIP)**: links to a sub-section that lists saved trunks (calls `/telephony/sip/trunks`) with add/edit/delete; one trunk's "is_active" flag drives the SIP option in the active-provider selector.
3. **Test results panel** — shows the last test result (timestamp + ok/fail + latency_ms + error message).

Form mutations:
- `useSaveTelephonyProvider(provider)` → `PUT /telephony/providers/{provider}` → toast on success/error, invalidate `telephonyProviders` query.
- `useTestTelephonyProvider(provider)` → `POST /telephony/providers/{provider}/test` → toast result, store in panel.
- `useActivateTelephonyProvider()` → `POST /telephony/providers/activate` → invalidate active-provider query, success toast.

All hooks live in `Talk-Leee/src/lib/telephony-api.ts` (new file). Endpoints registered in `backend-endpoints.ts`.

### 2. SIP trunk sub-component

`Talk-Leee/src/components/settings/sip-trunks-list.tsx` — list/add/edit/delete trunks via existing `/telephony/sip/trunks` endpoints. Form fields: `trunk_name`, `sip_domain`, `port`, `transport` (dropdown UDP/TCP/TLS), `direction` (dropdown), `auth_username`, `auth_password`. Auth password posted plaintext over HTTPS, encrypted server-side.

### 3. Routing

No new top-level route — Settings tab is fine. If the section grows, can promote to `/settings/telephony` later.

---

## Critical files

### New (backend)
- `backend/Alembic/versions/0005_tenant_telephony_credentials.py`
- `backend/app/infrastructure/telephony/twilio_provider_adapter.py`
- `backend/app/api/v1/endpoints/telephony_providers.py`
- `backend/tests/unit/test_telephony_providers_endpoint.py`
- `backend/tests/unit/test_twilio_provider_adapter.py`

### Modified (backend)
- `backend/app/infrastructure/telephony/provider_factory.py` — add `create_for_tenant()`
- `backend/app/infrastructure/telephony/vonage_provider_adapter.py` — accept injected creds (constructor), keep env fallback
- `backend/app/workers/dialer_worker.py` — call `create_for_tenant()` instead of `create()`
- `backend/app/api/v1/routes.py` — mount `telephony_providers_router`
- `requirements.txt` — add `twilio>=9.0.0` (only if not already present)

### New (frontend)
- `Talk-Leee/src/components/settings/telephony-providers-section.tsx`
- `Talk-Leee/src/components/settings/sip-trunks-list.tsx`
- `Talk-Leee/src/lib/telephony-api.ts`
- `Talk-Leee/src/lib/__tests__/telephony-api.test.ts`

### Modified (frontend)
- `Talk-Leee/src/app/settings/page.tsx` — add Telephony tab
- `Talk-Leee/src/lib/backend-api.ts` — add `telephony.{listProviders, saveProvider, testProvider, activateProvider, sipTrunks.*}` methods
- `Talk-Leee/src/lib/backend-endpoints.ts` — register new paths

---

## Verification

### Backend
- `pytest backend/tests/unit/test_twilio_provider_adapter.py` — adapter `ping()` with mocked Twilio SDK; `originate_call()` shape.
- `pytest backend/tests/unit/test_telephony_providers_endpoint.py` — upsert + test + activate flow; RLS filters by tenant; secrets never returned in GET response; failed-decrypt returns 500 not 200.
- `alembic upgrade head` clean against an empty schema and against current prod schema.
- Existing test suite green (`pytest backend/tests`).

### Frontend
- `tsc --noEmit` clean.
- `useSaveTelephonyProvider` test: posts the right body, invalidates queries on success.
- Visual: open `/settings`, click **Telephony**, see three cards; paste a known-bad Twilio SID + token → click **Test** → red toast "Authentication failed (HTTP 401)"; paste real test creds → green toast with latency; click **Make active** → banner updates.

### End-to-end (against deployed backend in hybrid dev)
1. Add Twilio test creds in Settings → Telephony → Twilio card → **Test** → see "OK, 142 ms".
2. Click **Make active** on Twilio.
3. Go to `/campaigns/{id}`, click **Start** with one contact.
4. Verify dialer worker logs show `provider=twilio` for the outbound call (use `journalctl -u talky-dialer-worker -f`).
5. Verify Twilio dashboard shows the call attempt with the expected from/to.
6. Switch active provider to Vonage → start another call → confirm `provider=vonage` in logs.
7. Add a SIP trunk pointing at a local PBX (Asterisk), set it active → outbound dials via SIP.

### DB checks
```
psql -c "SELECT tenant_id, provider, status, last_tested_at FROM tenant_telephony_credentials;"
psql -c "SELECT id, active_telephony_provider FROM tenants;"
```

### Deploy steps (one-shot)
- `scp` 5 backend files (1 migration + adapter + endpoint + 2 modified) → `/opt/talky/backend/...`
- `alembic upgrade head` on prod
- `pip install twilio` in the prod venv if missing
- `systemctl restart talky-api talky-dialer-worker`
- Hard-refresh frontend; smoke the flow as above.

---

## What this plan deliberately is NOT

- **No Telnyx / Plivo adapters** — deferred. Adding either later is a single new adapter file plus a CHECK constraint update on `provider`.
- **No multi-provider routing per campaign / per number prefix** — single active per tenant. A `route_policies`-style escalation is a future v2.
- **No inbound number provisioning UI** — Twilio number purchase / port-in is not in scope; tenant pastes a number they already own.
- **No webhook URL editing** — webhook URL stays as `${API_BASE_URL}/api/v1/{provider}/event`. Per-tenant webhook routing is a future.
- **No PBX bootstrap / Asterisk install scripts** — assumes the tenant has a reachable SIP trunk; we just store credentials and dial against it.
- **No SMS** — telephony in this plan is voice-only. Twilio SMS is a separate later track.
- **No "platform vs. tenant" billing split** — calls go on the tenant's Twilio/Vonage account if their creds are set; no per-minute markup. Future work.
