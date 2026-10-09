# AssemblyAI 3.6 Pro integration — 10 October 2026

## Authorized scope

The owner explicitly requested AssemblyAI Universal-3.6 Pro alongside Flux, all
relevant streaming STT controls including Fast/Balanced/Accuracy modes, English
only, documentation review before implementation, and verification. This is a
specific provider exception to the feature freeze; unrelated work remains frozen.

Implementation is isolated in `codex/assemblyai-36-20261010`, initially based on
main `3951efe9` and rebased without conflicts onto `67933e1d`, preserving the new
interrupted-goodbye handling and synthetic-caller timing fixes. The original
checkout and other dirty worktrees were preserved.
See [integration and operating notes](../../backend/docs/assemblyai-streaming.md)
for the exact controls, protocol, credential configuration and release sequence.

## Evidence collected

| Requirement | Local evidence | Live release status |
| --- | --- | --- |
| Exact 3.6 Pro, three modes, English | Protocol tests assert model/mode acknowledgement and `language_codes=["en"]`; API and SQL reject a non-English AssemblyAI selection | Requires credentialed provider probe |
| Flux and AssemblyAI selectable | Actual React DOM tests change engines, modes, save, remount and reload; existing Nova remains | Requires deployed authenticated UI check |
| All 24 supported settings | DOM interactions, Zod/Pydantic validation, provider request assertions, real SQL/API roundtrips | Live provider effects not yet measured |
| Durable per-tenant controls | Actual PostgreSQL 16.1, raw and production JSON codecs, NOSUPERUSER/NOBYPASSRLS reads and writes | Migration not applied to production |
| Inbound/outbound/test transport wiring | Selected session, inbound snapshot/rehydration, Twilio/Vonage format, campaign test and prewarm regression checks | No real call claimed |
| Reliable interruption and shutdown | Protocol tests cover hesitation growing into an interruption, empty revised finals, mute boundaries, cancellation, prewarm ownership, failed writes and cleanup | Needs representative telephone audio |
| Useful context without hidden rules | Successful TTS updates context; failed/interrupted text is excluded; campaign/system prompts and implicit keyterms are absent | Live accuracy comparison pending |

Completed verification runs (some overlap; do not sum as unique test counts):

- After rebasing, the combined adapter/context/runtime/caller-turn suite passed
  392 checks with one skip. This includes the new upstream goodbye/queued-speech
  regression modules and the AssemblyAI-specific outage advice.
- 286 call/runtime/regression checks passed, one pre-existing skip.
- 43 caller-turn and runtime checks passed after adding empty-final handling.
- 68 adapter/settings tests passed; scoped Ruff and Black passed. Deterministic
  timing tests reproduced and fixed per-frame send-latency drift without allowing
  catch-up bursts after network backpressure.
- 87 config/catalog/credential unit checks passed.
- Final config review reproduced a case/whitespace mismatch between saved engine
  names and runtime selection. Canonicalization now precedes identity validation
  and the credential check; mixed-case/whitespace requests cannot bypass them.
  The affected suite, including real PostgreSQL, passed 119 checks.
- Review also reproduced stale credential absence after opening AI Options
  before registering a tenant key. AssemblyAI now re-reads the tenant credential
  for each resolution, including across dashboard/call-worker resolver instances;
  addition, rotation and revocation take effect without process restarts. Existing
  environment fallback semantics and other providers' caching remain unchanged.
  The affected config/runtime/credential suite passed 91 checks, including seven
  credential lifecycle cases.
- Six real PostgreSQL integration checks passed, including all modes, every
  setting, tenant isolation, migration constraints and protected downgrade.
- 114 context/greeting/playback checks passed, with two explicit skips. Empty
  audio, failed playback receipts and late interruptions do not become context.
- 19 frontend DOM/API/control checks passed; TypeScript checking and the production
  build passed (70 static pages). Scoped lint has zero errors and one pre-existing
  page effect warning. Desktop and 390-pixel mobile renders were inspected without
  horizontal overflow. Browser save/reload retained mode, prompt, silence override
  and English using the actual page with synthetic auth/API transport; this does
  not prove a deployed backend or live provider session.

The PostgreSQL fixture uses an explicitly designated localhost test database and
random isolated schemas and roles. Cleanup completed; shared application tables,
production data, and the running database service were unchanged.

## Live verification prerequisite

No API key appeared in the task text. Presence-only checks found no
`ASSEMBLYAI_API_KEY` in the standard checked local or server backend environment
files. The owner has been asked to designate a private credential location.
No key values were printed or committed.

Presence-only rechecks on this continuation still found no key in the checked
local/server environment files. The production API process environment could not
be read with the available SSH operator permissions, so these checks do not prove
the absence of keys in every secret store or process. No tenant was selected
arbitrarily to obtain its private credential.

A synthetic mono 16 kHz PCM clip was generated locally using Windows speech
synthesis: a fictional Alex Taylor and `alex.taylor@example.com`. The production
adapter probe refuses to run without the key and reported that missing prerequisite.
No synthetic or customer audio has been transmitted to AssemblyAI by this work.

Do not mark the goal complete or claim improved email accuracy from unit tests.
Remaining: real provider acknowledgement/transcription in all modes, migration and
deployment, authenticated selection/save/reload, and an English test call. The
existing paid-use readiness freeze and broader acceptance gates remain unchanged.
The new PostgreSQL integration module is explicitly included in CI. Production
activation must use the existing supported deployment procedure and its measured
drain evidence; this integration does not bypass those requirements.

## Published review and CI

[Draft PR 20](https://github.com/talkyai-source/Talky.ai-complete-/pull/20) publishes
the integration. Initial head `4dc5d746` passed the Vercel preview deployment,
secret scanning, telephony ingress checks, Admin checks and fast voice suite.
The full unit/security run passed 12,311 checks with 152 skips and 18 subtests,
but failed one migration-head assertion that still expected 0064. The assertion
is updated to require 0065 and explicitly preserve its parent and both released
histories; this is not recorded as a green full suite until CI reruns it.

The general Backend and SQL jobs could not start PostgreSQL because Docker Hub
returned a pull-rate limit and authentication-endpoint timeouts. The frontend
audit failed on the already recorded `braces` advisory (11 dependency paths).
These failures remain visible; no workflow gate or vulnerability is suppressed.
The PR checks provide final-head status separately from these initial results.
