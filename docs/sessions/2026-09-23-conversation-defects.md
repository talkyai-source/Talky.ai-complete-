# 2026-09-23 — The agent spoke for the caller, and what else a whole-corpus read found

Owner report: a Test-agent transcript in which the agent answered its own
questions, invented an email address, confirmed it, and hung up. Then: analyse
every conversation, plan the fixes, premortem the plan, fix everything broken.

Production went from `edee6fe8` to **`3aa7cc7e`** in five deploys. No migration.
Canonical suite **9,181 → 9,358 passed / 8 skipped / 0 failed** on the final
tree (every one of the 177 new tests reconciled by test ID). Ruff clean on `app/`.

## How the work was found

A six-lens read of 56 calls / 605 turns from the last 30 days, each finding
adversarially re-checked against the raw transcripts: 74 findings, 68 confirmed,
5 refuted (one "the agent hung up" was the caller saying "get off my number";
another was wrong by 3x). Counts below are the verified ones.

Two methodological rules came out of the day and are worth keeping:

* **Measure the healthy population, not just the defect.** The splitter fix was
  gated on replaying all 306 real agent turns and requiring that *no healthy
  turn changed*. Eight changed; all eight were the defect.
* **Count what the database says it did.** Three times a check passed while the
  thing it checked was broken (below). Every one was a test of the mechanism I
  had in mind, not of the outcome.

## What shipped

| Commit | Fix | Evidence |
|---|---|---|
| `9e7f9c65` | Agent spoke the caller's lines and hung up on them | Both splitters required whitespace after a terminator; the model writes turn boundaries without one. 8 of 306 real turns carried the signature, all 8 the defect, 0 false positives. The turn is now cut there; the end-call in that completion is ignored; history stores what was spoken |
| `765b7ccd` `b05c50ab` | Summaries rejected by their own schema | Prompt asked for `action_items`/`objections` as objects, schema said strings. An existing test was *asserting the bug* and was rewritten |
| `357b9f1a` | Re-enrich knowledge in place | `dojo` 0 → 39/43, `Estimation new` 0 → 20/27; `search_text` rebuilt, so fuzzy matching recovers too |
| `ccbce0da` | Summaries lost on long calls | Flat 1,500 output tokens; a 243 s call ran out mid-document. Now sized to the call, one retry at 8,000 |
| `941a5cd8` | Agent invented a dot in an email | "john co at g mail dot com": asks *johnco@ or john.co@* instead of guessing; "g mail"/"hot mail" rejoined; "my email is bob at…" no longer INVALID |
| `0943f3e1` | Agent invented a URL | `/sample-reports` appeared nowhere in the knowledge. Every spoken address is now checked against what the model was given |
| `62a615a0` | Email before answering; re-asked after three objections | Objections to *being asked* tracked; answer-first stated; "not interested in sharing my number" no longer counts as declining the call |
| `5fc31343` | Opening "Hello?" landed on the caller's own hello | Silence clock started before the STT socket opened; now counts from first caller audio |
| `bcb3222c` | Test agent resolved its opener from different inputs than a live call | Passed the wrapper instead of `call_session`; every `company_name_fallback` in 30 days came from here. Not audible today |
| `ce2cd223` | One campaign's offer sent to every tenant on every turn | Per-turn block said "steer toward their email for a sample". A/B on real lines, production model: email-instead-of-answer **7/8 → 1/8** |
| `90f76f79` `3aa7cc7e` | Agent asked a question and hung up | `[[END_CALL]]` honoured unconditionally. Now held when the agent's last sentence is a question or a contact capture is open |
| `8f7096fb` | 4,436 suspicious-session warnings a week | Sticky flag re-logged every request. Now once per session. Nobody was logged out |
| `d54c151e` | RLS bypass lost after first pooled release | Two sibling scripts had the trap; a repo-wide guard now fails on it |

## Corrections to things I said during the day

* "Call summaries will persist again" — too strong. The schema fix worked; a
  second, independent cause (token budget) then surfaced on the next call.
* "Nothing was captured" on the Test agent was a defect — it was not. Test
  calls skip lead capture by design.
* "`dojo` is live" — it was `stopped`; `Estimation new` is a `draft`.
* "The Test agent calls itself 'our office'" — the spoken opener is a bare hello
  and never named the company. The fix still matters; it was not audible.
* The sentence-cap fix was "a small change in one file" — it was two files
  sharing one wrong assumption, plus a buffer bug my own fix would have caused.
* "20 tests unaccounted for" — I had compared a run from one moment with a
  collection from a later one.

## Checks that passed while the thing was broken

1. The re-enrichment script's first production run reported **34 writes and
   made none**. asyncpg's `RESET ALL` on release wiped the RLS bypass set in
   `init=`. Caught by the script's own read-back guard; data verified untouched.
2. My test asserted the bypass was set in `init=` — true, and still broken.
3. `test_schema_types_match_the_python_defaults` asserted the exact rule that
   made production discard summaries.

## Not verified on live traffic

Between the first deploy and this report **one call** reached production, and
it ran on the earlier code. Every fix above is verified as *loaded* (each deploy
exercises the new code with the failing calls' own lines) — none is yet proven
on a real call. Log lines to watch: `model_wrote_caller_turn`,
`ungrounded_link_rewritten`, `end_call_stripped_question_open`,
`end_call_stripped_wrong_person`, and the absence of `json_validate_failed`.

## Still open

**Your decision, not engineering:**
* `inbound_recording_enabled = false` — no inbound call has ever been recorded.
* `inbound_transfer_enabled = false` — why no caller asking for a human got one.
* `goal_achieved` is never set by the call path, yet dashboards report it — so
  every conversion rate reads zero. Needs a definition of "goal" per campaign
  type, or the metric removed.

**Engineering, not done:**
* Hangups after a *statement* while the caller is engaged and no capture is
  open (`e3427ee2`) — needs the caller's intent.
* The v2 device fingerprint still varies between requests from one browser;
  finding which signal needs per-signal comparison that is not logged.
* Audio gaps — 15 s hole and 16% frame loss on a real call on 22 Sep. Needs
  packet-level diagnosis at the gateway.
* `ARI GET /applications/talky_ai → 404` (15 times).
* Inbound contact capture has never existed; inbound callers returning a missed
  call get an invented reason; no answering-machine detection on inbound.
* "Misheard, ask again" (in the craft block) did not measurably change the one
  case tested. Unproven.
* Persona base still ~14k characters per turn — the largest latency lever left.
