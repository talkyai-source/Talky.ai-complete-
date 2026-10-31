# Knowledge routing and evidence-budget follow-up — 6 October 2026

Two reproduced AG02 defects are repaired locally. Neither changes the retrieval
thresholds, gold answers, providers or dependencies. AG02 remains in progress.

## Reproduced causes and changes

Source `3c6ac038` fixes evidence-budget starvation in the shared traditional/tool/
native preparation boundary. Weak passages were consuming space before a later
sufficient passage, then being discarded only after that useful passage could no
longer fit. An oversized heading also stopped all remaining candidates. The
sixteen new controls reproduced **10 failures and 6 passes** against the original
application; these represent variants of two budget defects, not ten separate
product failures. A control reproduces starvation with the default 2,000-character
budget as well as smaller explicit budgets.

The existing maximum-three candidate window is now stably grouped using the
existing sufficient-coverage rule before allocating space. Retrieval order within
each group, thresholds, source passages, qualifications and provenance stay intact.
An individually oversized heading is skipped so a later shorter source can fit.
Unusable or injected strong candidates still permit weak fallback; weak evidence
does not become factual authority. No extra source is admitted outside the window.

Source `fe1ad226`, integrated from `0500c9f2`, separately fixes enrichment response
ownership. A model result could previously write another batch's section metadata,
accept boolean indices, partially mutate a batch before failure, or escape the
single-retry fail-soft handler on malformed JSON shapes. Wrong-section aliases
could then make unrelated authored source appear matched. Whole-response decoding
now validates unique exact integer indices belonging to the requested batch,
field shapes and existing limits before assignment. Both batch and single retry
use the same decoder. The original source remains usable when enrichment fails.
The [owner evidence](../ag02-enrichment-routing/verification.md) preserves its
**36 failures / 12 passes** baseline and **111 passing tests** across seven modules,
including 48 new controls. Those counts overlap the combined run below.

## Verification and source binding

- Budget repair alone: **116 passed, zero failures/skips, 10 warnings**, seven
  affected modules, including the sixteen new controls. CI-rule Ruff and whitespace
  checks passed. Root and an independent source review cleared the change.
- Combined committed source `d3821a0b`: **210 passed, zero failures/skips, 10
  warnings**, twelve affected modules in 2.59 seconds. All 21 recorded source/test/
  fixture hashes stayed unchanged and match the executed commit. The guard recorded
  zero prohibited connection attempts and 223 Windows internal socket pairs.
- The enrichment owner run precedes the budget repair. Its shared-budget dependency
  hash intentionally differs from the integrated source; the combined run verifies
  that integration. Its changed enricher/test files remain identical to the owner
  commit. No historical owner manifest was rewritten.

`commands.json` records the actual invocations and environment; `result.json`
contains the combined before/after hashes and guard counters. The archived runner
was executed as `tmp/knowledge-evidence-integration.py` from `backend`; its root
calculation depends on that original path. Restore it there to reproduce rather
than running the archived artifact path directly. The baseline and 116-test run
used ordinary pytest with existing synthetic boundaries; no global network-counter
claim is made for those two earlier runs. Warning details were suppressed by the
recorded commands, and all ten warnings remain explicit.

## The quality gate still fails

The unchanged pinned 60-question evaluator was run on the combined source and
retained **exit code 1**. Expected-source recall remains **42/45 (93.33%)** and
sufficient evidence remains **32/45 (71.11%)**, below the unchanged 85% target.
`gold-final.json` and `gold-final.txt` preserve all per-case results. The evaluator
uses raw unenriched source, so it does not measure the malformed enrichment repair;
its short source passages also do not reproduce the long-source budget defect.
No score improvement, model-answer fidelity or customer acceptance is claimed.

The earlier actual-PostgreSQL score of 31/45 remains historical; no SQL retrieval
evaluation was rerun here. Representative campaign knowledge and approved expected
answers remain pending. Rejected stemming experiments, unsupported-question
admissions and existing semantic failures remain visible. No live model, database,
telephone, browser, deployment or push occurred. No package or release gate is
promoted and deferred packages remain untouched.
