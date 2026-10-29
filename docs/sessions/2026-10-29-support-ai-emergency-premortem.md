# Customer-support AI and emergency-access pre-mortem — 29 October 2026

This owner record covers bounded repairs in the production-readiness freeze. It does not approve a paid release. The [execution tracker](../production-readiness/execution-tracker.json) and the retained [Groq qualification failure](2026-10-28-groq-section-qualification.md) remain authoritative. No new provider request, real customer message, telephone call, or production mutation was made for this work.

## Reproduced failures and repairs

| Failure narrative | Evidence and repair |
|---|---|
| A malformed model action copies a caller's private information into incident logs. | `_repair_action_json` logged the first 160 raw characters. A synthetic health-detail string survived the global redactor. Diagnostics now retain the event and character count, with unchanged parsing. |
| A failed knowledge request leaks caller words disguised as a section identifier. | `run_knowledge_lookup` logged unvalidated model arguments. Logs now include only identifiers present in the scoped catalog, counts, integer cursors, and bounded status/reason fields. |
| A follow-up answer sees a refund promise but loses its exclusions. | Recent knowledge silently sliced complete source text at 2,500 characters. The reproducer retained “Refunds are available” while dropping a later exclusion without disclosure. Short sources remain complete; larger sources retain validated original section references for a fresh read. Memory is bound to the same tenant/campaign catalog snapshot, including its source revision identity. |
| The model promises an unavailable handoff and hangs up. | The exact retained Q43/H13 response chunks now replay through the full traditional turn finisher. Neither disconnects or opts out the caller. Q43's unavailable sales-handoff promise remains unchanged and failed; the test explicitly preserves it. |
| An attacker with an administrator session guesses or replays an approval code. | Emergency approval now requires enabled TOTP, a consumed counter, and a current authenticated profile/session and platform-role check. Each account receives five proof attempts per 300-second fixed window through an atomic Redis claim, before comparison, with a two-second timeout. Changing request IDs, refusals, rollback, or successful comparisons do not reset the budget. Missing, failed, or unconfirmed Redis claims fail closed. |
| Concurrent administrators overwrite an approval or receive multiple emergency tokens. | Approval and issuance now lock the request row inside the existing authenticated transaction. Issuance checks requester identity and distinct verified approvals under that lock. Historical unverified approvals and tokens cannot establish authorization. Unsupported SMS/email/in-person proof methods fail closed. |
| Operators see false success, inconsistent expiry, or lose an issued token during notification failure. | Missing/inactive-row mutations return conflicts. All list filters use bounded database pages; pending/approved expiry is projected consistently in detail and listing. Each mutation writes an actor/event/time entry to `actions_taken` in its transaction. Redis notification failures preserve committed results and log structural failures. The secondary audit response says `audit_submission_accepted`, never that buffered audit submission proves durable persistence. |

The dependency factory refreshes the current Redis client and database-pool lifecycle. Legacy plaintext approval-code removal is owned by the separate [0083 migration](../../backend/Alembic/versions/0083_customer_support_contracts.py); other approval/review/session history is retained.

These changes preserve model-owned conversation. There is no lexical answer judge, scripted support dialogue, generic prompt warning, new provider, or broad architecture replacement. Knowledge reads still use the existing call snapshot. Database serialization is scoped to the authenticated account and emergency request; independent requests do not need a process-wide lock.

## Verification

- AI repairs: **34 passed** in `test_action_envelope_never_spoken.py` and `test_knowledge_followup_memory.py`.
- Separate support-boundary selection: **198 passed**, covering captured Q43/H13 finisher replay, knowledge evidence/sections, caller-authorized endings, action/recipient confirmation, transfer capability gates, interrupted history preservation, and support-report receipts. External providers and media are mocked.
- Emergency final selection: **44 passed** in `test_emergency_access_authorization.py` and `test_bh_auth_backend_emergency_access_tz.py` (9.86 seconds; one existing Starlette warning).
- Final native PostgreSQL selection: **1 passed** in `scripts/database/tests/test_emergency_access_contracts_pg.py` (98.40 seconds; three existing Pydantic warnings).
- [Independent private Redis 7.0.15 verification](artifacts/2026-10-29-emergency-redis-budget.json): the exact candidate Lua and proof-budget helper admitted five of twenty concurrent attempts, isolated accounts, preserved fixed expiry and missing-TTL counts, and rejected missing/unreachable Redis and a lost reply after a committed claim. The fixture used a private Unix socket with TCP disabled; the installed Redis was untouched. Lua SHA-256: `23867c8699d570a409c9c018c01d4157908ac7b0f2f1a162e9666fa2bbe58f90`.

Run the emergency units from `backend` with `PYTHONPATH=.`. Run the PostgreSQL module from the repository root with `PYTHONPATH=backend` and `TEST_POSTGRES_BIN` pointing to PostgreSQL 16 binaries. Both use `python -m pytest <the named files> -q --tb=short`; the PostgreSQL fixture does not accept an existing database URL.

The native PostgreSQL fixture creates its own loopback PostgreSQL 16 cluster and canonical 0082→0083 database. The real service, JSON codec, TOTP verifier, transactions, and independent connections are exercised. Both approval callers must be observed blocked on the real request-row lock before release; both approvals must survive, a used TOTP must be rejected, and exactly one token/event may be issued. Expired pending and approved requests must agree between detail and filtered listing. Authenticated-session lookup, secret decryption, and the separate Redis budget are replaced in that database test. It does not establish a deployed identity, alert, or emergency-token consumer.

The first PostgreSQL attempt failed because the synthetic enabled-MFA row lacked required `verified_at`. The fixture was corrected without weakening the schema. Local tests use the existing Python 3.12.12 / pytest 7.4.4 environment; no exact-requirements reinstall is claimed. Earlier checkpoints (28, 31, 35, and 37 emergency unit checks) are superseded by the final selection and are not added together.

## Coverage and remaining acceptance

| Package | Evidence added here; remaining boundary |
|---|---|
| AG01 | No new saved-profile qualification. Capture designated saved/effective profile and SIP/live behavior. |
| AG02 | Complete-source memory, scoped references, privacy and evidence lifecycle tests. Actual semantic/native/deployed knowledge qualification remains open. |
| AG03 | H13 captured replay verifies ending authorization. Long Realtime relationship corrections beyond twelve messages remain open. |
| AG04 | Captured finisher, ending and interruption-history boundaries. Q43 fails; configured models and real voice still require qualification. |
| AG05 | Contact revision and stale/ambiguous action-authorization tests. Real spelling/correction → saved lead → displayed evidence → connector acceptance remains open. |
| AG06 | Mocked action and support-report receipts distinguish provider acceptance from delivery. Original-account email/calendar/CRM and operator reconciliation remain open. |
| AG07 | Small boundary repairs preserve existing architecture. No release acceptance is inferred. |
| OP03 | Interrupted-history tests. Real STT/TTS recovery, playback and human hearing remain open. |
| OP04 | No new selected-trunk/caller-ID handset evidence. |
| OP05 | No new PBX restart, ambiguous-owner or cross-process reconciliation evidence. |
| OP06 | Availability and fresh transfer-policy checks pass offline. The scoped carrier/runtime gate remains. |
| OP07 | Caller ending/DNC authorization and diagnostic privacy checks. Consent, per-store deletion, recording retention and deployed reconciliation remain open. |
| OP11 | Emergency approval/issuance hardening. A real emergency-token consumer, durable alert delivery, operator drill and complete deployed audit acceptance remain missing. |

## Next model acceptance proposal

Q43's captured requests already contain the corrected `customer_support@17` guide, scoped source, and only knowledge/end-call tools. No remaining instruction contradiction was found. The supported inference is a capability-adherence failure for the captured Groq `openai/gpt-oss-20b` profile; unchanged reruns cannot repair it.

Capture the actually saved paid primary and fallback profiles first. Source defaults currently describe Cerebras `gpt-oss-120b` at temperature 0.6 / visible target 90, with Groq 20B fallback; defaults are not evidence of the selected campaign. The current `qualify_agent_sections.py` transport supports Groq/OpenAI/Gemini but lacks the existing Cerebras adapter path. Extend that evaluator through the existing adapter and dry-check request/profile/tool/source equality before proposing one actual run. This adds evaluator coverage, not a provider or a silent production switch.

Proposed initial sample: unchanged Q01, Q43, Q60, H04, H11, H13; frozen sources and rubrics; at most 24 attempts, 30,720 reserved output tokens, 35 seconds per case, and 1,200 seconds overall. Enforce the actual wire output ceiling against that budget. Review every answer and retain every failure; any unavailable handoff or critical unsupported claim fails the candidate. No retry-until-pass process is permitted. The existing representative conversation/audio cohort and human review remain separate gates.
