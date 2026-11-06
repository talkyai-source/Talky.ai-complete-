# Saved model availability

AI Options now accepts only the traditional models displayed in its menu.
Reading an old saved profile preserves its exact selection. Existing known
legacy models can still run; hiding a model from the menu is not evidence that
the provider retired it.

At cascaded call setup, an unknown model under a recognised provider uses that
provider's offered platform default for this call. A known model also uses that
default when a complete, fresh model-list observation for the same tenant and
exact credential proves it absent. The original profile, caller configuration,
provider choice, credential and generation settings are retained. Realtime is
outside this remediation.

The current defaults come from the shared existing menu: Groq
`openai/gpt-oss-20b`, Cerebras `gpt-oss-120b`, Gemini `gemini-3.8-flash`, OpenAI
`gpt-6-luna`, and DeepSeek `deepseek-flash`. An unknown provider remains an error;
there is no justified same-provider substitute.

Every fallback emits `llm_model_fallback` at ERROR. A separate transaction tries
to append `ai.model.runtime_fallback` to the existing audit chain within one
second. It records tenant, provider, selected model, reason and the requested
model's digest/length, never credentials or arbitrary requested-model text. A
database/signing/chain failure emits `llm_model_fallback_audit_unavailable` and
does not fail the call. An audit row cannot be guaranteed while the audit
database is unavailable. Cancellation still cancels setup.

## Nightly comparison

`scripts/check_saved_models.py` pages the tenant directory, reads each profile
with an explicit tenant predicate and checks the selected provider using that
tenant's existing credential resolver. It makes model-list GETs only. It does
not edit profiles or issue text-generation requests. Existing credential
resolution may update a credential's `last_used_at`; this is not an entirely
read-only database connection.

Successful catalogs are cached in Redis under a hash of tenant, provider and
credential identity/material. They are authoritative for 30 hours and expire
after 36 hours. A rotated credential needs its own observation. Missing, stale,
malformed or failed observations remain unknown; they never retire every model.
Provider HTTP errors, empty lists and incomplete pagination do not overwrite a
successful catalog with an empty one.

The report has explicit `retired`, `default_unavailable`, `unknown`, `issues`
and `complete` fields, plus profile/credential counts. Model identifiers from saved rows are represented by
digests, so corrupt user-controlled strings cannot become log content. No
credential value is returned in the report; per-key issues carry a SHA-256
credential fingerprint to distinguish account observations. Exit status is
0 for a complete healthy report, 2 for a model/coverage problem, and 1 for
startup failure.

Bounds: 100 tenant IDs per page, 1,000 tenants per default run (maximum 10,000),
300 seconds per default scan (maximum 600), 15 seconds per credential resolution,
16 distinct configured keys per profile, 10 seconds per provider catalog, ten catalog pages, 5,000 model IDs and
1 MiB of streamed decompressed response per page. Call setup makes no model-list
request and waits at most 150 ms for an availability cache read.

`talky-model-catalog.timer` schedules the hardened `User=talky` oneshot daily at
03:45 host time, with a six-minute service ceiling and persistent catch-up.
Installation needs the timer's enable line in `systemd/install-services.sh`.
Item 16 supplies the `OnFailure` notification integration and destination;
the unit's nonzero status alone is not proof that an operator received an alert.

## Owner verification

After release approval and normal environment/service installation, the owner
can run the report with the application's configured process environment:

```sh
cd /opt/talky/backend
venv/bin/python scripts/check_saved_models.py
systemctl status talky-model-catalog.timer talky-model-catalog.service --no-pager
journalctl -u talky-model-catalog.service -n 50 --no-pager
```

Review affected tenant UUIDs against their saved profiles. Select a supported
model through the authenticated AI Options save path; do not bulk-remap rows
from a provider error response. If a platform default is absent, the owner must
qualify a replacement and update the offered/default catalog before relying on
it. A fallback cannot make an unavailable credential, provider or platform
default usable. The audit does not certify voice quality, latency or pricing.

For a platform credential, the nightly comparison also enumerates every key in
that provider's actual shared resolver pool (currently DeepSeek for LLMs), using
the existing single/CSV parser and without advancing rotation for enumeration.
BYOK profiles check only their tenant key. A profile with more than 16 configured
keys is explicitly incomplete; at most 16 are probed, and the rest stay unknown.
The global deadline can also leave coverage incomplete. Rotation/revocation
after the run requires a new observation; no key inherits another account's
catalog. Provider-list outages remain visible and require owner investigation.

No production provider requests, phone calls, timer execution, notification
delivery or real PostgreSQL audit-chain drill were run as part of local unit
verification. The release freeze remains in effect.
