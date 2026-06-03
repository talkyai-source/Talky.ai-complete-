# Plan — Vectorless RAG: campaign knowledge tree + simplified creation flow

**Date:** 2026-06-03
**Status:** awaiting review (no code yet)
**Decision locked:** retrieval = **Adaptive FTS** (small KB → inline; large KB → Postgres full-text over enriched tree leaves). No vector DB, no embeddings.

---

## 1. Goal

Let a campaign owner give the agent real knowledge by **uploading a `.md`/`.txt` file** instead of hand-writing prompts. The system turns that file into a **hierarchical knowledge tree** (PageIndex-style, vectorless), shows the tree in the frontend for review/editing, and feeds the agent the *right* slice of it at call time — without adding latency to the voice turn or any new infrastructure.

This is a **content/knowledge layer** that augments the existing persona layer (`script_config` → persona prompt). Persona = *how* the agent behaves; knowledge tree = *what it knows* when a prospect asks a question or objects.

## 2. Design principles (the corrections that shaped this)

1. **Markdown headings ARE the tree.** Parse `#/##/###` deterministically; never ask the LLM to "split" structured md. LLM is used only to *enrich* nodes (summary, keywords, example questions). Plain `.txt` with no headings is the one case where the LLM segments.
2. **Build offline, retrieve fast.** The LLM builds/enriches the tree once on upload (slow is fine). Per-turn retrieval is a single indexed Postgres FTS query (~ms) — never an LLM tree-walk (seconds = unacceptable on the voice path).
3. **Adaptive.** KB ≤ ~1,500 tokens → inline the whole compacted tree in the system prompt (cheapest, best recall, zero per-turn work). KB larger → per-turn FTS injection of top-K leaves.
4. **Short injections.** Inject node `summary`(+short `content`), not whole sections — voice answers are 1–2 sentences.
5. **No new infra.** Postgres `tsvector` FTS only. Reuses the existing DB, the existing per-turn injection seam, and the Alembic migration system (item 4).

## 3. Architecture overview

```
UPLOAD (.md/.txt)
   │  POST /api/v1/campaigns/{id}/knowledge   (multipart)
   ▼
INGEST (async, one-time per upload)
   1. store raw md                         → campaign_knowledge_sources
   2. parse md headings → node tree        (deterministic; .txt → LLM segment)
   3. ONE batched LLM enrich call          → summary, keywords[], example_questions[] per node
   4. build search_tsv per node; sum tokens → set campaign.knowledge_mode = inline | retrieve
   5. status = ready
   ▼
STORAGE  campaign_knowledge_nodes (parent_id tree + FTS index)
   ▼
CALL TIME  (turn_streamer.py, per user turn)
   inline   → whole compacted tree already in system_prompt (added at session build)
   retrieve → 1 FTS query on transcript → top-2 leaves → inject "Relevant knowledge:" block
   ▼
FRONTEND  collapsible tree (edit / disable / reorder), creation wizard
```

## 4. Data model (Alembic `0010_campaign_knowledge`)

```sql
CREATE TABLE campaign_knowledge_sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,                 -- denormalised for RLS
    filename      TEXT,
    raw_md        TEXT NOT NULL,
    token_count   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'processing'   -- processing|ready|failed
                  CHECK (status IN ('processing','ready','failed')),
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE campaign_knowledge_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    source_id       UUID NOT NULL REFERENCES campaign_knowledge_sources(id) ON DELETE CASCADE,
    parent_id       UUID REFERENCES campaign_knowledge_nodes(id) ON DELETE CASCADE,
    depth           INTEGER NOT NULL DEFAULT 0,
    path            TEXT NOT NULL,                -- "1.2.3" materialised path, sortable
    position        INTEGER NOT NULL DEFAULT 0,
    heading         TEXT NOT NULL,                -- from md heading
    content         TEXT NOT NULL DEFAULT '',     -- body under this heading (excl. children)
    summary         TEXT,                         -- LLM: 1 line
    keywords        TEXT[],                       -- LLM: synonyms/terms for matching
    example_questions TEXT[],                     -- LLM: "used when caller asks…"
    search_tsv      tsvector,                     -- heading + content + keywords + example_qs
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ckn_fts        ON campaign_knowledge_nodes USING gin(search_tsv);
CREATE INDEX idx_ckn_campaign   ON campaign_knowledge_nodes (campaign_id, enabled);
CREATE INDEX idx_ckn_tree       ON campaign_knowledge_nodes (campaign_id, parent_id, position);

-- RLS mirrors tenant_ai_credentials (both tables):
ALTER TABLE campaign_knowledge_nodes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_knowledge_sources ENABLE ROW LEVEL SECURITY;
CREATE POLICY ckn_isolation ON campaign_knowledge_nodes FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);
-- (same for _sources)
```
Also add `campaigns.knowledge_mode TEXT DEFAULT 'none'` (`none|inline|retrieve`) — set by ingest, read at session build. Add the same DDL to `database/schema/baseline_2026-06-02.sql` for fresh installs.

## 5. Ingest pipeline

New package (follows scripts-folder convention, ≤600 lines/file):
`backend/app/services/scripts/knowledge/`
- `md_tree.py` — deterministic md→node-tree parser (markdown headings → nested nodes; preserves body per node). `.txt` fallback: LLM segmentation.
- `enricher.py` — one batched LLM call: input the node headings+content, output `{summary, keywords, example_questions}` per node. Bounded + JSON-schema'd; fail-soft (node still usable with empty enrichment).
- `ingest_service.py` — orchestrates: store source → parse → enrich → tsv (`to_tsvector('english', heading||' '||content||' '||array_to_string(keywords,' ')||' '||array_to_string(example_questions,' '))`) → set `knowledge_mode` from token_count → status=ready. Idempotent re-ingest (replace nodes for the source).
- `retrieval.py` — `retrieve_knowledge(campaign_id, transcript, k=2) -> list[node]` via `websearch_to_tsquery` + `ts_rank`; and `compact_tree(campaign_id) -> str` for inline mode.

Threshold: `INLINE_MAX_TOKENS = 1500` (env-tunable).

## 6. Retrieval + prompt injection (the call path)

**Inline mode:** at session build (`build_telephony_session_config`), if `campaign.knowledge_mode == 'inline'`, append `compact_tree(...)` to the composed `system_prompt` once. Per-turn cost: zero.

**Retrieve mode:** in `turn_streamer.py`, at the existing injection seam (right after the Ask-AI product block, ~line 90), add:
```python
if session.knowledge_mode == "retrieve" and last_user_text:
    nodes = await retrieve_knowledge(session.campaign_id, last_user_text, k=2)   # 1 FTS query
    if nodes:
        block = "Relevant knowledge for this question:\n" + "\n".join(
            f"- {n.summary or n.heading}: {n.content[:240]}" for n in nodes)
        system_prompt = system_prompt + "\n\n" + block
```
- Generalises the existing Ask-AI keyword-injection from hardcoded to data-driven.
- `session.knowledge_mode` + `session.campaign_id` carried on the session (set at build). Retrieval guarded so non-knowledge campaigns pay nothing.
- Latency budget: one GIN-indexed FTS query, target < 15ms; runs concurrently with first-token wait, so net ≈ 0 added to perceived latency.

## 7. API endpoints (`app/api/v1/endpoints/campaign_knowledge.py`, new router)
- `POST /campaigns/{id}/knowledge` — multipart upload (.md/.txt), kicks off async ingest, returns `source_id` + `status=processing`.
- `GET  /campaigns/{id}/knowledge` — returns the tree (nodes nested by parent_id) + source status + mode.
- `PATCH /campaigns/{id}/knowledge/nodes/{node_id}` — edit `summary/content/enabled/position` (re-tsv on content change).
- `DELETE /campaigns/{id}/knowledge/sources/{source_id}` — remove a source + its nodes.
All tenant-scoped (RLS + `require_tenant_access`), size-capped (e.g. 256KB md), rate-limited.

## 8. Frontend (Talk-Leee)
- `components/campaigns/knowledge-tree.tsx` — collapsible outline of the node tree; each node shows heading, summary, keyword chips, "🗣 used when caller asks: <example_questions>"; controls: enable/disable toggle, drag-reorder, inline-edit summary/content.
- `components/campaigns/knowledge-upload.tsx` — drag-drop `.md`/`.txt` + paste; shows ingest progress (poll status).
- **Creation wizard** (refactor `campaign-form.tsx` into steps):
  1. Basics — name, voice, agent names, goal, persona type (→ `script_config`).
  2. Knowledge — upload md/txt → ingest → review tree (the `knowledge-tree` component).
  3. Preview & test — show composed agent + a quick test chat.
- API client additions in `Talk-Leee/src/lib/api.ts`.

## 9. Build sequence (each phase shippable, behind `CAMPAIGN_KNOWLEDGE_ENABLED` flag)
- **P1 — DB + ingest:** migration 0010 + baseline; `knowledge/` package; upload+ingest endpoint. (No call-path change.) Verify: upload a sample md, inspect nodes + tsv in DB.
- **P2 — retrieval + injection:** `retrieve_knowledge`/`compact_tree`; wire inline at session build + FTS at turn_streamer seam; `knowledge_mode` on session. Flagged. Verify: live call, knowledge block appears, latency unchanged, persona untouched.
- **P3 — frontend tree:** tree view + upload UI + edit/disable.
- **P4 — creation wizard:** the 3-step flow.
- **P5 — (optional) recall upgrade:** cached per-call "branch router" LLM call, only if FTS recall proves weak in practice. Long-context inline already covers small KBs.

## 10. Testing
- Unit: `md_tree` (headings→tree incl. skipped levels, code blocks, no-heading txt), `retrieval` (FTS ranking, disabled nodes excluded, tenant isolation), `enricher` (schema-validated, fail-soft).
- Integration: ingest a real md → assert tree shape + tsv; retrieve for a sample question → expected node; turn_streamer injects block in retrieve mode and nothing in `none` mode.
- Latency: assert retrieval query < 15ms on a ~200-node KB.

## 11. Risks / edge cases
- **FTS misses synonyms** → mitigated by LLM `keywords`/`example_questions` in the tsv; P5 router as escape hatch.
- **Huge upload** → cap size; `retrieve` mode bounds prompt growth regardless of KB size.
- **Enrich LLM cost/latency** → one batched call per upload, fail-soft (node usable without enrichment).
- **Stale knowledge after edit** → re-tsv on content edit; re-ingest replaces nodes for a source.
- **RLS** → retrieval runs via `acquire_with_tenant` (the dialer/bridge path has no JWT), same as `tenant_phone_number_service`.
- **Voice answer length** → injected snippets capped (240 chars); persona prompt already constrains reply length.

## 12. Out of scope (deliberately)
- Vector embeddings / vector DB (the whole point — vectorless).
- Per-turn LLM tree navigation (latency).
- Cross-campaign / global knowledge sharing.
- Live document sync from external sources (future).

---

**Open follow-ups after approval:** confirm `INLINE_MAX_TOKENS`, the upload size cap, and whether the wizard replaces or sits alongside the current `campaign-form.tsx`.
