# AG05 independent persistence evidence inventory

Date: 2026-10-05. Worktree: `tmp/production-ready-20261004`. The initial audit began after AG03 (`27c04347`); the AG04-only changes were committed as `c93a05e3` while this audit ran. The final acceptance below exercises the uncommitted AG05 candidate layered on that commit; the parent's release manifest supplies the eventual source binding. This inventory retains the pre-repair observations separately. It is not deployment or customer-call evidence.

## Final measured acceptance

`backend/tests/integration/test_ag05_lead_evidence.py`: **30 passed, no skips, 23 existing `datetime.utcnow` deprecation warnings, 32.49 seconds**. The coherent result is `lead-evidence-final.txt`. The fixture used the actual migrated public tables through **`0057_transcript_save_state`**, not a minimal replacement schema. `migration.txt` records the successful upgrade from retained head `0056` and the new column's `unknown` default and NOT NULL constraint. This was a forward upgrade of the retained synthetic database, not a new full-bootstrap rehearsal or a production migration.

Covered boundaries:

- Current and additional contact proof, independent value/confirmation identities, manual edits and withdrawals, invalid/cancelled states, required fields derived from the call's actual campaign, same-tenant wrong-binding refusal, foreign-tenant controls, and a newer human review on an older call.
- Real SQL permission failure during capture, successful later-turn retry, failed revocation without poisoned retry state, conditional stale revocation versus newer same-value/different-proof and different-value evidence, and stale initial writes.
- Actual OpenAI and xAI event parsers through the native bridge, synthetic matching playback receipts, serialized contact persistence, current confirmation revision, incremental transcript persistence, and actual call/contact API reads. No provider connection or audio synthesis is used.
- A saved confirmation revised from yes to no while contact UPDATE permission is denied: the original database row remains available for audit, while call details, contact details and call-list projections cease presenting it as confirmed. The manual-edit negative control retains authority.
- Atomic final calls/child-transcript writes, failed child insertion rollback, retained same-process retry evidence, partial/failed/complete markers, duplicate final jobs, stale incremental writes, and current-call transcript precedence over later historical child rows.
- A genuine child Python process exits with `os._exit(23)` after an incremental commit but during an uncommitted final write. A subsequent fresh database/API read retains only the previously committed partial text; it does not invent the lost correction or a confirmed contact. The bounded child process owns only this test's synthetic identities and restricted role.
- Hangup contact persistence fails once and then succeeds after the transcript commit. A separate persistent-contact-failure case leaves `contact_save_failed` evidence visible even after final transcript persistence and the independent business-summary save report complete.
- Business notes retain the caller quote and `needs_review` / `caller_quote_only` status, and a human correction survives repeated extraction. The classification supplied to this test is synthetic; no model interpretation is evaluated.
- Actual ASGI lead routes serialize dates/provenance, reject unauthorized permission access and foreign-tenant edits, and allow a manual withdrawal. Authenticated identity and effective permission lookup are explicitly synthetic overrides; this is not deployed login or JWT authentication proof.

The fixture creates a unique `NOLOGIN NOSUPERUSER NOBYPASSRLS` role and verifies its flags and unscoped read denial. The existing call-list implementation deliberately sets the application's bypass GUC but retains its explicit tenant predicate; this test does not label that internal query as RLS-only enforcement. Fixture teardown left **zero synthetic AG05 tenant rows and zero AG05 roles**, verified read-only after the final run. The disposable PostgreSQL service remains running and idle for coordinated final checks.

Fourteen annotated expected-versus-observed synthetic examples are retained in `api-state-examples.json`: additional-contact cancellation, invalid contact, manual withdrawal, legacy unknown transcript state through ASGI, native pending/confirmed/revised contact, business review note, failed/complete transcript save, recovered/failed contact save, failed-revocation projection and manual revision immunity. Synthetic fixture ownership UUIDs are normalized in this artifact. A completed fake transport receipt is not evidence that a person heard or understood speech.

Exact final command from the worktree's `backend`:

```powershell
$env:TEST_DATABASE_URL='postgresql://talky@127.0.0.1:55434/cp04_acceptance_test'
$env:AG05_EVIDENCE_OUTPUT='../docs/sessions/artifacts/ag05/api-state-examples.json'
& 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe' -m pytest -q tests/integration/test_ag05_lead_evidence.py 2>&1 | Tee-Object '../docs/sessions/artifacts/ag05/lead-evidence-final.txt'
```

The owned test file also passed `python -m ruff check tests/integration/test_ag05_lead_evidence.py --select F`; the owned diff passed `git diff --check`. Source changes belong to the parent and the canonical-transcript/native-capture owners. This independent slice changes tests and evidence only.

Intermediate failures were retained rather than relabelled as passes. `manual-order-initial.txt` and `manual-order-contact-retry.txt` exposed draft SQL parameter-type ambiguity before their intended semantic assertions; those are not proof that the manual-order hypothesis failed. `finalizer-projection-targeted.txt` records three passing controls and a fixture that incorrectly assumed a recovered contact could have no additional recovery-note row. `lead-evidence-snapshot-fixture.txt` records 29 passes and a business-note fixture whose declared `action_results=None` did not match the database's seeded JSON default; the existing snapshot fence correctly refused it. The final fixture explicitly supplies the same NULL snapshot and all 30 cases pass.

## Audited baseline chain and trust boundaries

- Traditional `voice_pipeline/turn_ender.py` calls `TranscriptService.flush_to_database` after a completed turn, then `capture_turn_slots`. The dialer call binding resolves the real persisted call separately from the voice-session buffer ID. An interrupted turn may instead reach the hangup flush.
- Native `realtime/bridge.py` accumulates transcript turns/revision metadata in `TranscriptService`. Its contact tasks are serialized and get a two-second teardown drain. At this baseline there is no native per-turn transcript flush. Telephony lifecycle calls `save_call_transcript_on_hangup` for both pipelines.
- `voice_pipeline/lead_slot_capture.py` derives current and additional contact fields from live state and invokes `LeadCaptureService`. A failed ordinary capture is retried on a later turn while the session remains alive. That is not a durable queue.
- `LeadCaptureService.capture` enforces contact normalization, caller pending versus confirmed states, source trust, tenant ownership of the call, and conditional summary revision predicates. A human edit has the highest source rank. Post-call business capture stores caller quotations as `needs_review` / `caller_quote_only`; a quotation is not verified business qualification.
- `call_summary/store.py` hashes the raw structured transcript, rendered transcript and action results. Its stale-write predicates prevent a job based on an older stored snapshot from replacing a newer summary. `call_summary/business_details.py` similarly fences derived notes. These existing protections must remain when canonical revision handling is added.
- `/calls/{id}/lead-details` and `/contacts/{id}/lead-details` return saved data, analysis status and CRM receipts. The current frontend `CapturedDetail` type and `LeadDetailsPanel` omit some contact audit columns and interpret every null value as a decline. Invalid or withdrawn contacts do not support that interpretation.

## Reproduced pre-repair defects

1. **Current confirmation proof is dropped.** A confirmed current email with `confirmation_evidence=caller_repeatback` reaches PostgreSQL as confirmed but without that evidence. The earlier-contact helper includes the evidence; the current-contact serializer does not. The call API test fails on the missing reason before it can check the contact API's missing `confirmed_at` projection.
2. **Additional contact withdrawal fails.** Live capture names a second address `email_2`, but the revoke service accepts only `email` and `phone`. After withdrawal, the real database still contains the second confirmed value.
3. **Same-tenant call binding can be wrong.** The writer accepts a different existing campaign and lead owned by the same tenant rather than the call's actual binding. Actual migration `0041_tenant_campaign_fk` already rejects foreign-tenant bindings. This is not evidence of a cross-tenant read or write bypass.
4. **Final transcript failure destroys retry evidence.** With real PostgreSQL UPDATE permission denied for `calls`, the hangup persister swallows the write failure, then clears and seals its final in-memory transcript. The expected retained-evidence/retry control fails.
5. **One revoke failure can poison subsequent retries.** A real transaction failure during revoke is followed by a pending replacement write. The database correctly refuses to downgrade the still-confirmed value, but `stored=False` is memoized as if the pending write had succeeded. The next turn skips both operations and leaves the old confirmed value. The focused fault test reproduces this with a database division-by-zero in the first revoke transaction and normal production SQL thereafter.
6. **The existing manual edit cannot withdraw an email.** The UI's null/empty edit reaches the real handler but returns 422 because confirmed contact normalization rejects an empty value. A protected manual tombstone is not yet represented by that route.
7. **Required-field display depends on optional query input.** A call with an actual required email returns an empty missing-required list when the optional campaign query is omitted. The route must derive the call's campaign rather than accepting the query as authority.
8. **The incremental transcript writer ignores its tenant argument.** Passing tenant A and a target call owned by tenant B writes A's synthetic transcript into B's row under the internal RLS bypass. This is a reproduced internal wrong-binding failure, not a demonstrated public endpoint exploit. Scoped writes must require the bound tenant.

Source locations at audit time: `lead_slot_capture.py:197–247,285–328,427–435,470–530`; `lead_capture_service.py:258–320,336–379,435–450`; `call_transcript_persister.py:290–440`; `transcript_service.py:250–291,422–492`; `realtime/bridge.py:365–373,1086–1169`; `lead-details-panel.tsx:84,138`. Line numbers will move with the repairs.

## Preserved pre-repair PostgreSQL controls

The database is the retained disposable PostgreSQL 16 instance at loopback port 55434, `cp04_acceptance_test`, initially at actual public migration head `0056_billing_refund_snapshots`. The new fixture does not reconstruct minimal tables. It creates UUID-scoped synthetic tenants, campaigns, leads and calls in the migrated tables and a unique `NOLOGIN NOSUPERUSER NOBYPASSRLS` role, verifies its flags and unscoped read denial, then removes its own mutable rows and grants. The production RLS policies are used unchanged. Internal functions can still deliberately set the application's existing bypass GUC; the role flag is not a claim that all platform-internal writes are tenant scoped.

The initial run had **5 failed / 4 passed**. Two failures were not product defects: the foreign binding was correctly rejected by an existing composite FK, and the restricted fixture lacked UPDATE permission required by the real campaign `FOR SHARE` trigger. Both were corrected transparently in the fixture; the initial log is retained.

The corrected baseline had **4 failed / 6 passed**, two existing `datetime.utcnow` deprecation warnings, in **15.94 seconds**. Passing controls verify manual correction survives live retry/withdrawal; a foreign call tenant cannot write/read; a transient real SQL permission failure retries once on the next turn without duplication; invalid tombstones retain their actual status through the call API; composite foreign tenant binding is rejected; and raw ASR revision metadata can be durably stored while retaining the original in-memory utterance.

The additional targeted revoke-retry test had **1 failed / 10 deselected** in **4.67 seconds**. The three added API/ownership tests had **3 failed / 11 deselected**, one existing deprecation warning, in **7.04 seconds**. These non-overlapping selections account for fourteen distinct tested cases, eight failures and six passes; they were not one coherent combined run. A fifteenth, controlled abrupt child-process exit case is authored but awaits the reviewed `0057` status migration and implementation before execution. Later repair results belong in a separate final log.

Commands from this worktree's `backend`, using the original workspace `backend/.venv/Scripts/python.exe`:

```powershell
$env:TEST_DATABASE_URL='postgresql://talky@127.0.0.1:55434/cp04_acceptance_test'
python -m pytest -q tests/integration/test_ag05_lead_evidence.py
python -m pytest -q tests/integration/test_ag05_lead_evidence.py -k failed_revoke
python -m pytest -q tests/integration/test_ag05_lead_evidence.py -k 'manual_withdrawal or required_fields or incremental_transcript'
```

Evidence: `lead-evidence-initial.txt`, `lead-evidence-baseline.txt`, `revocation-retry-initial.txt`, `api-ownership-initial.txt`. Only synthetic values appear. These direct endpoint calls exercise production query/projection code; they do not test HTTP authentication, RBAC routing, deployed login, browser rendering or a provider.

## Ownership and review limits

The native/shared-capture owner implements bounded provenance, conditional revocation, authoritative call binding, per-turn transcript flushing and read-time contact-proof projection. The canonical-transcript owner implements validated revision consumption, scoped transactional writes, finalization retries, current snapshot precedence and migration `0057`. The parent owns API/frontend status presentation and the final combined validation. This independent slice supplies the actual database/ASGI/fault acceptance and reported the concrete failure boundaries above. Earlier root UI review findings about partial-versus-known-loss wording, latest-call scope, absent-call warnings and malformed historical metadata were addressed by their owner; frontend render/build results are recorded separately, not counted as these 30 database cases.

## Recovery limits remain explicit

A durable contact row, previously committed transcript snapshot and raw revision metadata are different artifacts. A prior successful write can survive a process death; uncommitted session state cannot. A successful summary retry cannot reconstruct a missing caller confirmation, missing playback receipt or never-persisted final contact from guesswork. At the audited baseline, native transcript evidence is especially exposed before hangup because accumulation is only in memory, even when some separate contact writes succeeded.

No actual production voice-worker termination, automatic restored-process recovery, external provider response, STT/TTS audio, hearing proof, human annotation, contact-cohort accuracy rate, or end-to-end Sales Hub browser acceptance has been measured by these tests. The controlled child-process exit proves the specific database transaction boundary described above; retaining a Python buffer is only same-process retry support. If both contact and transcript evidence failed to commit before process death, these changes cannot reconstruct it. A failure note also requires a successful database write. The contact read projection protects the tested API surfaces; it is not evidence that every CRM/effect consumer has adopted the same gate (AG06 handoff). Native DNC durability at call end remains the separate OP05/OP07 lifecycle handoff and is not claimed repaired by AG05.

The disposable server is currently running for coordinated AG05 acceptance; no production database, customer data, provider calls, email sends or source implementation was changed by this independent test slice.
