# AG02 knowledge correctness implementation

Local source candidate: `69d09fe575831c42a14b28c0a7417ca1b388c492`, based on `717fb14faf70f365e023f4fd630cadb1e86775b8`, on `codex/production-ready-20261004`. No push, deployment or external provider calls. AG02 remains **in progress**: deterministic repairs passed, but the synthetic retrieval quality gate failed and human/provider acceptance remains unfinished. AG03 is the next implementation package.

## What caused the observed risks

1. Traditional no-match, weak-match and inline instructions contradicted the central facts rule by promising to check or follow up without a supported route. They now state inability to confirm and allow only an available next step. Weak results do not authorize factual confirmation. Existing action authorization/receipt guards still apply.
2. An already-scoped call could receive inline or pinned knowledge from a mismatched campaign/tenant. Setup now rejects that mismatch before attaching facts or loading the tree. Live SQL and snapshot loading also require matching tenant, campaign, source and ready publication status.
3. The traditional worker's process-local cache could retain facts after an edit or disable in another process. Live-mode turns now read current storage under the existing timeout. Admission-pinned calls deliberately retain their captured revision. This removes a cache benefit; the resulting database/latency effect requires OP08 measurement, not an unmeasured performance claim.
4. Editing source text left generated answers, keywords and example questions describing its previous revision. HTTP and authorized assistant edits now share one write contract, invalidate derived text, rebuild the index and advance the existing source version atomically. Row locks preserve the committed combination during concurrent heading/content edits.
5. Missing or malformed coverage could be treated as trusted. One strong match could promote unrelated weak passages into financial grounding. Coverage must now be finite and within zero to one; unknown coverage remains weak, and matched evidence contains only sufficiently covered authored source passages.
6. Generated summaries/voice answers could become facts when source content was absent or disagree with current source. Factual rendering now uses authored source only. Generated keywords/example questions may still assist retrieval; they are not proof of an answer. Topic summaries are labeled as navigation.
7. Truncation could separate a price from its condition or silently omit the rest of an inline document. Related sentence qualifiers stay together; an oversized indivisible passage is withheld. Incomplete full inline trees fall back to retrieval. Poisoned inline trees also fall back rather than deleting a line that might contain an exclusion.
8. Realtime guessed tool success from returned wording and accumulated facts across later revisions. It now returns explicit matched, weak, missing, unavailable and superseded status with source references. Each lookup replaces factual authorization, and delayed results cannot restore earlier prices, including while waiting for speech playback.
9. Provenance hashes could themselves be damaged by the PII logger. Exact 64-hex values under specific profile keys now survive, while phone/email-shaped values remain redacted. Knowledge logs retain counts/status/digests instead of query, heading or source content. The per-turn version digest includes source revision as well as node revision.

Traditional and native prompts remain separate. Native instructions advanced to `realtime@6`; traditional changes are dynamic knowledge blocks outside the governed persona templates. Existing persona version tests pass, and actual streamer tests verify that the logged full instruction digest matches the submitted prompt.

## Verification

- **791 backend tests passed**, zero skipped, across the modules listed in [the final command](artifacts/ag02/backend-final-command.json). [Output](artifacts/ag02/backend-final.txt). This combined run includes knowledge, native providers, actions, current-turn end scope, prompt identity and privacy regression coverage; earlier agent counts overlap and must not be added to it.
- **9 separate PostgreSQL integration tests passed**, using the actual knowledge migration in isolated schemas, synthetic data and a role without RLS bypass. [Output](artifacts/ag02/ingestion-retrieval-postgres.txt) and [setup, commands and limits](artifacts/ag02/ingestion-retrieval-notes.md). This is focused migrated-schema coverage, not a full production database migration rehearsal. The disposable server was stopped afterward.
- CI-rule Ruff passed for all changed Python files; staged diff checks passed. [Lint](artifacts/ag02/lint.txt). CI now includes the new PostgreSQL boundary tests, but remote CI was not run.
- Before-fix reproductions covered contradictory fallback/identity boundaries, stale cache, source edits/tenant SQL, malformed or mixed evidence, lost qualifiers, native stale results and hash redaction. [Root evidence](artifacts/ag02/root-verification.json), [shared evidence](artifacts/ag02/evidence-boundary.json), [native evidence](artifacts/ag02/native-verification.json).
- The initial combined run had one stale test expecting generated text to substitute for absent source; it was updated to the explicitly changed source-only contract. The initial output is retained in `backend-regression.txt`; the final combined run passed.
- [Source integrity](artifacts/ag02/source-integrity.json) binds all 36 changed source/test/CI files to the candidate, using CRLF-to-LF normalized SHA256. [Verification manifest](artifacts/ag02/verification.json) binds the artifacts.

No frontend sources changed in this package. The earlier broad frontend failure and lint configuration issue remain as documented under AG01. No human heard test audio, and no model generated the synthetic speech used by deterministic admission tests.

## The quality gate is not passing

The [60-question corpus](artifacts/ag02/gold-corpus.json) describes a fictional UK merchant payment service. It includes current and disabled old revisions, prices and conditions, exclusions, product conflicts, short follow-ups, unanswerable questions and embedded instruction attacks. It is proposed synthetic gold, **not approved customer knowledge**.

Targets were published before the first run: at least **90% expected-source recall** and **85% sufficient matched passages** for answerable questions. A nonzero requirement prevents universal abstention from passing. One initial annotation incorrectly called a question answerable when the source did not address it; that label was corrected transparently, retaining the initial results. There are now 45 answerable cases.

| Evaluated path | Expected source in top 3 | Sufficient matched passage | Target outcome |
| --- | --- | --- | --- |
| Pinned production retriever | 42/45, 93.33% | 32/45, 71.11% | Failed |
| Actual PostgreSQL retriever | 40/45, 88.89% | 31/45, 68.89% | Failed |

[Pinned results](artifacts/ag02/gold-pinned-current.json) and [PostgreSQL results](artifacts/ag02/gold-postgres.json) retain per-case query, expected source/version/facts, selected evidence and coverage. Both paths use the same shared evidence boundary. These metrics describe source retrieval and evidence availability, **not model answer fidelity**. No unsupported-claim success score is awarded without actual output and human review.

This corpus intentionally exercises raw source with optional enrichment unavailable. Common failures involve unseen synonyms and paraphrases, rather than dropped returned text. Successful enrichment with synonyms/example questions may perform differently; it needs a separately versioned and approved evaluation. Failed-question aliases were not inserted and thresholds were not lowered. The standalone evaluator writes the full report and exits **1** on the failed gate.

## Remaining acceptance and operating limits

- Improve or qualify the actual intended campaign corpus until the unchanged retrieval targets pass; obtain content-owner review of expected facts and acceptable abstentions. This local candidate does not qualify that campaign for sale.
- Run the selected real model/voice profiles and controlled telephone cohort under AG04/OP08. Retain raw output and submitted speech under the agreed data policy; evaluate qualitative claims, paraphrase comprehension and product identity. Numeric/link guards are not universal truth verification or complete injection prevention.
- Uploads are additive sources. Matching filenames do not establish replacement lineage. Two enabled, ready documents with conflicting facts require explicit content-owner resolution; this change does not infer which one wins. Existing source versions and snapshots do not provide a full immutable archive of every historical node edit.
- Admission snapshots intentionally preserve the admitted revision throughout the call. Live retrieval observes the revision selected for each turn; a source change after retrieval does not retroactively rewrite already submitted speech. Outbound inline facts are captured at setup. These policies must be understood in campaign acceptance.
- Latest-result native grounding is conservative: a fact from an earlier lookup must be retrieved again before a later response may quote it. Measure the resulting conversation behavior with the actual provider.
- No final release gate is marked passed, no historical Leads are rewritten and the feature freeze remains active. CP05, CP06 and CP09 remain deferred at the user's request.

## Rollback

There is no schema migration in this candidate. Preserve source revisions, snapshots, old call/lead evidence and action receipts. If retrieval behavior regresses, revert or forward-fix ranking/selection separately while retaining honest uncertainty, tenant/source checks and receipt guards. Do not restore unsupported promises or stale evidence and label the profile approved. Coordinate release and drain incompatible active sessions through OP12.
