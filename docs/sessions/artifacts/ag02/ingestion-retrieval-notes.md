# AG02 ingestion, source revision and tenant retrieval evidence

Date: 2026-10-05. Working baseline: `717fb14f`, branch `codex/production-ready-20261004`; results include uncommitted integrated AG02 changes. These are local synthetic checks, not a production deployment, customer content approval or voice-call acceptance.

## Reproduced defects and repairs

- Six initial database/boundary tests failed: live hits lacked source identity/revision; processing/failed sources could supply nodes; legacy cross-tenant source references remained retrievable; content edits retained an old generated price; and inline rendering attempted an RLS-bypass acquisition for a missing tenant. The actual `0010_campaign_knowledge` migration is used in an isolated schema, with a `NOSUPERUSER NOBYPASSRLS` application role. Existing single-column foreign keys permit the legacy corrupt relationship exercised by the test; this does not establish that production contains such rows.
- `retrieve_knowledge` and the shared current-node loader now require enabled nodes from a ready source with matching tenant and campaign. Explicit tenant predicates supplement RLS. Inline rendering and inbound admission use the same loader. Hits include source ID, source revision, node update time, and a node-version token.
- HTTP and authorized assistant node mutations use one small SQL helper. Source edits discard stale generated summary/phrasing/search terms unless summary or phrasing was explicitly resupplied in that edit. Both node and source revision change in the same transaction. No human approval binding exists for generated `voice_answer`; factual rendering therefore uses source content only, including historical rows.
- A separate real PostgreSQL concurrency reproduction showed a new heading being committed while a concurrent content update indexed the old heading. Locking the current node before combining fields fixes this; the test observes actual PostgreSQL lock contention and verifies the committed heading/content/index combination and revision counter.
- A direct ingestion-service test reproduced creating a source under tenant A for tenant B's campaign. Public endpoints already have an authorization lease. The service insert now also requires the campaign's matching tenant, protecting direct callers without relying only on that lease.
- Incomplete full inline trees return empty and force existing per-turn retrieval rather than baking a truncated price/condition and suppressing subsequent lookup. Topic-map summaries remain navigation only. Parent-owned session injection rejects suspicious knowledge bakes and enforces session identity; parent-owned turn streaming removes live process-local retrieval caching, retaining its existing lookup timeout.

## Snapshot and version policy limits

Inbound admission intentionally pins the selected ready/enabled nodes and checksum. Later edits or disables do not mutate that admitted call's snapshot. Live current retrieval reads current committed data; the cache is not a cross-process freshness mechanism. Complete inline knowledge remains a setup-time bake under the existing policy.

Uploads remain additive documents identified by source ID. A reused filename does **not** automatically replace an older source, and no filename-based supersession heuristic was introduced. Operators must disable/remove obsolete evidence. The old-version negative control explicitly disables its old node; it does not prove automatic conflict resolution between two enabled, ready uploads. Existing `source.version` now advances for node edits; the schema does not archive every past editable node body. Existing pinned call snapshots are preserved. No migration or historical deletion was added.

## Measured results and commands

Interpreter: original workspace `backend/.venv/Scripts/python.exe`; working directory: this worktree's `backend`. Disposable PostgreSQL 16 on loopback port 55434, database `cp04_acceptance_test` (existing public migration head `0056_billing_refund_snapshots`), with each knowledge fixture creating/removing its own random schema and role. Only synthetic fixtures were used. The owned PostgreSQL process was stopped after testing; its retained data directory was not deleted.

Unit command:

```powershell
python -m pytest -q tests/unit/test_knowledge_render.py tests/unit/test_knowledge_write_invalidation.py tests/unit/test_knowledge_ingest_staging.py tests/unit/test_campaign_knowledge_staged_http.py tests/unit/test_campaign_knowledge_permissions.py tests/unit/test_assistant_knowledge_authorization.py tests/unit/test_assistant_campaign_admin.py tests/unit/test_inbound_admission.py tests/unit/test_agent_knowledge_repairs.py tests/unit/test_knowledge_relevance.py tests/unit/test_knowledge_session_inject.py tests/unit/test_knowledge_budget.py tests/unit/test_knowledge_md_tree.py tests/unit/test_knowledge_enricher_model.py tests/unit/test_kb_source_first_all_paths.py
```

Result: **231 passed, 3 pre-existing deprecation warnings, 2.88 seconds**, saved in `ingestion-retrieval-unit.txt`. The earlier 223-test run excluded the eight renderer-path cases; do not add the two overlapping counts together.

Database command:

```powershell
$env:TEST_DATABASE_URL='postgresql://talky@127.0.0.1:55434/cp04_acceptance_test'
$env:AG02_POSTGRES_GOLD_OUTPUT='../docs/sessions/artifacts/ag02/gold-postgres.json'
python -m pytest -q -s tests/integration/test_knowledge_retrieval_boundaries.py
```

Result: **9 passed, 3.87 seconds**, saved in `ingestion-retrieval-postgres.txt`. Those passes verify database selection/mutation boundaries and complete matrix execution; they do not award the separate quality target. The integration module is included in the existing migrated PostgreSQL CI job.

`ruff check --select F` passed for the six changed application files and four owned test files. `git diff --check` passed (line-ending normalization notices only).

## Quality target still unmet

Actual PostgreSQL production FTS/trigram + shared evidence on the predeclared raw, unenriched synthetic matrix: 60 questions, 45 answerable. Expected-source recall at three results: **40/45 (88.89%)** against 90%. Complete, sufficiently matched source passage: **31/45 (68.89%)** against 85%. Foreign-tenant/disabled-control hits: **0**. `gold-postgres-initial.json` and `gold-postgres.json` retain the observed results. No query expansions, question text, thresholds or ranking were tuned to make these results pass.

The matrix is synthetic and not approved by a customer/content owner. Model response fidelity, actual provider behavior, human approval and full call latency were not evaluated. Lexical paraphrase coverage and semantic answerability remain open; zero foreign evidence does not mean zero hallucination.

## Owned source inventory

- `backend/app/services/scripts/knowledge/ingest_service.py`
- `backend/app/services/scripts/knowledge/retrieval.py`
- `backend/app/services/scripts/knowledge/node_updates.py` (new shared mutation helper)
- `backend/app/api/v1/endpoints/campaign_knowledge.py`
- `backend/app/infrastructure/assistant/tools/campaign_admin.py`
- `backend/app/domain/services/telephony/inbound_admission.py`
- `backend/tests/integration/test_knowledge_retrieval_boundaries.py` (new)
- `backend/tests/unit/test_knowledge_render.py`
- `backend/tests/unit/test_knowledge_write_invalidation.py`
- `backend/tests/unit/test_kb_source_first_all_paths.py`
- `.github/workflows/ci.yml` (adds only this integration module to the existing job)

No changes to production configuration, credentials, customer documents or remote providers. No live sends, calls, database migrations on production, commits or pushes were performed by this subagent.
