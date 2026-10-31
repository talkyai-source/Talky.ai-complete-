# Model-driven contact recording and native conversation simplification

Scope: the user's request to let the conversation model understand contacts and wording, while keeping backend source, syntax, currentness, and save checks. Source commits: `f9a725cc` (six application paths and the new focused test) and `4dd3c252` (native inferred-state removal and two additional controls), based on `0270d449`. Root reviewed both commits before authorization. This owner did not change the native knowledge lookup or the root-owned traditional streamer and prompt wiring.

## Behavior

Both voice engines can call the same `record_contact` boundary. It accepts email/phone, `set`, `add`, `confirm`, or `withdraw`, a candidate, the expected current value, and an exact excerpt of the current caller turn. The model interprets natural spelling, ownership, correction, and agreement. The backend validates syntax, exact caller attribution, current turn identity, and candidate consistency. An exact quote establishes attribution, not whether the model understood it correctly.

Complete candidates are saved pending; incomplete or invalid values retain the caller quote with `needs_clarification` and no usable value. Confirmation requires the existing candidate and a later caller turn, without prescribed wording or a regex readback. Its evidence is explicitly `model_interpreted_caller_confirmation`, not invented playback/hearing proof. Adding another contact preserves the prior confirmed value. Withdrawal and same-item transcript revisions clear the contribution they invalidate. A revised confirmation removes the native published confirmed contact immediately; an empty final cannot authorize a tool. Invalid, conflicting, unavailable, and stale results carry the currently selected contact status/value/quote when its kind is known.

Existing lead persistence still owns tenant/call binding, test exclusion, validation, revision checks, and SQL construction. `saved=true` requires acknowledgement of the exact selected field fingerprint; another saved field or an in-memory update cannot supply it. Failed/conflicting writes remain unsuccessful. No new table or provider is introduced.

Traditional turns no longer run regex contact extraction, readback classification, a separate confirmation LLM, or contact-specific STT mode switching. Native turns no longer run that workflow or reject normal prose using price/link/relationship/action-completion regex checks. Runtime action executors, effect receipts, opt-out handling, privacy processing, caller/source ordering, transcript revisions, cancellation and transport ownership remain. Normal language fidelity now depends on the model and representative evaluation; this is not a guarantee that speech is always correct.

Caller-first bare greetings reach the ordinary model once rather than a separate presynthesized opener. Configured agent-first greetings/recording disclosure and other optional prewarm/opener-ladder machinery remain outside this bounded removal.

The native follow-up also removes transcript regex classification into relationship/provider/interest/sales facts. Current, late historical, and revised caller words remain transcript context. Existing identity/contact/tool evidence remains separate. Root's corresponding shared-state rendering/reducer change is separately owned and is not exercised by this owner's run.

## Existing lead scope

Live `lead_slot_capture.SLOT_FIELDS` already contained only email and phone before this change. No live name/company field was silently removed. Existing imported contact identity and post-call model summary/business-note processing remain separate; the latter is not proof of structured spoken-name/company collection or a scheduled follow-up. Adding more fields is not part of this change.

## Verification and preserved failures

All runs used the existing repository Python venv, test environment and a dummy unavailable database URL. Synthetic SQL/model/media ports replace effects. Real contact state, validators, persistence SQL construction, transcript source binding, provider event parsing, and native bridge methods execute. There was no actual SQL, provider, telephony, browser, or customer acceptance execution, and no exact dependency-lock qualification.

| Run | Result | Source binding | Network guard |
| --- | --- | --- | --- |
| First draft | 29 passed, 2 failed, 17 warnings; 5.98s | 22 hashes unchanged; seven owned input snapshots retained | 63 internal socketpairs; 0 prohibited sockets/transports |
| Corrected draft | 31 passed, 18 warnings; 2.77s | 22 hashes unchanged | 63 internal socketpairs; 0 prohibited sockets/transports |
| Contact final | 32 passed, 20 warnings; 2.48s | 22 hashes unchanged; matches `f9a725cc` owned source | 65 internal socketpairs; 0 prohibited sockets/transports |
| Native follow-up final | 49 passed, 27 warnings; 3.53s | 25 hashes unchanged; matches `4dd3c252` current inputs | 97 internal socketpairs; 0 prohibited sockets/transports |

Counts are not additive. The final 49 includes all 34 contact controls and 15 selected existing native provider/revision/opt-out/action-ownership controls. They cover syntax, visible partial candidates, correction, withdrawal, additional contacts, later confirmation, source quotes, stale turns, serialized updates, revision during a paused write, failed/conflicting/test writes, selected-field acknowledgement, real traditional source binding, both native event parsers, published contact retraction, caller-first opening, and source ownership without inferred facts. Existing revision controls verify that corrected caller input invalidates pending external-action authorization before the synthetic effect.

The first run is an implementation draft run, not a pre-change production baseline. Its native timeout coincided with an invalid extra live-state suffix and an unbound synthetic call fixture. The suffix was removed and the fixture supplied its explicit unavailable binding. The timeout's exact latency cause was not separately profiled. Its traditional fixture had invented a source row instead of using the actual `TranscriptService.bind_caller_turn`; the corrected control now exercises that binding. Initial logs and source snapshots are preserved; no failure is relabelled as a production incident. The intermediate 31-case snapshot is represented by recorded hashes, not a complete separately committed source tree.

Scoped repository Ruff F checks with existing F401/F841 exclusions and `git diff --check` passed after each final source state. Git emitted only normal CRLF normalization notices. The report does not claim a broad lint or complete backend suite pass.

## Remaining qualification

Old tests asserting removed private regex helpers, forced readbacks, caller-first instant audio, or native semantic blocking need contract migration in the integrated suite. Examples include the email/phone confirmation, contact best-practice, email-LLM-first, call-1436672a regression, phantom-goodbye helper import, native relationship guard, and instant-opener modules. Their unrelated transport/action contracts must be retained; hiding or skipping failures would not prove integration. Parent integration owns the revised model/tool prompt and shared runtime-state contracts.

No quality gate, approved profile, customer acceptance, release state, or production-readiness freeze is changed by these local checks. Actual contact interpretation accuracy, model tool use, speech quality and latency still require representative model/call evaluation.

Exact execution argv, safe environment values, Python/package versions, and before/after hashes are in each result JSON. `commands.json` records the shell invocations; `manifest.json` binds the final 25 inputs and all package/report LF hashes. Historical logs must not be overwritten by rerunning the same artifact paths.
