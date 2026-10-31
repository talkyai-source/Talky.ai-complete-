# AG04 offline contact and historical DNC controls

Source candidate: `7bc0023e68419a27d7c932ab1f4eff2df40057b5`, based on the separate native DNC receipt-fixture candidate `88d1bbfaf37541ed33f6a2129535c518b7b9f72a`. No application, prompt, evaluator, threshold or canonical matrix changes were made. MAIN was not modified while its final regression ran.

The existing common runners now exercise two additional proposed canonical conditions. These are authored runtime controls, not evidence that a model independently generated the correct conversation or that a person heard it.

## Exact contact confirmation

Both real native event parsers feed the existing bridge the same caller statement, `My email is alex at example dot com.`, the same response, `Your email is alex@example.com, correct?`, and the same bare caller reply, `Yes.`. The wording reuses the previously verified AG05 integration fixture. Synthetic sockets and gateway receipts replace the external boundaries.

| Receipt or interruption | Actual capture result |
| --- | --- |
| Matching completed `transport_played` receipt | Exact address confirmed; source and confirmation item/order/digest plus matching readback utterance retained |
| Completed receipt belonging to an expired utterance | Address remains awaiting confirmation |
| Unknown receipt | Address remains awaiting confirmation |
| Transmission-only evidence, zero played milliseconds | Address remains awaiting confirmation |
| Actual `speech_started` while the first submission is blocked | Output cleared/truncated, interrupted transcript evidence retained, no completed receipt, address remains awaiting confirmation |

The report now serializes the actual bounded capture dataclasses: value source, confirmation source, status source and readback. It does not fabricate these fields from fixture expectations. Tests compare source and confirmation hashes to the exact synthetic caller text and correlate readback to the submitted utterance. The gateway rejects unsupported fixture receipt labels instead of treating any label as successful playback.

Only native replay is newly mapped to `ag04.exact_contact_confirmation`. The traditional runner's fake TTS does not provide a correlated playback receipt, so it cannot establish this condition. Synthetic native acknowledgement also does not prove hearing, acoustic recognition, durable Lead persistence or delivery. Broader phrasing remains unqualified: an exploratory `Is your email ...?` question without the existing recognizer's explicit confirmation cue conservatively remained pending; no parser change was made.

## Historical DNC recollection

The caller says, `Last year I told another company to stop calling me. What are your opening hours?` This distinguishes historical recollection from the existing quoted and negated cases.

The native parser/bridge denies a generated `end_call` tool request through actual caller-intent admission. The traditional pipeline ignores the generated end sentinel. Both retain the continued conversation, leave the DNC flag false, and make zero calls to the synthetic DNC persistence port. The ordinary response avoids inventing opening hours without source facts. All six configured replay adapters exercise this condition; none is thereby semantically approved.

## Verification

- Before adding the declared fixtures, 12 new checks failed because those fixtures did not exist. This is evidence of the corpus gap, not a production runtime failure.
- Final focused native/traditional/evaluator tests: **97 passed, zero skips**, with 1,049 existing deprecation warnings. Scoped Ruff `F` and `git diff --check` passed.
- The existing CLI ran against the committed source with no uncommitted backend changes: **190 declared and observed rows, 1,296 runtime checks, zero control failures, zero evidence errors, zero blocked network attempts**.
- The CLI correctly exited **1**: the original captured Groq semantic failure remains failed, with 189 other semantic findings unreviewed. Live profiles, human rubric, telephone/audio hearing and latency remain untested; `production_approved` is false.
- The union covers **39 of 50 proposed canonical conditions**, up from 37. The 190 rows, 64 engine-specific fixture IDs, provider repeats and five receipt variants are not counts of human scenarios or complete per-profile coverage. Per-profile gaps remain in the report.
- Marker-aware installed-version checks satisfy all 62 active directly declared production requirements using the exact dependency overlay before the existing test-only Lua target. This is not a fresh transitive dependency resolution. The shared original virtual environment was unchanged.
- Independent read-only review by `audio_audit` found no material defect in the five-file extension; the reviewer did not rerun the tests.

Exact commands, environment and caveats are in [verification.json](artifacts/ag04-contact-controls/verification.json). [replay-summary.json](artifacts/ag04-contact-controls/replay-summary.json) records the gates, per-profile coverage and archive hashes. [replay.json.gz](artifacts/ag04-contact-controls/replay.json.gz) preserves the complete CLI JSON byte-for-byte after decompression, including raw/submitted text, prompts, history, requests and the original failed Groq observation. [new-case-observations.json](artifacts/ag04-contact-controls/new-case-observations.json) extracts the 16 new rows without changing their observations.

The remaining 11 unmapped canonical conditions and live/human/account approvals remain outstanding. In particular, action-unknown-outcome evidence is deferred until the existing actual executor port can be exercised meaningfully; a canned result is not a substitute for its receipt/idempotency behavior.

## Root integration follow-up

Source was integrated as `1c7dce70`. The same three focused modules passed on
the combined root candidate `e4f76f87`: **97 passed, zero skips**, 1,049 warnings,
8.00 seconds. The source snapshot remained unchanged. See
[integrated command](artifacts/ag04-contact-controls/integrated-command.json) and
[output](artifacts/ag04-contact-controls/integrated-tests.txt). This confirms the
test-only extension with the later DNC/authentication repairs; it does not change
the preserved semantic failure or any live/human gate.
