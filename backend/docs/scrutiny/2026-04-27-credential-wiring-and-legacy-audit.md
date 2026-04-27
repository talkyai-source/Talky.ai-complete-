# T1.1 follow-up + T2.6 — credential wiring + legacy audit

_Date: 2026-04-27_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Tier 1 follow-up + Tier 2, T2.6_
_Tests: 18 new — total 230 new-code_
_Routes: unchanged (247) — all changes are runtime / startup behaviour_

---

## T1.1 follow-up — wire CredentialResolver into the orchestrator

### What was missing

T1.1 (2026-04-25) shipped the storage + resolver mechanism for
per-tenant AI credentials, plus the admin CRUD. But the
**~14 `os.getenv("*_API_KEY")` call sites on the live origination
path were untouched** — the new mechanism was stranded behind an
unused service. A tenant could register a Cartesia key in
`tenant_ai_credentials` and the call would still use the global
env key.

### What shipped

**`VoiceSessionConfig` gains `tenant_id: Optional[str] = None`.**
None preserves legacy behaviour (env-only). When set, the
orchestrator looks up the tenant's encrypted credential first.

**Three call sites in `voice_orchestrator.py` migrated:**

- `_create_llm_provider` — replaces `os.getenv(api_key_env)` with
  `await get_credential_resolver().resolve(provider_type,
  tenant_id=config.tenant_id, env_var=api_key_env)`. The resolver
  applies the same env_var fallback the bare lookup did, so
  tenant-less paths (Ask AI demo, etc.) keep working unchanged.
- `_create_tts_provider` — Cartesia / Deepgram / ElevenLabs each
  now resolve via the resolver. Removed a duplicate Cartesia
  branch that had been unreachable dead code from a previous
  refactor.
- `_create_stt_provider` — Deepgram Flux key likewise.

**`build_telephony_session_config` propagates `tenant_id`** off
the campaign row into the new field. Implemented via a small
`_campaign_tenant_id(campaign)` helper that mirrors the existing
`_campaign_id` helper. None when no campaign is supplied →
existing dev/test paths preserve env-only resolution.

### Bonus regression fix

The existing `_check_business_hours` had a redundant function-local
`from datetime import datetime` that shadowed test-side
`patch("app.domain.services.call_guard.datetime", …)`. Tests passed
on dev machines only because the real wall-clock time happened to
fall inside the configured business-hours window. Removed the
redundant import — same module-level binding everyone else uses,
and the patches now actually take effect. Test
`test_business_hours_falls_back_to_tenant_tz_on_unknown_callee`
now exercises the frozen-time path it always claimed to.

### Tests (9 new)

`backend/tests/unit/test_orchestrator_credential_wiring.py`:

- LLM: with `tenant_id` set, resolver receives the right
  provider name + tenant_id + env_var fallback hint, and the
  resolved key flows into `provider.initialize(...)`.
- LLM: with `tenant_id=None`, resolver still runs but with
  `tenant_id=None` so it short-circuits to env.
- TTS Cartesia: same wiring proven.
- TTS ElevenLabs: same wiring proven.
- STT Deepgram Flux: same wiring proven.
- `VoiceSessionConfig.tenant_id` defaults to None and accepts
  override.
- `build_telephony_session_config(campaign={tenant_id: "X", …})`
  produces `cfg.tenant_id == "X"`.
- `build_telephony_session_config()` without a campaign yields
  `cfg.tenant_id is None`.

All tests use `unittest.mock.patch` against the resolver and
provider classes — no real API calls.

### What still uses env-only

Out of scope for this sprint and intentionally left:
- `ai_options.py` test/benchmark buttons — admin-user paths
  without tenant context.
- `intent_detector.py` — utility prompt; no tenant association.
- `voice_worker.py:238` — standalone fallback path; not on the
  live dialer-worker → telephony-bridge route.

These can migrate later once the per-tenant config UI surfaces
their use cases.

---

## T2.6 — Legacy persona audit

### What was opaque

The persona-prompt sprint (2026-04-24) introduced layered prompts.
Campaigns without `script_config.persona_type` fall through to
`telephony_session_config.TELEPHONY_ESTIMATION_SYSTEM_PROMPT` — the
hardcoded "All States Estimation" construction-estimating script.

The plan calls for removing that fallback once every live campaign
has migrated. But until this sprint there was **no visibility**
into how many campaigns still relied on it. Operators would have
had to run an ad-hoc DB query, and a one-off query is the kind of
thing that doesn't get rerun.

### What shipped

**`backend/app/core/legacy_campaign_audit.py`** — read-only audit
that runs at lifespan step 2.6 (after the container is up,
before the public router serves requests):

- `audit_legacy_campaigns(db_pool, sample_size=5)` returns a
  `LegacyCampaignAuditResult(probed, total_active, missing_persona,
  sample_ids, error)`.
- Counts campaigns in `running` / `scheduled` / `paused` /
  `draft` whose `script_config->>'persona_type'` is NULL or empty.
- Returns up to `sample_size` IDs so operators have a starting
  point for migration without dumping a full list into the log.
- `log_audit_summary(result)` emits the right log level: INFO when
  fully migrated, WARN when unmigrated campaigns exist, with the
  sample IDs and a doc-pointer.
- `LegacyCampaignAuditResult.to_dict()` shape is `/health`-ready
  for surfacing in the public health response.

**Result is stashed on `app.state.legacy_campaign_audit`** so the
existing `/health` endpoint can pick it up without any new
plumbing.

### What we deliberately did NOT do

- **Did not delete the fallback.** The plan requires zero
  unmigrated campaigns on prod for a sustained window before the
  fallback can be removed safely. This sprint just makes the gap
  visible.
- **Did not force-migrate.** Operators decide when to flip — the
  audit is an observability primitive, not a policy enforcer.

When the audit reads zero in prod for, say, two weeks, the next
sprint can:

1. Replace the fallback at
   `telephony_session_config.py:355-360` with a `raise` instead
   of falling through.
2. Drop the hardcoded `TELEPHONY_COMPANY_NAME` and `AGENT_NAMES`
   constants (lines 21-33).
3. Drop the prompt template constant entirely.

### Tests (9 new)

`backend/tests/unit/test_legacy_campaign_audit.py`:

- No DB pool → unprobed result, error="no_db_pool".
- DB raise → unprobed, error captured (never bubbles into
  startup).
- Fully migrated → `probed=True, missing_persona=0, sample_ids=[]`,
  `fully_migrated=True`.
- Partial migration → counts and sample IDs returned.
- Empty campaigns table → trivially fully migrated.
- `to_dict()` round-trip matches the expected `/health` shape.
- Log emission: skipped → INFO; ok → INFO without WARN; unmigrated
  → WARN with sample IDs in the line.

All tests use a small fake asyncpg pool — no real DB needed.

---

## Verification

```bash
./venv/bin/python3 -m pytest \
  tests/unit/test_orchestrator_credential_wiring.py \
  tests/unit/test_legacy_campaign_audit.py \
  -q
# 18 passed
```

Full new-code suite (18 files):

```
230 passed in 1.70s
```

End-to-end smoke:

```python
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
)
cfg = build_telephony_session_config(
    campaign={"id": "c1", "tenant_id": "tenant-Z"},
)
# cfg.tenant_id == "tenant-Z"  → orchestrator will resolve per-tenant keys
```

---

## File manifest

**New**
- `backend/app/core/legacy_campaign_audit.py`
- `backend/tests/unit/test_orchestrator_credential_wiring.py`
- `backend/tests/unit/test_legacy_campaign_audit.py`
- `backend/docs/scrutiny/2026-04-27-credential-wiring-and-legacy-audit.md` (this file)

**Modified**
- `backend/app/main.py` — lifespan step 2.6 runs the legacy audit
- `backend/app/domain/services/voice_orchestrator.py`
  - `VoiceSessionConfig.tenant_id` field added
  - `_create_llm_provider` resolves via CredentialResolver
  - `_create_tts_provider` resolves via CredentialResolver (and
    drops a duplicate Cartesia branch)
  - `_create_stt_provider` resolves via CredentialResolver
- `backend/app/domain/services/telephony_session_config.py`
  - `_campaign_tenant_id` helper
  - VoiceSessionConfig construction propagates `tenant_id`
- `backend/app/domain/services/call_guard.py`
  - Removed redundant function-local `from datetime import datetime`
    so test patches actually take effect

---

## What's next

Items still tracked in the plan:

- **T1.3 integration** — wire the resilient STT/TTS wrappers into
  the live orchestrator factories. Mechanical now that
  `tenant_id` flows down. Needs staging dry-run.
- **T2.2 integration** — swap `DialerQueueService` for
  `DialerStreamsQueueService` in
  `campaign_service._get_queue_service` and `dialer_worker`'s
  consume loop. Opt-in via `DIALER_QUEUE_BACKEND=streams` env
  recommended.
- **T1.4** — Twilio / Telnyx adapters (still blocked on sandbox
  credentials).
- **Legacy fallback removal** — pending the audit reading zero
  in prod for a sustained window.
