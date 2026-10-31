# AG02: generated search metadata cannot establish factual confidence

Date: 6 October 2026. Base: `c01175c15679d881f591c7c5d0a5e17d5af4c465`.

Generated keywords and example questions could route an unrelated authored section
and also give it full evidence coverage. A valid response for the correct node was
enough; the earlier enrichment ownership repair did not prevent this. PostgreSQL
also counted aliases when measuring word rarity, so changing another node's
generated aliases changed an unchanged source's confidence.

`retrieval.py` now keeps the existing mixed search index, candidate selection and
ranking, while measuring factual coverage from authored heading/content only.
PostgreSQL uses a separate authored-source document frequency for this final
coverage. Pinned queries without measurable coverage terms now receive unknown
coverage, preventing reuse of an incoming score from a previous query. No threshold,
gold question, provider, dependency or schema changed.

## Reproduction and verification

| Check | Preserved result |
| --- | --- |
| Actual valid-node enrichment decoder → ingestion search text → pinned retrieval → shared evidence | Initial **8 failed, 4 passed**; aliases wrongly admitted unrelated support text, including legacy derivative fallback |
| Actual PostgreSQL retrieval → shared evidence, four new controls | Initial **3 failed, 1 passed**; unrelated aliases gave coverage 1.0, and another node's alias changed unchanged source coverage from 0.5 to 0.2082559308 |
| Numeric-only incoming-confidence follow-up | **1 failed, 12 deselected** before the two-line unknown-coverage correction |
| Final affected unit suite | **174 passed** across ten modules, including all 13 new pinned controls; earlier 173-pass run retained separately |
| Actual PostgreSQL existing integration module | **13 passed**, including four new controls and the existing 60-question matrix; not a passing knowledge-quality gate |
| Source checks | Scoped Ruff `F` checks and `git diff --check` passed |

The SQL run preceded the numeric-only correction in the pinned function. The
tested full-file hash and final full-file hash differ. The exact UTF-8 bytes of
`retrieve_knowledge` remain identical, recorded in [SQL parity](artifacts/ag02-authored-coverage/sql-final-parity.json).
The affected unit suite ran again after that correction. The earlier results were
preserved, not replaced or added to the final count.

The database run was explicitly authorized against the existing disposable
`127.0.0.1:55434/cp04_acceptance_test`. The existing fixture applied migration 0010
inside UUID private schemas and used UUID non-bypass roles. The runner required
the existing `pg_trgm` extension and public head `0061_dnc_runtime_contract` before
execution. Public data and schema snapshots, all schema/role names and source
hashes matched before/after; zero prohibited network attempts were recorded.
No existing public records, extension installation or unrelated-session cleanup
were involved. This is actual SQL/RLS evidence using a partial private fixture,
not a full deployment qualification.

## Knowledge gate remains open

The unchanged, raw unenriched synthetic matrix still fails:

- Pinned: expected-source recall **42/45**, sufficient passage **32/45**; standalone evaluator exit **1**.
- PostgreSQL: expected-source recall **40/45**, sufficient passage **31/45**; zero cross-tenant or disabled hits.

The source repair does not establish semantic answer fidelity or product identity,
complete qualifier handling, live call quality, customer approval or production
latency. Generated aliases can help routing but cannot alone authorize a factual
answer. Correctly cautious abstention remains possible for valid paraphrases.

The new checkout's matrix uses LF (raw SHA-256 `31782fc367ca7bb3f07ba0569b5d1eb0e4cdfbae274b618f3b7c0733db504b52`);
MAIN and historical artifacts use CRLF (`48014c077165609663d74c775bf0439a1e769bdd15ed07052b74a6e40252cb8d`).
Normalized LF bytes and the base Git blob are identical; [parity evidence](artifacts/ag02-authored-coverage/gold-parity.json)
records this formatting difference without changing the corpus.

## Evidence and commands

The [verification manifest](artifacts/ag02-authored-coverage/verification.json)
records exact commands, final source hashes and artifact hashes. Unit logs,
original failure logs, PG snapshots, preserved pre-follow-up results and both
matrix reports are in [the artifact directory](artifacts/ag02-authored-coverage/).

All commands used the existing repository `backend/.venv/Scripts/python.exe`,
not a newly installed or claimed exact-requirements environment. PG metadata
records interpreter and dependency provenance. No provider, browser, audio or
customer interaction occurred. Independent read-only review and root review
cleared the source; the production-readiness freeze and package/gate statuses
remain unchanged.
