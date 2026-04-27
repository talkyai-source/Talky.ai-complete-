# T1.1 — Per-tenant AI provider credentials

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 1, T1.1_
_Tests: 13 new (total 144 across the new-code suite)_
_Routes: 238 → 241 (+3 for the credentials CRUD)_

---

## What was broken

Every tenant shared the same process-wide `GROQ_API_KEY` /
`DEEPGRAM_API_KEY` / `CARTESIA_API_KEY` / `ELEVENLABS_API_KEY` /
`GEMINI_API_KEY` from `.env`. Grep confirms ~14 distinct call sites
that read these directly via `os.getenv(...)` in
`backend/app/api/v1/endpoints/ai_options.py` alone, plus each
provider implementation.

Consequences:

- **Noisy-neighbour blast radius.** One tenant's traffic burned
  down the shared Groq/Deepgram rate limits; every other tenant on
  the same cluster felt the 429s.
- **No per-tenant billing attribution.** All calls showed up on one
  credit card.
- **No way to honour a tenant's own provider contract.** A tenant
  with their own Deepgram enterprise contract still had to pay us for
  their own traffic.
- **Rotating a compromised key was all-tenants-at-once.**

The underlying interface (`LLMProvider.stream_chat`, TTS/STT
equivalents) already accepts a string key per instance, so the
infrastructure was ready — only the *source* of that key was hard-
coded to the env.

---

## What shipped

A storage + resolver mechanism that lets each tenant bring their own
keys, with env as the universal fallback. **No call-site rewrites
yet** — new code consults the resolver; existing `os.getenv()` call
sites keep working until a focused pass migrates them. This was
deliberate: the mechanism lands without risk to the live pipeline.

### New migration

`backend/database/migrations/20260425_add_tenant_ai_credentials.sql`

Creates `tenant_ai_credentials`:
- `tenant_id UUID` (FK to tenants, cascade delete)
- `provider TEXT` (lower-cased at the API boundary; free-text so new
  providers don't need a migration)
- `credential_kind TEXT` — `api_key` today; `oauth_refresh_token`,
  `service_account_json` are allowed values so future providers can
  reuse the table.
- `encrypted_key TEXT` — envelope-encrypted ciphertext from
  `TokenEncryptionService`; plaintext NEVER lands in the DB.
- `last4 TEXT` — last four chars, kept for UI display
  ("••••8f3a") so operators can tell keys apart without decryption.
- `label TEXT` — optional tenant-facing note.
- `status TEXT` — `active` | `disabled`. Rotation disables the old
  row (audit-preserving) before inserting the new one.
- `last_used_at`, `rotated_at` — audit columns.

Partial unique index on `(tenant_id, provider, credential_kind)` where
`status='active'` enforces "one live key per provider per tenant".

RLS policy isolates rows by `current_setting('app.current_tenant_id')`.

### New resolver

`backend/app/domain/services/credential_resolver.py`

```python
class CredentialResolver:
    async def resolve(
        self,
        provider: str,
        *,
        tenant_id: Optional[str] = None,
        credential_kind: str = "api_key",
        env_var: Optional[str] = None,
    ) -> Optional[str]: ...
```

Resolution order:

1. Per-tenant encrypted row (when `tenant_id` is provided and the row
   is `status='active'`). Decrypted and trimmed before return.
2. Process env var — the old single-tenant default. The mapping lives
   in `_ENV_VAR_BY_PROVIDER` and covers `groq`, `gemini`, `deepgram`,
   `cartesia`, `elevenlabs`, `openai`, `anthropic`. Callers can
   override with `env_var=...`.
3. `None` — the caller decides whether to raise or degrade.

Also exports:
- `resolve_sync_env_only(provider, env_var=None)` — for paths that
  don't have a tenant context (smoke tests, intent classifier). This
  is the `os.getenv(...)` replacement for migration work.
- `env_var_for_provider(provider)` — the mapping table as a function.
- `get_credential_resolver()` — process-wide singleton lazily bound
  to the container's DB pool.

### Fail-safe behaviours

Every failure mode falls back to the env var so the service never
stops working because of a bad tenant row:

- DB connection down / timeout → log warning, return env key.
- Decryption throws (corrupted ciphertext, key rotation mid-flight) →
  log error, return env key.
- `tenant_id=None` (unauthenticated path) → skip DB, return env.
- `db_pool=None` → skip DB, return env.
- Nothing configured anywhere → return `None`.

### New admin CRUD endpoints

`backend/app/api/v1/endpoints/tenant_ai_credentials.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/api/v1/tenant-ai-credentials/` | List tenant's credentials (NEVER returns plaintext — only `last4` + `label`) |
| POST   | `/api/v1/tenant-ai-credentials/` | Register or rotate a key. Rotation flips the existing active row to `disabled` first, then inserts the new active row — all in one transaction. |
| DELETE | `/api/v1/tenant-ai-credentials/{id}` | Disable a credential (audit-preserving; row remains) |

All endpoints require an authenticated tenant user. RLS guarantees a
tenant can only touch their own rows.

---

## Design choices

- **No plaintext caching.** The resolver does a fresh DB round-trip
  per call. For telephony, this is one extra sub-ms query per
  origination — negligible next to the ~1s STT/TTS warmup. Caching
  plaintext in memory would mean a process-wide "decrypted key" cache
  that's hard to invalidate on rotation.
- **Lazy encryption import.** Tests pass an explicit
  `encryption_service=` so they don't need the production
  `get_encryption_service()` to be importable. Production construction
  via `get_credential_resolver()` lazily wires the real one.
- **Tenant + env are the only two layers.** No three-layer
  "tenant → partner → env" hierarchy yet. Partner-level keys can be
  added later without changing the resolver contract — they'd just
  become a second lookup between layers 1 and 2.
- **No ready-to-use rotation endpoint beyond POST.** A second POST
  with the same `(provider, credential_kind)` flips the old row to
  `disabled` and inserts the new one in a single transaction. That's
  the rotation primitive. A UI flow can orchestrate multiple POSTs.

---

## What this unlocks (for free)

- Adding GPT/Claude/Cohere: once a provider factory is wired, tenants
  with their own keys can use it without any more backend changes.
- Per-tenant billing: log `provider` + `tenant_id` + duration on each
  call and attribution is trivial.
- Security incident response: disable one tenant's key without
  touching the others.

---

## What is NOT done in this sprint

The ~14 existing `os.getenv("DEEPGRAM_API_KEY")` / similar call sites
still read the env directly. Migrating them is straightforward
(replace with `await get_credential_resolver().resolve("deepgram", tenant_id=...)`)
but touches many files and each change needs retest in the live
pipeline. Plan:

1. Start with the origination path (`telephony_bridge.make_call` →
   `telephony_session_config` → provider factories). This is the only
   path that actually has a tenant context today.
2. Leave `ai_options.py` test/benchmark buttons on env for now — they
   are admin-user utility endpoints and don't model tenant calls.
3. Intent detector stays env-only (no tenant context).

Tracked as a follow-up; not a blocker for the mechanism to ship.

---

## Verification

```bash
./venv/bin/python3 -m pytest tests/unit/test_credential_resolver.py -q
# 13 passed
```

End-to-end smoke (ad-hoc):

```python
from app.domain.services.credential_resolver import CredentialResolver
# With a fake pool returning a verified row + a fake encryption:
# resolver.resolve("groq", tenant_id="t1") → "tenant-key"
# With no row: falls back to os.environ["GROQ_API_KEY"].
```

---

## Test coverage (13 tests)

`backend/tests/unit/test_credential_resolver.py`:

- Env-only resolution: stripped value returned, `None` when missing,
  honours explicit `env_var=` override, unknown provider returns
  `None`.
- `env_var_for_provider()` covers every known provider, normalises
  case, trims whitespace.
- Tenant row wins over env when present.
- Env fallback when no tenant row.
- Env fallback when `tenant_id=None` (skips DB round-trip entirely).
- Returns `None` when nothing is configured.
- **Fail-safe: DB raise → env fallback.**
- **Fail-safe: decrypt raise → env fallback.**
- Provider name is lower-cased before the SQL query.
- Plaintext is trimmed (guards against `\n` sneaking in and breaking
  Bearer-style auth headers).

---

## File manifest

**New**
- `backend/database/migrations/20260425_add_tenant_ai_credentials.sql`
- `backend/app/domain/services/credential_resolver.py`
- `backend/app/api/v1/endpoints/tenant_ai_credentials.py`
- `backend/tests/unit/test_credential_resolver.py`
- `backend/docs/scrutiny/2026-04-25-t1-1-tenant-ai-credentials.md` (this file)

**Modified**
- `backend/app/api/v1/routes.py` — registers the new router

---

## What's next

- Migrate the ~14 `os.getenv("*_API_KEY")` call sites on the
  origination path to `CredentialResolver.resolve(...)`. Pure
  mechanical follow-up — no design work needed.
- **T1.3** — STT/TTS reconnect + secondary-provider failover.
- **T1.4** — Twilio/Telnyx adapters.
