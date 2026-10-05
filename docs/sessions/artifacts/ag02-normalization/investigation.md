# AG02 lexical normalization investigation — rejected proposals

Date: 2026-10-05. Source baseline: `33360ddf42c1089e78f9ee9918ba0d770f4347c7`.
Worktree: `tmp/production-ready-ag02-normalization-20261005`.

**Disposition: no application integration and no dependency addition.** Both
prototypes expand sufficient-evidence admission to an unrelated source. The
paired proposal improves the unchanged synthetic gold score from 32/45 to
34/45, but still fails the predeclared 85% sufficient-passage target. The
90% expected-source recall target and all other acceptance criteria remain
unchanged. No model, human, acoustic or production qualification is awarded.

## Observed root causes

The production pinned retriever uses exact raw word overlap for coverage,
while its fuzzy score can return a candidate whose coverage is zero
([retrieval.py](../../../../backend/app/services/scripts/knowledge/retrieval.py):310–350).
PostgreSQL retrieval instead uses English text-search lexemes and weighted
coverage. Oversized source passage selection also uses exact word overlap
([passages.py](../../../../backend/app/services/scripts/knowledge/passages.py):26–50).
Consequently, changing coverage alone cannot repair both missing candidates
and oversized-passage selection. The shared evidence threshold stays 0.5
([kb_budget.py](../../../../backend/app/domain/services/voice_pipeline/kb_budget.py):76–148).

The independent controls were declared before their experiments. The first
inventory measured 12 failures / 7 passes; an expanded inventory including a
long source measured 13 failures / 10 passes. Final unchanged-production
controls, including later observed-collision regressions, measured **13 failures
/ 15 passes**. Twelve failures are singular/plural directions for account,
fee, booking, delivery, batch and invoice. The remaining failure is a long
source with `Deliveries take five days. This excludes remote islands.` queried
as `Delivery`. The preserved positive control for `not supported` retains the
full original statement and adjacent condition. Failures are qualification
gaps, not assertions that an actual caller received a false answer.

The historical gold failures mainly concern missing sources or insufficient
lexical coverage, rather than dropped text: in the preserved raw-source
corpus all found-source insufficient cases retained the required source
fragments. The existing corpus contains no source long enough to exercise
the newly declared long-passage control. Therefore that corpus alone cannot
establish passage-selection correctness.

## Experiment results

| Experiment | Actual boundary/control result | Decision |
| --- | --- | --- |
| Blanket Snowball keys for coverage and passage matching, unchanged candidate ranking | 20/23 controls passed; delivery directions and long source still lacked candidates | Insufficient repair |
| Additional blanket collision probes | `Universes` → `Universities are listed here.` changed weak to matched; the two multiword cases below also changed weak to matched | Rejected before application edits |
| Direct surface plural relation AND Snowball agreement, used for candidate overlap, coverage and passage matching | 26/28 passed; all 12 plural directions and the long source with its exclusion passed | Rejected because two negative controls fail |

The paired proposal compares only direct `s`, `es`, and `y`/`ies` surface
relations, then requires established Snowball agreement. It does not equate
arbitrary stems and does not contain a per-word exception list. Digit-bearing
or oversized tokens remain exact and remain in the coverage denominator.
Nevertheless these actual `retrieve_pinned_knowledge` →
`prepare_knowledge_evidence` controls newly become matched:

- `Universe courses` against `University course fees are 100 units.`
- `Organ memberships` against `Organization membership costs 50 units.`

The paired proposal does **not** conflate Universe/University or
Organ/Organization. It matches the other, valid plural pair and reaches
exactly 0.5 coverage while the critical mismatched noun remains uncovered.
This distinction matters: passing the single-word stem-collision negative
does not establish safe evidence admission for a whole question. No
additional special-case filter or threshold change was attempted.

The test-only overlay retains the production functions' score weights, fuzzy
rules, stopwords, safety checks, original passages and source provenance.
Candidate overlap changes, so candidate selection and relative ranking can
change; the artifact explicitly preserves those differences.

`paired-gold.json` records all 60 unchanged cases, including 45 answerable:

- Baseline: expected source recall **42/45 (93.33%)**; sufficient passage
  **32/45 (71.11%)**.
- Paired experiment: expected source recall **42/45 (93.33%)**; sufficient
  passage **34/45 (75.56%)**. Q12 payment links and Q14 weekend payouts improve.
- Both reports fail the unchanged retrieval qualification gate. No SQL
  retrieval matrix or provider answer fidelity was rerun in this investigation.
- The working gold file bytes equal the baseline Git blob. The matrix hash
  recorded by both reports is identical; the fixture was not edited.

## Established normalizer assessment

The candidate was `snowballstemmer==2.2.0`, downloaded as a wheel into ignored
`tmp/ag02-normalizer-wheel` and imported only by the diagnostic processes.
It was **not installed** in the project environment or requirements.

Measured archive facts: **93,002 bytes compressed; 785,954 bytes expanded;
37 archive members; no `Requires-Dist` entries; BSD-3-Clause metadata**.
The normal package import loads all its supported language modules. The
explicit `EnglishStemmer` class avoids the package factory's optional
native-extension substitution. `BaseStemmer` mutates its current word and
cursor, so each prototype creates a fresh local instance; there is no shared
mutable instance or persistent caller-term cache. Only alphabetic tokens of
2–64 characters enter stemming; other tokens are retained exactly.

The Windows Python 3.12.12 wheel-only measurements are diagnostic, not a
deployment latency forecast: cold import/instance without tracing took
542–859 ms over three subprocesses. Traced import retained about 3.0 MB of
Python allocations and peaked near 14.0 MB; tracing itself increased runtime.
The blanket helper's 1,600-token synthetic loop had a median near 110 ms over
seven runs. A 100,000-character token was retained exactly and bypassed the
stemmer in about 0.06 ms. Installed bytecode, host contention and production
workloads can differ; the paired helper's end-to-end production cost was not
benchmarked.

Both official [PostgreSQL 15 Snowball source notes](https://raw.githubusercontent.com/postgres/postgres/REL_15_STABLE/src/backend/snowball/README)
and [PostgreSQL 16 source notes](https://raw.githubusercontent.com/postgres/postgres/REL_16_STABLE/src/backend/snowball/README)
reference upstream Snowball v2.2.0. The package documentation describes
[stemming and optional native acceleration](https://pypi.org/project/snowballstemmer/2.2.0/).
This supports a version comparison, **not** a claim of retrieval parity or
semantic equivalence.

Authorized loopback read-only comparison used `SHOW server_version` and
`SELECT ts_lexize('english_stem', ...)` in a read-only transaction. The actual
server reported **16.1**. Tested ordinary word stems agreed with the pure
Python class; PostgreSQL removed its stopwords, including `and`, `from`,
`no`, `not`, and `the`. PostgreSQL tokenization, stopword behavior, query
composition and IDF weighting remain different. No PostgreSQL 15 server
was executed; its algorithm reference was inspected only. No schema, table,
customer data or production database was mutated. Connection closed after
the probe; the shared disposable server was left running.

## Existing enrichment and remaining work

The existing ingestion path already incorporates keywords and example
questions into `search_text`
([ingest_service.py](../../../../backend/app/services/scripts/knowledge/ingest_service.py):91–93).
Its optional enrichment returns empty derivatives when unavailable and can
publish the original source ready
([enricher.py](../../../../backend/app/services/scripts/knowledge/enricher.py):112–133).
Generated summaries or aliases are not factual source approval. The raw,
unenriched gold condition remains a real supported fail-soft condition.

The next qualification input is a versioned, representative campaign corpus
with content-owner review, assessed through existing ingestion/enrichment and
source-only evidence paths. That is pending qualification work, not a new
algorithm proposal or a claim that enrichment closes the gap. Preserve this
raw-source baseline separately and retain honest abstention. No live
enrichment/model request was made for this investigation.

## Evidence and reproduction

- `predeclared-controls.md`: evolving inventory, with additions distinguished
  from the initial predeclared cases.
- `initial.txt`, `baseline-expanded.txt`, `baseline-final-controls.txt`:
  retained production red results with the exact inventory counts above.
- `candidate-controls.txt`: first blanket experiment, 20 passes / 3 failures
  over its then-current 23 controls; it predates added collision regressions.
- `wheel-assessment.json`: first diagnostic with no-match singular collisions.
- `wheel-assessment-final.json`: measured footprint and the actual newly
  admitted collision probes; initial no-op cases remain visible.
- `paired-controls.txt`: final paired prototype, 26 passes / 2 failures.
- `paired-gold.json`, `paired-gold-command.txt`: unchanged gold before/after.
- `test_normalization_controls.py`, `normalizer_probe.py`,
  `paired_plural_probe.py`: reproducible test-only sources.

The controls were moved from `backend/tests/unit/test_ag02_normalization_boundary.py`
to this artifact directory after the experiment. Historical logs retain their
original path. The deliberately failing controls are not added to the normal
unit-suite collection. The prototype entrypoints now address the artifact
control file; no application file was changed.

Use the original project Python interpreter from this worktree's `backend`
directory, with `ENVIRONMENT=test`, `PYTHONPATH=.` and
`DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`. Download the
wheel with `python -m pip download --no-deps --only-binary=:all: --dest
../tmp/ag02-normalizer-wheel snowballstemmer==2.2.0`.

```text
python -m pytest ../docs/sessions/artifacts/ag02-normalization/test_normalization_controls.py -q -o addopts=
python ../docs/sessions/artifacts/ag02-normalization/paired_plural_probe.py --wheel ../tmp/ag02-normalizer-wheel/snowballstemmer-2.2.0-py2.py3-none-any.whl --controls
python ../docs/sessions/artifacts/ag02-normalization/paired_plural_probe.py --wheel ../tmp/ag02-normalizer-wheel/snowballstemmer-2.2.0-py2.py3-none-any.whl --gold-output NEW_OUTPUT.json
python ../docs/sessions/artifacts/ag02-normalization/normalizer_probe.py --wheel ../tmp/ag02-normalizer-wheel/snowballstemmer-2.2.0-py2.py3-none-any.whl --output NEW_WHEEL_OUTPUT.json
```

Expected baseline and prototype control exits are nonzero. `--postgres` is
optional and must be used only with the explicitly coordinated disposable
loopback fixture; it is unnecessary for the rejected-proposal controls.
The current blanket entrypoint runs the expanded control file rather than
reproducing the earlier 23-case count. The in-process pytest overlay emits a
benign `anyio` assertion-rewrite warning because imports precede pytest.

Independent read review by `llm_audit` confirmed the paired experiment's
scope, two remaining admission failures and unchanged gold metrics. That
review ran no additional suite or PostgreSQL commands. Root agreed to reject
both integrations. Application code, requirements, SQL, gold and thresholds
remain at baseline; AG02 qualification remains open.
