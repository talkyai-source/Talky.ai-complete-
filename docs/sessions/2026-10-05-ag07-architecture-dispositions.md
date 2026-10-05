# AG07 architecture dispositions at the integrated candidate

Reviewed source baseline: `e4f76f8733cceeb67da53cc14a00e7ba5f892bf9`.
This records each existing architecture observation against the current repairs.
It does not create a restructuring project or claim that runtime acceptance is
complete. The historical size measurements remain historical observations.

| Observation | Current evidence and bounded disposition | Remaining work |
| --- | --- | --- |
| A01: large services with mixed responsibilities | Keep the current entry points. The changed responsibilities already use existing helpers: prepared knowledge evidence, receipt-aware action execution, provider-slot ownership, prewarm results and transactional call settlement. File size alone supplies no reason for another extraction. The full unit/security and migrated database runs passed at `44dcc236`. | Review new failure-path changes at their owning seam; live cancellation/recovery acceptance stays with OP02/OP05. This is not a claim of complete layer independence. |
| A02: lifecycle acquisition and settlement ownership | Flux socket slots acquire at connection setup and transfer with prewarm ownership; media receipts remain attached to their utterance. Final call settlement locks the owned lead, consults durable caller opt-out and preserves actual call outcome. Unknown external effects are not blindly replayed. Redis leases retain their existing TTL and conditional backing-key reconciliation; ownership is not retained indefinitely. | Multi-process Redis recovery, actual media failure and deployed lifecycle settlement remain OP02/OP03/OP05 acceptance. Outbound voice admission is post-answer; these repairs do not establish a pre-ring global cap. |
| A03: concrete providers and container lookups inside domain services | `VoiceOrchestrator._create_stt_provider`, LLM/TTS assembly and media gateway assembly still select infrastructure. Voice action `_pool` accepts an existing session pool but otherwise uses the container; native knowledge setup also reads the container. Corrected README and diagram describe these actual boundaries. A new dependency framework is not justified by this observation. | Keep injection at the existing touched seams where it enables a verified repair. The saved-configuration failure paths are being traced separately; documentation is not their fix. |
| A04: native runtime imports shared session classes from the orchestrator | `app/realtime/runtime.py` imports `VoiceSession` and `VoiceSessionConfig`; the latter extends `RealtimeSessionConfig`. The shared container carries separate native session/bridge resources and traditional provider/pipeline resources. Native prompt construction and protocol remain in `app/realtime`. | Moving these classes solely to reduce imports would be cosmetic at this point. Preserve both engine contracts around future ownership changes. No seamless native lifecycle independence is claimed. |
| A05: unused parallel traditional generation and KB trimming | The executable caller inventory and remaining-function AST comparison established that the removed helpers were unused. The real streaming path is covered instead. Mechanical integration `09bcd50f` and the separately reviewed behavior repair `7f9ec58f` passed 2,750 integrated checks; the later full suite is also green. | The bounded duplication finding is resolved in the local candidate. This does not approve every model's semantics or close AG07 as a whole. |
| A06: transient capture/state and unclear restart contract | Native relationship correction uses typed `LiveConversationState`; current capture records retain typed value/readback/confirmation provenance. The outer `CallSession.captured_slots` field is still `Optional[Any]` and excluded from serialization. The runtime diagram explicitly states that provider objects, tasks and capture state are not a restartable call snapshot. Durable Lead/transcript/action evidence is separate. | Do not describe the entire session as fully typed or resumable. A process failure can lose unsaved transient state. Durable saving and reconciliation acceptance stay with AG05/AG06/OP05. |
| A07: template identity is narrower than assembled behavior | Existing AG01 diagnostics record safe effective request/instruction hashes and explicit knowledge identity, including unversioned knowledge. They do not record arbitrary caller content or pretend a template hash reconstructs the full request. | AG02 retrieval targets and released profile/knowledge acceptance remain open. |
| A08: documentation overstates configuration/layer boundaries | The backend README no longer promises complete provider independence or switching every path through YAML, and removes an unsupported Whisper example and missing provider-guide link. The existing diagram now shows tenant/campaign input, pinned inbound admission and separate native/traditional assembly. | Configuration lookup strictness still differs by entry path. Outbound prewarm and legacy DID resolution are under runtime investigation. Deployed topology/configuration verification belongs to OP12. |

## Evidence and review boundaries

- [Mechanical cleanup](2026-10-05-ag07-mechanical-cleanup.md) and
  [integration](2026-10-05-ag07-integration.md) preserve the A05 inventory,
  independent review and separate behavior fix.
- [Operations integration](2026-10-05-op01-op03-integration.md),
  [pacing holds](2026-10-05-op05-pacing-holds.md), and
  [DNC settlement](2026-10-05-op07-dnc-settlement.md) identify changed ownership
  boundaries and their remaining provider/Redis acceptance.
- [Exact full verification](2026-10-05-exact-integration-verification.md) records
  11,775 passing backend checks, 19 explicit skips and 435 passing migrated
  database checks. These are local software evidence, not actual deployment or
  uninterrupted call recovery.
- [Saved/effective profiles](2026-10-05-ag01-implementation.md) and
  [contact replay](2026-10-05-ag04-contact-and-historical-dnc-controls.md) record
  the scope of request identity and capture provenance. Human semantics and
  hearing remain unqualified.

Independent `audio_audit` source review found the configuration/coupling and
bounded A05 disposition supported. Its correction about Redis lease expiry and
conditional crash cleanup is incorporated above; the report does not promise
indefinite leases. The reviewer also checked the local document links. No tests
or database operations were performed for this documentation-only change.

The README/diagram edits in this disposition are documentation only. Application
behavior, provider choices, migration state, credentials and customer data are
unchanged. A05 is resolved for its bounded local duplication finding in the
reviewed candidate. The other observations retain their owning
repair/acceptance dependencies; AG07 remains in progress.

## Subsequent owning repairs

After the baseline review above, strict known-tenant lookup admission and
[selected outbound lifecycle ownership](2026-10-05-selected-outbound-warmup-lifecycle.md)
were repaired and independently reviewed. The README now describes those
implemented guarantees while preserving unowned legacy/cloud and process-loss
limits. The combined `be44bf72` run passed 1,025 checks with one explicit POSIX
skip. This advances the A02/A03/A08 owning work; their deployed acceptance and
AG07 remain open. A05's bounded local disposition is unchanged.
