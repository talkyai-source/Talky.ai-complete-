# Tenant provider key validation

Settings now exposes a Provider API keys section for the signed-in tenant.
Members may read masked status; tenant, partner and platform administrators
within that tenant may save, check or disable keys. The backend enforces this
boundary independently of the screen. Saving a key does not change the selected
AI provider or configure a new provider offering.

`POST /api/v1/tenant-ai-credentials/` checks the candidate API key before
encrypting or replacing any active key. Unsupported providers and non-API-key
credential kinds are refused. A rejected key returns 400 `provider_key_invalid`;
the previous active key remains unchanged. Provider/network/rate-limit failures
return 503 `provider_key_check_unavailable`, rather than certifying an invalid key
or disabling an existing key. A valid result is saved with the encrypted key,
masked suffix, validation status and UTC check timestamp in the existing atomic
create/rotate audit transaction. The plaintext is never returned or audited.

The list response adds two nullable fields:

```json
{"validation_status": "valid", "last_checked_at": "2026-11-06T01:00:00+00:00"}
```

`validation_status` is `valid` or `invalid`; NULL with NULL timestamp means not
checked. Migration 0089 does not label old credentials valid. Existing activation
status remains separately `active` or `disabled`.

`POST /api/v1/tenant-ai-credentials/{credential_id}/check` checks an existing
tenant-owned key without holding a database transaction during provider I/O.
The saved observation and audit event use an ID/tenant/version comparison. A
key rotated or changed during the check returns 409 `credential_changed`; a slow
result cannot overwrite that newer version. Provider outages keep the prior
observation. Recheck does not automatically enable or disable the key. Replace
or explicitly disable an invalid key; the existing runtime resolver/fallback
and cross-process cache TTL remain unchanged.

All probes use server-owned HTTPS URLs, one read-only request, a five-second
overall timeout and a one-MiB streamed response cap. They do not follow redirects
or generate speech, text, calls, transcripts or tokens. ElevenLabs' public
models response is insufficient authentication evidence, so its account identity
read is used instead. AssemblyAI uses a one-entry transcript metadata list.
Provider response bodies are discarded, never stored in the observation/audit.
Restricted keys must permit this validation endpoint; a403 can mean insufficient
validation permission rather than a malformed secret.

A successful credential check establishes access to that endpoint at that time.
It does not prove credit, model or generation permissions, regional availability
or call readiness. The selected provider still needs its normal configuration
and owner acceptance. No live provider call or production credential was used
in the local regression tests.

Deploy the linearly chained 0089 migration before code that selects the new
columns. Its nullable metadata and NOT VALID observation constraint preserve
existing keys; owner checks can inspect unvalidated legacy rows separately.
Downgrade removes only metadata objects owned by 0089, losing those derived
observations while retaining key ciphertext, activation/history and immutable
audit receipts. Adopted pre-existing compatible objects are preserved. Actual
PostgreSQL migration/downgrade rehearsal is a separate gate from DDL unit guards.

Frontend key text exists only in the current form, clears after submission or
identity changes, and is never written to browser storage. Pending writes are
serialized and old actor/tenant responses are ignored. Backend validation and
tenant predicates remain authoritative.
