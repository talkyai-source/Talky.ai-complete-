# Plan — Vectorless RAG: campaign knowledge tree + simplified creation flow

**Date:** 2026-06-03
**Status:** **P1–P4 done & fully deployed (2026-06-04). Backend live on prod (flag ON); frontend live at talkleeai.com.** Remaining: human-answered live-call acceptance of P2; P5 (optional branch router) only if recall proves weak.
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
    voice_answer    TEXT,                         -- LLM: 1–2 sentence spoken-style answer (voice-optimised injection)
    keywords        TEXT[],                       -- LLM: synonyms/terms (incl. likely STT mishears) for matching
    example_questions TEXT[],                     -- LLM: "used when caller asks…"
    search_tsv      tsvector,                     -- heading + content + keywords + example_qs
    search_text     TEXT,                         -- same, plain text — for pg_trgm fuzzy (STT-mishear tolerance)
    priority        SMALLINT NOT NULL DEFAULT 0,  -- tie-breaker / pin critical nodes
    hit_count       BIGINT NOT NULL DEFAULT 0,    -- retrieval analytics (most-used knowledge)
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ckn_fts        ON campaign_knowledge_nodes USING gin(search_tsv);
CREATE INDEX idx_ckn_trgm       ON campaign_knowledge_nodes USING gin(search_text gin_trgm_ops);  -- fuzzy/STT-tolerant
CREATE INDEX idx_ckn_campaign   ON campaign_knowledge_nodes (campaign_id, enabled);
CREATE INDEX idx_ckn_tree       ON campaign_knowledge_nodes (campaign_id, parent_id, position);
-- requires:  CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- RLS mirrors tenant_ai_credentials (both tables):
ALTER TABLE campaign_knowledge_nodes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_knowledge_sources ENABLE ROW LEVEL SECURITY;
CREATE POLICY ckn_isolation ON campaign_knowledge_nodes FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);
-- (same for _sources)
```
Also on `campaigns`: `knowledge_mode TEXT DEFAULT 'none'` (`none|inline|map_retrieve|retrieve`, set by ingest from the model-aware budget — §13A) and `knowledge_model TEXT NULL` (optional larger-context model for knowledge-heavy campaigns — §13B). Migration `0010` also runs `CREATE EXTENSION IF NOT EXISTS pg_trgm;` (fuzzy/STT-tolerant retrieval). Mirror all DDL into `database/schema/baseline_2026-06-02.sql` for fresh installs/CI.

## 5. Ingest pipeline

New package (follows scripts-folder convention, ≤600 lines/file):
`backend/app/services/scripts/knowledge/`
- `md_tree.py` — deterministic md→node-tree parser (markdown headings → nested nodes; preserves body per node). `.txt` fallback: LLM segmentation.
- `enricher.py` — one batched LLM call: input the node headings+content, output `{summary, keywords, example_questions}` per node. Bounded + JSON-schema'd; fail-soft (node still usable with empty enrichment).
- `ingest_service.py` — orchestrates: store source → parse → enrich → tsv (`to_tsvector('english', heading||' '||content||' '||array_to_string(keywords,' ')||' '||array_to_string(example_questions,' '))`) → set `knowledge_mode` from token_count → status=ready. Idempotent re-ingest (replace nodes for the source).
- `retrieval.py` — `retrieve_knowledge(campaign_id, transcript, k=2) -> list[node]` via `websearch_to_tsquery` + `ts_rank`; and `compact_tree(campaign_id) -> str` for inline mode.

**Mode selection is model-aware (not a fixed threshold) — see §13.** Three modes, chosen automatically from the campaign's model context window and the KB size: `inline` (whole tree fits the budget), `map+retrieve` (always inline the heading/summary *skeleton* — the agent's "table of contents" — and FTS the detailed leaves per turn), `retrieve` (huge KB → FTS only).

## 6. Retrieval + prompt injection (the call path)

**Inline mode:** at session build (`build_telephony_session_config`), if `campaign.knowledge_mode == 'inline'`, append `compact_tree(...)` to the composed `system_prompt` once. Per-turn cost: zero.

**Retrieve / map+retrieve mode:** in `turn_streamer.py`, at the existing injection seam (right after the Ask-AI product block, ~line 90), add a data-driven injection. Retrieval is **hybrid** — FTS *and* trigram-fuzzy in one query — because the input is an **STT transcript that mishears words** ("commitment"→"committing"); exact FTS alone would miss those. One combined indexed query, ranked, top-K:
```python
if session.knowledge_mode in ("retrieve", "map_retrieve") and last_user_text:
    nodes = await retrieve_knowledge(session.campaign_id, last_user_text, k=2)  # hybrid FTS+trgm, 1 query
    if nodes:
        block = "Relevant knowledge for this question:\n" + "\n".join(
            f"- {n.heading}: {n.voice_answer or n.summary or n.content[:240]}" for n in nodes)
        system_prompt = system_prompt + "\n\n" + block
        # fire-and-forget hit_count bump for analytics
```
`retrieve_knowledge` SQL (sketch): `ts_rank(search_tsv, websearch_to_tsquery(:q))` **OR** `similarity(search_text, :q) > 0.25`, combined score `0.7*ts_rank + 0.3*trgm_sim + 0.01*priority`, `WHERE enabled`, `LIMIT k`.
- Injects the LLM-written **`voice_answer`** (spoken-style) when present, so the agent speaks a clean 1–2 sentence answer, not a raw doc snippet.
- `session.knowledge_mode` + `session.campaign_id` set at build; non-knowledge campaigns pay nothing.
- Latency: one GIN-indexed (FTS+trgm) query, target < 15ms, runs concurrently with first-token wait → net ≈ 0 perceived.

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
- **P1 — DB + ingest: ✅ DONE (2026-06-03).** migration 0010 + baseline; `knowledge/` package; upload+ingest endpoint. Validated end-to-end against **real prod Postgres + Groq** (harness ran on a test campaign, cleaned up after itself): parse→6 nodes, Groq enrichment on all, `search_tsv` populated, model-aware `knowledge_mode` set, FTS + fuzzy retrieval + `compact_tree` + `hit_count` analytics all correct.
  - Two retrieval bugs found by the live harness and fixed (commit c3ed6ce):
    1. **`similarity()` → `word_similarity()`**: `similarity(text, query)` divides by the union of the *whole document's* trigrams → always ~0 for a short query vs long node, so the fuzzy half never fired. `word_similarity(query, text)` scores against the best contiguous extent inside the text — that's what catches STT mishears (`warrantee`→`warranty`).
    2. **AND-only FTS → tiered OR-recall**: `websearch_to_tsquery` is AND-only, so "what areas do you cover" (`'area' & 'cover'`) missed the Service Areas node (has "area", not "cover"). Retrieval is now tiered: exact-AND (2) > any-term-OR (1, AND-query rewritten to OR) > fuzzy word_similarity (0), sorted by strength→priority→hit_count. Precision wins; recall never silently drops a relevant node. Floor via `KNOWLEDGE_WORD_SIM_FLOOR` (0.35).
  - **Migration footgun fixed**: revision id `0009_dialer_jobs_failure_classification` (39 chars) overflowed `alembic_version.version_num` varchar(32) → `alembic upgrade head` rolled back, stranding prod at 0008. Shortened to `0009_dialer_failure_class`; keep all future revision ids ≤32 chars.
- **P2 — retrieval + injection: ✅ DONE + verified on prod (2026-06-04).** Two seams:
  - **Pre-warm** (`telephony/prewarm.py` → `knowledge/session_inject.apply_campaign_knowledge`): once, async with the pool. `inline`→bake full `compact_tree` into `call_session.system_prompt`; `map_retrieve`→bake the skeleton (TOC); `retrieve`→nothing here (served per-turn). Also stamps `call_session.tenant_id` (the orchestrator never copied `config.tenant_id`) + `knowledge_mode`.
  - **Per-turn** (`voice_pipeline/turn_streamer.py`): for `retrieve`/`map_retrieve`, fetch top-k for the caller's latest message and inject for that turn only. Bounded by `KNOWLEDGE_RETRIEVE_TIMEOUT_MS` (250ms) so a slow DB can't hurt TTFT; fully fail-soft. `inline` skips it (already baked).
  - `CallSession.knowledge_mode` added; shared `knowledge_enabled()` gate; flag **enabled on prod** (`CAMPAIGN_KNOWLEDGE_ENABLED=true` in `/opt/talky/backend/.env`). Zero effect for `none`-mode campaigns (returns before any DB call). 8 new unit tests; ruff-F clean. Verified on prod: ingested a 10-node KB into campaign 5f47a5fa → injection bakes it into the prompt; per-turn retrieve resolves all sample questions. **Pending: a human-answered live call for final acceptance.**
- **P3 — frontend tree: ✅ DONE (2026-06-04).** `Talk-Leee/src/components/campaigns/knowledge-panel.tsx` on the campaign detail page: upload .md/.txt, mode badge (inline/map_retrieve/retrieve) w/ explanation, sources list (status + delete), collapsible/indented node tree showing spoken answer + keyword chips + hit_count analytics, per-node enable/disable + pin (priority) + inline-edit of the spoken answer (optimistic + rollback). Renders nothing when the backend flag is off (GET 404s). API methods added to `src/lib/api.ts`. tsc clean, eslint clean, `next build` OK. Frontend = Vercel project `talkleeai` (NOT in the backend git-deploy; deploys via Vercel).
- **P4 — creation wizard: ✅ DONE (2026-06-04).** Backend: additive **`knowledge_driven`** flag (default false → existing slot-based campaigns unchanged) threaded through `composer.compose_prompt` (lean identity+tone body, skips required content slots), `campaign_prompt_service` (persists flag in script_config), Create/Update/Preview schemas + endpoints, and per-call `telephony_session_config`. Backend live on prod. Frontend: `Talk-Leee/src/components/campaigns/campaign-wizard.tsx` — 3-step (Basics: name/company/persona/agents/voice/goal → Knowledge: upload .md/.txt → Review: prompt+greeting preview via `previewCampaignPrompt(knowledge_driven)`), creates with `knowledge_driven:true` then chains the knowledge upload. Mounted at `/campaigns/new` (default; toggle to the classic `CampaignForm` for power users / editing). 10 new backend unit tests; tsc + eslint clean.
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

## 13. Resolved follow-ups + smart capabilities (maxed)

User direction: maximise capability, don't limit features. Resolutions:

**A. Inline budget → model-aware, computed, not a fixed number.**
Maintain a small model registry `{model: context_window}` (e.g. `llama-3.1-8b-instant: 8192`, `llama-3.3-70b-versatile: 131072`). At session build compute:
`available = context_window − reserved(persona ≈ 600 + history budget + response 400 + safety 200)`, `inline_budget = floor(available × 0.6)`.
Then pick mode automatically: KB ≤ `inline_budget` → **inline**; KB ≤ ~4× budget → **map+retrieve** (skeleton always inlined + per-turn detail); else → **retrieve**. So the system uses the *most* context the model safely allows and scales up by itself when a campaign runs a bigger model. (For the current 8B/8k model that lands ≈ 2.5–2.7k inline tokens — but it's derived, so upgrading the model needs zero code change.)

**B. Per-campaign model selection (new capability).** Add `campaigns.knowledge_model` (nullable). Knowledge-heavy campaigns can opt into a large-context model (e.g. 128k) for inline; default stays the fast 8B. Honest trade-off documented in the UI: bigger context = higher latency/cost, so it's opt-in, and **retrieve mode means even the 8B handles unlimited-size KBs** without it.

**C. Upload → generous + multi-source + multi-format.**
- **Per-file cap 10 MB** (text is tiny; this is effectively "no limit" for real docs), **multiple sources per campaign** (a real knowledge *base*, not one file), async chunked ingest with concurrent batched enrichment so large docs don't block.
- Formats: `.md`/`.txt` at launch; `.pdf`/`.docx` extraction as a fast-follow (text-extract → same pipeline). **No cap on total KB size per campaign** — retrieve mode is size-independent.
- **Versioning:** re-upload creates a new source version; previous kept; one-click rollback.

**D. Wizard → replaces `campaign-form.tsx`, full-featured.** A single smart multi-step flow that preserves every current field and adds: knowledge upload + live tree review/edit, **per-node test ("ask a question → see which nodes light up + the exact injected block")**, and a **preview test-chat** before going live. Old form retired (not duplicated) to avoid two drifting code paths.

**Extra smart capabilities folded in (because they make it genuinely capable):**
- **Hybrid FTS + trigram retrieval** (§6) — robust to STT mishears; the single most impactful upgrade for voice.
- **`voice_answer` per node** — enrichment writes a spoken-style 1–2 sentence answer, so injected knowledge sounds natural, not like a pasted doc.
- **Knowledge analytics** — `hit_count` per node → frontend shows "most-asked topics" and "unused knowledge" so owners can prune/expand; feeds a feedback loop for better scripts.
- **Pinning / priority** — `priority` column lets an owner force-include critical nodes (compliance lines, current promo) regardless of match score.
- **Query expansion (cheap, optional)** — expand the transcript with persona/domain synonyms before the query for better recall, no extra LLM call (use the node `keywords` corpus).
- **Branch router (promoted from optional)** — a single cheap branch-prediction call at first relevant turn, **cached per call**, runs in parallel with first-token so it adds no perceived latency; improves recall on vague/multi-topic asks. On by default in `map+retrieve`/`retrieve`, disable-able.

**Net:** unlimited KB size (via retrieve), max safe inline per model (derived), multi-file multi-format versioned knowledge base, STT-robust hybrid retrieval, voice-optimised answers, analytics + pinning, and a single capable wizard. The only inherent limit is the chosen model's context window for *inline* — and that's handled by auto-mode + optional bigger model + size-independent retrieve.
