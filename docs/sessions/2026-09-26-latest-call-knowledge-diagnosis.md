# Latest Dojo call: knowledge retrieval diagnosis

Investigated 26 September 2026 PKT using production journal, tenant-scoped read-only database queries, deployed source, and read-only retrieval replays.

- Campaign: `b7260bfb-0e41-4b5e-a7a9-f0e593816320` (Dojo).
- Latest stored campaign call: `4160f0b9-715c-4a4d-8aa2-d47449ef34b8`.
- Voice session: `266edaed-3d80-48a0-9699-4c34602fd01b`.
- Created 26 September 00:12:56 PKT; ended 00:18:20 PKT. Stored billed/conversation duration field: 318 seconds (5m18s); this differs from the full setup-to-end interval.
- Deployed revision: `cad3f7d1c5fc64e4de07aa5b382c2f4821704ea1`; production checkout was clean.

## Finding

Retrieval was enabled and executing, but the selected context was often irrelevant or incomplete. Thirty logged lookups returned hits in 18–90 ms (mean 38 ms). There were no KB timeout, no-hit, error, or injection-drop events in the captured call window.

### Reproduced wrong ranking

The deployed inject path joins the latest caller utterance and previous caller utterance into one search query. PostgreSQL ranks whole conversational wording using full-text matching and trigram similarity; only three results reach the answer builder.

| Question | Actual top three | Observed answer |
| --- | --- | --- |
| Dojo rewards card | Overview; Known Gaps; AI Retrieval Examples | Agent said it did not have current rewards-card details. |
| Monthly card-machine line rental | Overview; Known Gaps; Dojo Bookings | Agent said it did not know the exact line-rental fee. |

A read-only replay using the stored transcript and deployed retrieval function reproduced both top-three lists exactly. The dedicated Rewards / Business Card Proposition section ranked sixth for the actual combined rewards query. Using only that full utterance ranked it seventh; simply removing the previous turn is insufficient. A focused `Dojo rewards business card` query brought it into second place. A focused `Dojo Go Max monthly rental` query returned Go Max and its backbook rules first and second; the specific hardware model would still need clarification in the real conversation.

### One source was not split into product sections

The customer-safe v2.2 source is one enabled Overview node containing 11,361 characters. Its headings use `#Purpose`, `#1. ...`, and `##Alex may say` without whitespace after the hashes. The Markdown parser requires whitespace. Parsing the stored content reproduces one node; adding heading whitespace in memory produces 39 nodes. No stored source was changed.

The deployed renderer budgets about 600 characters per result and preserves its short voice answer. For this Overview, that delivers introductory guidance and “refer to Azian if unsure,” rather than the later product sections. The read-only replay confirmed the resulting bodies. The second source contains 43 enabled nodes, 15 without voice answers; this count includes nodes that may have no body and is not proof of 15 enrichment failures.

### Tool-mode setting does not apply to the selected provider

The environment file specifies `VOICE_KB_MODE=tool`, but this call used Cerebras `gpt-oss-120b`. Deployed `knowledge_tools_for` supports Groq and Gemini only, so Cerebras falls back to automatic injection. The journal confirms `KB_DEBUG` injection events rather than `KB_TOOL` lookups. Changing the environment flag alone will not give Cerebras model-authored focused searches.

### Some requested facts are explicitly unverified in the sources

The stored rewards section marks cashback, credit limits, and interest-free terms as user-supplied, historical, conflicting, or requiring verification. The newer customer-safe source explicitly lacks approved current rewards terms. Go Max rental figures are described as launch-specific and requiring a current quote. Therefore retrieval repair can improve recognition and useful explanations, but these records do not authorize unconditional current commercial quotes.

## Other observed call issues

The transcript shows repeated phone-number corrections, a missing final digit in the first readback, and a later readback that appended an unrelated fragment. It also records WhatsApp as “By what, sir?” and a later correction as “There are two files in the end.” These are separate recognition/dialogue issues; audio was not listened to in this investigation, so the transcript alone does not establish exactly what was spoken or heard.

## Recommended correction

1. Normalize/validate heading formatting and re-ingest the oversized source into topic sections.
2. Improve query focusing and topic/heading ranking; use prior-turn context selectively. Prevent broad overview/gap/example sections from displacing direct product answers.
3. Preserve the matched passage when trimming long nodes, not only their introductions and generic summaries.
4. Support focused knowledge tool calls for Cerebras if tool mode is intended for that provider.
5. Supply approved current commercial facts and resolve source-version conflicts before allowing exact current quotes.

Verify the changes against these exact stored questions and related follow-ups before a new call. No application code, production knowledge records, configuration, or services were changed or restarted. No call was placed.

## Evidence

- `tmp/latest-call-266edaed.log`: scoped production journal capture.
- `tmp/latest-call-kb-replay.txt`: read-only retrieval replays and reconstructed result bodies.
- `tmp/latest-call-kb-diagnostic.txt`: stored transcript and scoped knowledge inspection; contains call data, keep local.
- Deployed source: `voice_pipeline/turn_streamer.py:188` (query concatenation), `voice_pipeline/knowledge_tool.py:156` (provider gate), `knowledge/retrieval.py:203` (ranking), `voice_pipeline/kb_budget.py:18` and `:26` (three-result/600-character budgets), `knowledge/md_tree.py:19` (heading syntax).

## Follow-up investigation: missing client details and repeated mistakes

The same call was investigated further on the user's request. These findings distinguish saved conversation evidence from operational contact fields and executed actions.

### What was actually saved

| Store | Verified result |
| --- | --- |
| Audio | Journal reports a local MP3 saved, 317.6 seconds, 2,541,357 bytes. Audio was not listened to in this investigation. |
| Transcript | Persisted at hangup; log reports 51 turns and 703 words. |
| Call summary | Includes Worldpay, requested pricing information, Thursday 4 PM WhatsApp follow-up, and a referral. |
| Structured call lead details | Exactly one row: `follow_up = thursday`, source `caller_stated`, confirmed false. |
| Contact record | `best_time_to_call`, `preferred_contact_method`, and `calling_notes` are null; custom fields are empty. |
| Follow-up execution | `action_plan_id` is null, `action_results` is empty, no `assistant_actions` for this call, and no reminders for the linked lead. |

The call was not excluded as a browser test: `calls.is_test=false`. The live writer successfully persisted the Thursday row at 19:15:02 UTC, so this was not a general database-write outage.

### Confirmed phone-capture trigger failure

The agent asked, “just the name and best way to reach him?” The caller answered, “His name is mister Smith, and his number is zero double one double two double three double four double five.”

The deployed agent-request recognizer does not recognize “best way to reach him” as opening phone capture. The caller-intent recognizer accepts phrases including “phone number” and “my number”, but not “his number”. Neither side activates the phone state machine.

A read-only replay of the persisted conversation through the deployed `CallState` user/agent update functions left `active_contact_kind`, `phone_capture`, and `phone` empty throughout the number exchange. Its final persistence snapshot contained only Thursday, matching the database. The standalone speech-number extractor correctly returned all eleven digits for the original utterance; its phone-intent gate returned false. The replay supplied a GB region to remove missing-country context as a confounder. It is a deterministic transcript replay, not a recovered live in-memory session.

Consequences:

- The initial transcript contains the full intended digit sequence, while the agent's first readback drops the final repeated digit. This is an answer-generation error on available transcript evidence, not evidence that speech recognition omitted that digit.
- The agent says “Thanks—got it. I'll pass that on” after a partial correction, but there is no confirmed phone state or saved contact row.
- Later it appends the unrelated “three eight one” fragment to its earlier incomplete readback. No tracked canonical number is available to constrain that response.
- Later corrections such as “Four four five five” and “Five five. Mate, it's five five” also do not open capture. The journal has no `phone_confirm` events in this call window.
- Contact persistence correctly refuses unconfirmed values, but pending/unrecognized details have no structured review row. They remain recoverable only from the transcript/summary.

A fix must track the referred friend separately from the original caller. Merely adding “his number” to a generic caller-phone regex risks overwriting the original client's contact details. Entering capture also does not establish number validity or confirmation; those checks must still occur.

### Fixed fields do not implement the requested business handoff

The deployed live writer's `SLOT_FIELDS` handles only email, phone, follow_up, project_type, and bidding_active. It has no fields for referral name/contact, current payment provider, preferred channel, detailed need, or next owner/action.

The day extractor matches “Thursday” and saves only that day token. It does not parse “four PM”, resolve a date/timezone, or update a previously captured day after a correction. Thus the live capture path loses the time even though the transcript and later summary preserve it.

Campaign-visible field definitions include `best_time_to_call` and `calling_notes`; configuring those fields does not create an extraction/writer implementation for them. The saved live key is `follow_up`, not `best_time_to_call`. Similarly, natural language campaign instructions asking for a handoff do not create referral fields or execute a WhatsApp workflow. The inspected additional instructions already say not to claim a handoff succeeded without system confirmation.

### Contradictory summary and stale contact notes

The saved summary says:

- outcome: `no_interest`;
- qualification: `unqualified`;
- timeline: `unknown`;
- identified need: pricing and service details;
- commitments: WhatsApp Thursday 4 PM and receiving pricing information;
- next step: forward the referral and arrange WhatsApp contact;
- what happened: no booking or send action was executed.

Those fields contradict each other. A requested follow-up does not prove a sale is qualified, but labeling it “no interest” and its timing “unknown” discards explicit evidence. The summarizer receives a generic analyst prompt, transcript, confirmed contacts, and executed actions; it does not receive campaign-specific qualification rules. Its coercion validates output shape, not agreement between outcome, timing, and commitments.

Replaying the actual summary through the deployed qualification predicates returned `outcome_accepted=false` and `summary_supports_lead=false` (`qualification_status=unqualified`). The summary therefore cannot refresh the lead via this path.

There is a second independent update restriction: `mark_lead_from_summary` updates only rows where `leads.is_lead=false`. This contact was already marked as a lead on 22 September and still points to that older qualifying call. Its saved follow-up note concerns a different earlier email/callback discussion. Even a corrected qualified/callback summary would not refresh that note through this update while `is_lead=true`.

### Other call-quality evidence and limits

- The transcript records “By what, sir?” where the later caller correction indicates WhatsApp was intended, and “There are two files in the end” during repeated digit correction. Audio review would be needed to attribute these exactly.
- At 19:17:25 UTC the primary Cerebras response timed out and failed over to Groq. The next completed response reintroduced the AI identity instead of following the requested handoff. The timing is observed; model failover alone is not proven to be the sole reason for that response.
- Several turns were interrupted, but deployed cancellation handling preserves the caller's message. The earlier hypothesis that every interrupted turn deletes caller context is not supported by the deployed code and is not used as the diagnosis here.

### Remediation priorities

1. Implement a single structured handoff state for the original caller and separately identified referrals; save field-level evidence and review status without claiming unconfirmed contacts are usable.
2. Recognize contact requests semantically or through broader tested contextual rules, and make readbacks use the captured canonical value. Handle correction fragments explicitly and bound clarification attempts.
3. Capture day, time, timezone, preferred channel, need, current provider, and owner/action; map them to the actual campaign/contact fields.
4. Make “saved”, “scheduled”, and “sent” responses depend on successful persistence/action results. No action was executed in this call.
5. Check summary consistency against explicit follow-up evidence and refresh current follow-up notes independently of first-time lead qualification.
6. Replay this exact call as a regression case, including the referral boundary, all number corrections, Thursday 4 PM, WhatsApp, and an already-qualified contact. Separately fix the knowledge ranking/chunking described above.

Further evidence: `tmp/latest-call-capture-diagnostic.txt` and `tmp/latest-call-capture-replay.txt` (local call data; do not publish). No production code, client records, summaries, or actions were changed. The investigation did not send a message or place a call.
