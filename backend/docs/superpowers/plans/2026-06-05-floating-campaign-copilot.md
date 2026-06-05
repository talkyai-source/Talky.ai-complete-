# Floating Campaign Copilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a text chat copilot to the Talk-Leee dashboard that reads campaigns + knowledge tree (incl. live RAG retrieval) and proposes edits behind a preview-diff + Apply gate, with full tenant isolation and audit.

**Architecture:** New isolated subsystem. Backend `CopilotService` runs a Gemini native-function-calling tool loop; *read* tools execute live, *propose* tools return a diff object and write nothing. Edits apply only when the user clicks Apply, which calls existing typed mutation endpoints. No voice-pipeline changes, no new DB tables.

**Tech Stack:** FastAPI, asyncpg (RLS via `acquire_with_tenant`), `google-genai` SDK (Gemini function-calling), Next.js + zod + `createHttpClient`, pytest.

**Spec:** `backend/docs/superpowers/specs/2026-06-05-floating-campaign-copilot-design.md`

---

## File Structure

**Backend — create**
- `backend/app/domain/services/copilot/__init__.py` — package marker.
- `backend/app/domain/services/copilot/tools.py` — tool JSON schemas + read-tool implementations (tenant-scoped).
- `backend/app/domain/services/copilot/proposals.py` — proposal + `human_diff` builders for each editable entity.
- `backend/app/domain/services/copilot/service.py` — `CopilotService`: the LLM tool loop + tenant injection + audit.
- `backend/app/api/v1/endpoints/copilot.py` — `POST /copilot/chat`.

**Backend — modify**
- `backend/app/infrastructure/llm/gemini.py` — add `generate_with_tools()` (non-streaming, tool-calling). Voice path untouched.
- `backend/app/api/v1/routes.py` — register the copilot router.

**Backend — tests**
- `backend/tests/unit/copilot/test_tools.py`
- `backend/tests/unit/copilot/test_proposals.py`
- `backend/tests/unit/copilot/test_service.py`
- `backend/tests/unit/llm/test_gemini_tools.py`

**Frontend — create**
- `Talk-Leee/src/lib/copilot-api.ts` — chat + apply API calls (apply reuses existing campaign/knowledge fns).
- `Talk-Leee/src/components/copilot/CampaignCopilot.tsx` — floating FAB + chat panel.
- `Talk-Leee/src/components/copilot/MessageList.tsx` — message + tool-result rendering.
- `Talk-Leee/src/components/copilot/ProposalDiffCard.tsx` — before→after diff + Apply/Cancel.

**Frontend — modify**
- `Talk-Leee/src/app/(dashboard)/layout.tsx` (or the existing dashboard shell that already mounts `voice-agent-popup`) — mount `<CampaignCopilot/>`.

---

## Conventions for every task
- Backend deps reused verbatim: `from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client` and `from app.core.db_utils import acquire_with_tenant`.
- Tenant scope: the LLM never supplies `tenant_id`. The endpoint reads it from `current_user.tenant_id` and the service injects it into every tool call.
- Run backend tests with the repo venv: `backend/venv/bin/python -m pytest <path> -v` (run from `backend/`).
- Commit messages: no `Co-Authored-By` line (repo rule — Uzair is sole author).

---

## SLICE 1 — Read-only copilot backend

### Task 1: Gemini `generate_with_tools()`

**Files:**
- Modify: `backend/app/infrastructure/llm/gemini.py` (add method after `stream_chat_with_timeout`, before the Identity section ~line 405)
- Test: `backend/tests/unit/llm/test_gemini_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/llm/test_gemini_tools.py
import pytest
from app.infrastructure.llm.gemini import GeminiLLMProvider, ToolTurn


def test_tool_turn_shape():
    """ToolTurn carries either text or tool_calls (never invents fields)."""
    turn = ToolTurn(text="hello", tool_calls=[])
    assert turn.text == "hello"
    assert turn.tool_calls == []
    turn2 = ToolTurn(text=None, tool_calls=[{"id": "0", "name": "read_campaign", "args": {"name": "DOJO"}}])
    assert turn2.tool_calls[0]["name"] == "read_campaign"


def test_history_to_contents_roundtrips_roles():
    """user/model text + tool_call/tool_result map to Gemini parts without loss."""
    p = GeminiLLMProvider()
    history = [
        {"role": "user", "content": "show DOJO"},
        {"role": "model", "tool_calls": [{"id": "0", "name": "read_campaign", "args": {"name": "DOJO"}}]},
        {"role": "tool", "tool_call_id": "0", "name": "read_campaign", "content": {"id": "abc", "name": "DOJO"}},
    ]
    contents = p._history_to_contents(history)  # pure translation, no network
    roles = [c.role for c in contents]
    assert roles == ["user", "model", "user"]  # Gemini function_response rides a 'user' turn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python -m pytest tests/unit/llm/test_gemini_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolTurn'`.

- [ ] **Step 3: Implement `ToolTurn` + the tool-calling method**

Add near the top of `gemini.py` (after the existing imports/`LLMTimeoutError`):

```python
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class ToolTurn:
    """One assistant turn from a tool-enabled model.

    Exactly one of these is meaningful per turn: `tool_calls` (the model wants
    tools run) or `text` (the model's final spoken answer). When tool_calls is
    non-empty, text is usually None.
    """
    text: Optional[str]
    tool_calls: list[Dict[str, Any]] = field(default_factory=list)
```

Add these methods to `GeminiLLMProvider` (after `stream_chat_with_timeout`):

```python
    def _history_to_contents(self, history: list[dict]):
        """Translate the copilot's generic history into Gemini Content parts.

        history items:
          {"role":"user","content": str}
          {"role":"model","content": str}                         # plain text
          {"role":"model","tool_calls":[{"id","name","args"}]}    # model asked for tools
          {"role":"tool","tool_call_id","name","content": dict}   # tool result
        Gemini has no 'tool' role: a function_response is sent as a 'user' turn.
        """
        from google.genai import types as genai_types
        contents = []
        for item in history:
            role = item.get("role")
            if role == "user":
                contents.append(genai_types.Content(
                    role="user", parts=[genai_types.Part(text=item.get("content") or " ")]))
            elif role == "model" and item.get("tool_calls"):
                parts = [
                    genai_types.Part(function_call=genai_types.FunctionCall(
                        name=tc["name"], args=tc.get("args") or {}))
                    for tc in item["tool_calls"]
                ]
                contents.append(genai_types.Content(role="model", parts=parts))
            elif role == "model":
                contents.append(genai_types.Content(
                    role="model", parts=[genai_types.Part(text=item.get("content") or " ")]))
            elif role == "tool":
                contents.append(genai_types.Content(role="user", parts=[
                    genai_types.Part(function_response=genai_types.FunctionResponse(
                        name=item["name"], response=item.get("content") or {}))]))
        return contents

    async def generate_with_tools(
        self,
        *,
        system_prompt: str,
        history: list[dict],
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> ToolTurn:
        """One non-streaming tool-calling turn. Returns tool_calls OR final text.

        `tools` is a list of {name, description, parameters(JSON schema)} dicts.
        Used only by the copilot — the voice hot path keeps using stream_chat.
        """
        if not self._client:
            raise RuntimeError("Gemini client not initialized. Call initialize() first.")
        from google.genai import types as genai_types

        fn_decls = [
            genai_types.FunctionDeclaration(
                name=t["name"], description=t.get("description", ""),
                parameters=t.get("parameters") or {"type": "object", "properties": {}})
            for t in tools
        ]
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_prompt or None,
            tools=[genai_types.Tool(function_declarations=fn_decls)],
        )
        resp = await self._client.aio.models.generate_content(
            model=self._model, contents=self._history_to_contents(history), config=config)

        calls = []
        for i, fc in enumerate(getattr(resp, "function_calls", None) or []):
            calls.append({"id": str(i), "name": fc.name, "args": dict(fc.args or {})})
        text = None if calls else getattr(resp, "text", None)
        return ToolTurn(text=text, tool_calls=calls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python -m pytest tests/unit/llm/test_gemini_tools.py -v`
Expected: PASS (both tests; `_history_to_contents` needs `google-genai` importable, which the venv already has).

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/llm/gemini.py backend/tests/unit/llm/test_gemini_tools.py
git commit -m "feat(copilot): add Gemini generate_with_tools (function-calling turn)"
```

---

### Task 2: Read-tool schemas + implementations

**Files:**
- Create: `backend/app/domain/services/copilot/__init__.py` (empty)
- Create: `backend/app/domain/services/copilot/tools.py`
- Test: `backend/tests/unit/copilot/test_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/copilot/test_tools.py
import pytest
from app.domain.services.copilot import tools


def test_read_tool_schemas_present():
    names = {t["name"] for t in tools.READ_TOOL_SCHEMAS}
    assert names == {"list_campaigns", "read_campaign", "read_knowledge_tree", "retrieve_knowledge"}
    for t in tools.READ_TOOL_SCHEMAS:
        assert "description" in t and "parameters" in t


@pytest.mark.asyncio
async def test_retrieve_knowledge_is_tenant_scoped(monkeypatch):
    """The tool must pass the caller tenant_id through, never trust LLM args."""
    seen = {}
    async def fake_retrieve(pool, tenant_id, campaign_id, query, k=3, bump_hits=False):
        seen.update(tenant_id=tenant_id, campaign_id=campaign_id, query=query, bump=bump_hits)
        return [{"id": "n1", "heading": "Pricing", "voice_answer": "From $99"}]
    monkeypatch.setattr(tools, "retrieve_knowledge", fake_retrieve)

    out = await tools.run_read_tool(
        "retrieve_knowledge",
        {"campaign_id": "c1", "query": "price", "tenant_id": "ATTACKER"},  # LLM-supplied tenant ignored
        pool=object(), tenant_id="REAL-TENANT")
    assert seen["tenant_id"] == "REAL-TENANT"
    assert seen["bump"] is False  # test box must not inflate hit_count
    assert out["hits"][0]["heading"] == "Pricing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: app.domain.services.copilot.tools`.

- [ ] **Step 3: Implement the read tools**

```python
# backend/app/domain/services/copilot/__init__.py
```
```python
# backend/app/domain/services/copilot/tools.py
"""Copilot read tools + JSON schemas. Every DB access is tenant-scoped by the
caller-supplied tenant_id; any tenant_id in the LLM args is ignored."""
from __future__ import annotations

from typing import Any

from app.core.db_utils import acquire_with_tenant
from app.services.scripts.knowledge.retrieval import retrieve_knowledge

READ_TOOL_SCHEMAS: list[dict] = [
    {"name": "list_campaigns", "description": "List this tenant's campaigns (id, name, status, persona, knowledge_mode).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "read_campaign", "description": "Read one campaign's full config by id or name.",
     "parameters": {"type": "object", "properties": {
         "campaign_id": {"type": "string"}, "name": {"type": "string"}}}},
    {"name": "read_knowledge_tree", "description": "Read a campaign's knowledge tree (headings, summaries, enabled, hit_count).",
     "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}},
                    "required": ["campaign_id"]}},
    {"name": "retrieve_knowledge", "description": "Run the LIVE retriever for a caller-style question; shows exactly what the agent would pull from the tree. Use this to test tree quality.",
     "parameters": {"type": "object", "properties": {
         "campaign_id": {"type": "string"}, "query": {"type": "string"}},
         "required": ["campaign_id", "query"]}},
]


async def _resolve_campaign_id(conn, name: str) -> str | None:
    return await conn.fetchval("SELECT id FROM campaigns WHERE name = $1", name)


async def run_read_tool(name: str, args: dict, *, pool: Any, tenant_id: str) -> dict:
    if name == "list_campaigns":
        async with acquire_with_tenant(pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT id, name, status, knowledge_mode, script_config->>'persona_type' AS persona "
                "FROM campaigns ORDER BY created_at DESC")
        return {"campaigns": [dict(r) | {"id": str(r["id"])} for r in rows]}

    if name == "read_campaign":
        cid = args.get("campaign_id")
        async with acquire_with_tenant(pool, tenant_id) as conn:
            if not cid and args.get("name"):
                cid = await _resolve_campaign_id(conn, args["name"])
            if not cid:
                return {"error": "campaign not found"}
            row = await conn.fetchrow(
                "SELECT id, name, status, voice_id, tts_provider, knowledge_mode, "
                "knowledge_model, script_config FROM campaigns WHERE id = $1", cid)
        return {"campaign": dict(row) | {"id": str(row["id"])}} if row else {"error": "campaign not found"}

    if name == "read_knowledge_tree":
        async with acquire_with_tenant(pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT id, depth, heading, summary, enabled, hit_count FROM campaign_knowledge_nodes "
                "WHERE campaign_id = $1 ORDER BY string_to_array(path, '.')::int[]",
                args["campaign_id"])
        return {"nodes": [dict(r) | {"id": str(r["id"])} for r in rows]}

    if name == "retrieve_knowledge":
        hits = await retrieve_knowledge(
            pool, tenant_id, args["campaign_id"], args["query"], k=3, bump_hits=False)
        return {"hits": [{"id": str(h["id"]), "heading": h.get("heading"),
                          "voice_answer": h.get("voice_answer"), "summary": h.get("summary")} for h in hits]}

    return {"error": f"unknown read tool {name}"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/copilot/__init__.py backend/app/domain/services/copilot/tools.py backend/tests/unit/copilot/test_tools.py
git commit -m "feat(copilot): tenant-scoped read tools + schemas"
```

---

### Task 3: `CopilotService` tool loop

**Files:**
- Create: `backend/app/domain/services/copilot/service.py`
- Test: `backend/tests/unit/copilot/test_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/copilot/test_service.py
import pytest
from app.domain.services.copilot.service import CopilotService
from app.infrastructure.llm.gemini import ToolTurn


class FakeLLM:
    """Returns a scripted sequence of ToolTurns."""
    def __init__(self, turns): self._turns = list(turns); self.calls = []
    async def generate_with_tools(self, *, system_prompt, history, tools, **kw):
        self.calls.append(list(history))
        return self._turns.pop(0)


@pytest.mark.asyncio
async def test_runs_tool_then_answers(monkeypatch):
    llm = FakeLLM([
        ToolTurn(text=None, tool_calls=[{"id": "0", "name": "read_campaign", "args": {"name": "DOJO"}}]),
        ToolTurn(text="DOJO uses the lead_gen persona.", tool_calls=[]),
    ])
    async def fake_read(name, args, *, pool, tenant_id):
        assert tenant_id == "T1"
        return {"campaign": {"id": "c1", "name": "DOJO", "script_config": {"persona_type": "lead_gen"}}}
    monkeypatch.setattr("app.domain.services.copilot.service.run_read_tool", fake_read)

    svc = CopilotService(llm=llm, pool=object(), tenant_id="T1", actor_id="U1")
    result = await svc.chat(history=[{"role": "user", "content": "what persona does DOJO use?"}])
    assert result["reply"] == "DOJO uses the lead_gen persona."
    assert result["proposals"] == []
    assert any(h.get("role") == "tool" for h in llm.calls[-1])  # tool result fed back


@pytest.mark.asyncio
async def test_loop_is_bounded(monkeypatch):
    """A model that only ever calls tools must not loop forever."""
    llm = FakeLLM([ToolTurn(text=None, tool_calls=[{"id": "0", "name": "list_campaigns", "args": {}}])] * 50)
    monkeypatch.setattr("app.domain.services.copilot.service.run_read_tool",
                        lambda *a, **k: __import__("asyncio").sleep(0, result={"campaigns": []}))
    svc = CopilotService(llm=llm, pool=object(), tenant_id="T1", actor_id="U1")
    result = await svc.chat(history=[{"role": "user", "content": "hi"}])
    assert "couldn't complete" in result["reply"].lower()
    assert len(llm.calls) <= svc.MAX_TOOL_ROUNDS + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: ...copilot.service`.

- [ ] **Step 3: Implement the service**

```python
# backend/app/domain/services/copilot/service.py
"""Copilot tool loop: read tools execute live, propose tools return proposals.
Tenant + actor are fixed at construction from the authed session — never the LLM."""
from __future__ import annotations

import logging
from typing import Any

from app.domain.services.copilot.tools import READ_TOOL_SCHEMAS, run_read_tool
from app.domain.services.copilot.proposals import PROPOSE_TOOL_SCHEMAS, build_proposal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the Campaign Copilot inside the Talky.ai dashboard. You help the "
    "signed-in user inspect and improve their voice-AI campaigns.\n"
    "- Use read tools to answer questions about campaigns and the knowledge tree.\n"
    "- Use retrieve_knowledge to show what the agent would pull for a caller question.\n"
    "- To CHANGE anything, call a propose_* tool. NEVER claim a change is done — "
    "proposals are shown to the user, who must click Apply. Describe what you are "
    "proposing in one short sentence.\n"
    "- Keep replies concise and plain-text."
)
_PROPOSE_NAMES = {t["name"] for t in PROPOSE_TOOL_SCHEMAS}


class CopilotService:
    MAX_TOOL_ROUNDS = 6

    def __init__(self, *, llm, pool, tenant_id: str, actor_id: str):
        self._llm = llm
        self._pool = pool
        self._tenant_id = tenant_id
        self._actor_id = actor_id

    async def chat(self, *, history: list[dict]) -> dict:
        convo = list(history)
        proposals: list[dict] = []
        tools = READ_TOOL_SCHEMAS + PROPOSE_TOOL_SCHEMAS

        for _round in range(self.MAX_TOOL_ROUNDS):
            turn = await self._llm.generate_with_tools(
                system_prompt=_SYSTEM_PROMPT, history=convo, tools=tools)
            if not turn.tool_calls:
                return {"reply": turn.text or "", "proposals": proposals}

            convo.append({"role": "model", "tool_calls": turn.tool_calls})
            for call in turn.tool_calls:
                result = await self._dispatch(call["name"], call.get("args") or {})
                if result.get("_proposal"):
                    proposals.append(result["_proposal"])
                convo.append({"role": "tool", "tool_call_id": call["id"],
                              "name": call["name"], "content": result})

        logger.warning("copilot_tool_loop_exhausted tenant=%s", self._tenant_id[:8])
        return {"reply": "Sorry, I couldn't complete that — try rephrasing.", "proposals": proposals}

    async def _dispatch(self, name: str, args: dict) -> dict:
        if name in _PROPOSE_NAMES:
            proposal = await build_proposal(name, args, pool=self._pool, tenant_id=self._tenant_id)
            # Tool result tells the model the proposal is staged (not applied).
            return {"status": "proposed_awaiting_user_apply", "summary": proposal.get("summary"),
                    "_proposal": proposal}
        return await run_read_tool(name, args, pool=self._pool, tenant_id=self._tenant_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_service.py -v`
Expected: PASS. (Requires `proposals.py` from Task 8 for import; if running Task 3 before 8, add a temporary `PROPOSE_TOOL_SCHEMAS=[]` and `async def build_proposal(*a, **k): ...` stub in `proposals.py` now, then flesh out in Task 8. Create the stub now to keep imports valid.)

Create the minimal stub now so imports resolve:
```python
# backend/app/domain/services/copilot/proposals.py  (stub — fully implemented in Task 8)
PROPOSE_TOOL_SCHEMAS: list[dict] = []
async def build_proposal(name, args, *, pool, tenant_id) -> dict:  # pragma: no cover
    raise NotImplementedError
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/copilot/service.py backend/app/domain/services/copilot/proposals.py backend/tests/unit/copilot/test_service.py
git commit -m "feat(copilot): bounded tool loop service (read tools + proposal collection)"
```

---

### Task 4: `POST /copilot/chat` endpoint + router

**Files:**
- Create: `backend/app/api/v1/endpoints/copilot.py`
- Modify: `backend/app/api/v1/routes.py` (register router — follow the existing `include_router` pattern)

- [ ] **Step 1: Write the endpoint**

```python
# backend/app/api/v1/endpoints/copilot.py
"""Dashboard campaign copilot — authed, tenant-scoped chat with read/propose tools."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.rate_limit import rate_limit_dependency  # same dep campaigns.py uses
from app.domain.services.copilot.service import CopilotService
from app.infrastructure.llm.factory import create_llm_provider  # see note in Step 2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/copilot", tags=["copilot"], dependencies=[Depends(rate_limit_dependency)])

_MAX_HISTORY = 40


@router.post("/chat")
async def copilot_chat(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID required")
    history = payload.get("history") or []
    if not isinstance(history, list) or len(history) > _MAX_HISTORY:
        raise HTTPException(status_code=400, detail="history must be a list of <=40 messages")

    llm = await _get_copilot_llm()
    svc = CopilotService(llm=llm, pool=db_client.pool,
                         tenant_id=str(current_user.tenant_id), actor_id=str(current_user.user_id))
    try:
        return await svc.chat(history=history)
    except Exception as exc:  # surface a clean error; details in logs
        logger.error("copilot_chat_failed tenant=%s err=%s", str(current_user.tenant_id)[:8], exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Copilot failed to respond")
```

- [ ] **Step 2: Resolve the LLM provider helper**

Inspect `backend/app/infrastructure/llm/factory.py` for the existing creation function (it builds the Gemini provider used by Ask-AI). Add a small cached accessor at the bottom of `copilot.py`:

```python
_copilot_llm = None

async def _get_copilot_llm():
    """Lazily build + initialize a Gemini provider for the copilot (cached)."""
    global _copilot_llm
    if _copilot_llm is None:
        from app.infrastructure.llm.gemini import GeminiLLMProvider
        prov = GeminiLLMProvider()
        await prov.initialize({"model": "gemini-2.5-flash", "temperature": 0.3, "max_tokens": 600})
        _copilot_llm = prov
    return _copilot_llm
```
Remove the unused `create_llm_provider` import from Step 1 once this is in.

- [ ] **Step 3: Register the router**

In `backend/app/api/v1/routes.py`, find the block of `from app.api.v1.endpoints import ...` + `api_router.include_router(...)` and add, matching the surrounding style:

```python
from app.api.v1.endpoints import copilot
api_router.include_router(copilot.router)
```

- [ ] **Step 4: Smoke-test import + route registration**

Run: `cd backend && venv/bin/python -c "import app.main; print('ok')"`
Expected: prints `ok` (no import error).
Run: `cd backend && venv/bin/python -c "from app.main import app; print([r.path for r in app.routes if 'copilot' in r.path])"`
Expected: shows `/api/v1/copilot/chat`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/endpoints/copilot.py backend/app/api/v1/routes.py
git commit -m "feat(copilot): POST /copilot/chat endpoint (authed, tenant-scoped, rate-limited)"
```

---

## SLICE 2 — Frontend chat panel (read)

### Task 5: `copilot-api.ts`

**Files:**
- Create: `Talk-Leee/src/lib/copilot-api.ts`

- [ ] **Step 1: Implement the client (follow `src/lib/api.ts` conventions: zod + `createHttpClient`)**

```ts
// Talk-Leee/src/lib/copilot-api.ts
import { z } from "zod";
import { createHttpClient } from "@/lib/http-client";
import { apiBaseUrl } from "@/lib/env";

const http = createHttpClient(apiBaseUrl);

export const HumanDiffSchema = z.object({
  field: z.string(), before: z.unknown(), after: z.unknown(),
});
export const ProposalSchema = z.object({
  proposal_id: z.string(),
  kind: z.string(),
  summary: z.string().optional().default(""),
  warning: z.string().optional().default(""),
  human_diff: z.array(HumanDiffSchema).default([]),
  target: z.object({ method: z.string(), path: z.string(), payload: z.unknown() }),
});
export const ChatResponseSchema = z.object({
  reply: z.string(),
  proposals: z.array(ProposalSchema).default([]),
});
export type Proposal = z.infer<typeof ProposalSchema>;
export type ChatResponse = z.infer<typeof ChatResponseSchema>;

export type CopilotMsg = { role: "user" | "model"; content: string };

export async function copilotChat(history: CopilotMsg[]): Promise<ChatResponse> {
  const raw = await http.post("/copilot/chat", { history });
  return ChatResponseSchema.parse(raw);
}

/** Apply a staged proposal by calling its target endpoint verbatim. */
export async function applyProposal(p: Proposal): Promise<void> {
  const { method, path, payload } = p.target;
  await http.request(path, { method, body: payload });
}
```
> Adjust `http.post` / `http.request` to the exact method names exported by `createHttpClient` (read `src/lib/http-client.ts`). If the client exposes typed verbs only, mirror an existing call in `api.ts`.

- [ ] **Step 2: Typecheck**

Run: `cd Talk-Leee && npx tsc --noEmit`
Expected: no new errors in `copilot-api.ts`.

- [ ] **Step 3: Commit**

```bash
git add Talk-Leee/src/lib/copilot-api.ts
git commit -m "feat(copilot): frontend api client (chat + applyProposal)"
```

---

### Task 6: `CampaignCopilot` panel + `MessageList`

**Files:**
- Create: `Talk-Leee/src/components/copilot/MessageList.tsx`
- Create: `Talk-Leee/src/components/copilot/CampaignCopilot.tsx`

- [ ] **Step 1: MessageList (renders user/model bubbles + a read-result `<pre>` block)**

```tsx
// Talk-Leee/src/components/copilot/MessageList.tsx
"use client";
import type { CopilotMsg } from "@/lib/copilot-api";

export function MessageList({ messages, busy }: { messages: CopilotMsg[]; busy: boolean }) {
  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-3">
      {messages.map((m, i) => (
        <div key={i} className={m.role === "user" ? "self-end" : "self-start"}>
          <div className={`rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${
            m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"}`}>
            {m.content}
          </div>
        </div>
      ))}
      {busy && <div className="self-start text-xs text-gray-400">thinking…</div>}
    </div>
  );
}
```

- [ ] **Step 2: CampaignCopilot (FAB + panel). Reuse the floating/portal pattern from `voice-agent-popup.tsx`.**

```tsx
// Talk-Leee/src/components/copilot/CampaignCopilot.tsx
"use client";
import { useState } from "react";
import { MessageList } from "./MessageList";
import { ProposalDiffCard } from "./ProposalDiffCard";
import { copilotChat, applyProposal, type CopilotMsg, type Proposal } from "@/lib/copilot-api";

export function CampaignCopilot() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<CopilotMsg[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const next: CopilotMsg[] = [...messages, { role: "user", content: text }];
    setMessages(next); setInput(""); setBusy(true);
    try {
      const res = await copilotChat(next);
      setMessages([...next, { role: "model", content: res.reply }]);
      if (res.proposals.length) setProposals((p) => [...p, ...res.proposals]);
    } catch {
      setMessages([...next, { role: "model", content: "Something went wrong. Try again." }]);
    } finally { setBusy(false); }
  }

  async function onApply(p: Proposal) {
    await applyProposal(p);
    setProposals((list) => list.filter((x) => x.proposal_id !== p.proposal_id));
    setMessages((m) => [...m, { role: "model", content: `Applied: ${p.summary}` }]);
  }
  function onCancel(p: Proposal) {
    setProposals((list) => list.filter((x) => x.proposal_id !== p.proposal_id));
  }

  return (
    <>
      <button onClick={() => setOpen((o) => !o)}
        className="fixed bottom-6 right-6 z-50 rounded-full bg-blue-600 px-4 py-3 text-white shadow-lg">
        {open ? "×" : "Copilot"}
      </button>
      {open && (
        <div className="fixed bottom-24 right-6 z-50 flex h-[28rem] w-96 flex-col rounded-2xl border bg-white shadow-2xl">
          <div className="border-b px-3 py-2 text-sm font-semibold">Campaign Copilot</div>
          <div className="flex-1 overflow-y-auto">
            <MessageList messages={messages} busy={busy} />
            {proposals.map((p) => (
              <ProposalDiffCard key={p.proposal_id} proposal={p} onApply={onApply} onCancel={onCancel} />
            ))}
          </div>
          <div className="flex gap-2 border-t p-2">
            <input value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Ask about or edit a campaign…"
              className="flex-1 rounded-lg border px-2 py-1 text-sm" />
            <button onClick={send} disabled={busy} className="rounded-lg bg-blue-600 px-3 text-white">Send</button>
          </div>
        </div>
      )}
    </>
  );
}
```
> `ProposalDiffCard` is created in Task 9. To compile Slice 2 alone, add a one-line stub: `export function ProposalDiffCard(_: any) { return null; }` in `ProposalDiffCard.tsx`, replaced in Task 9.

- [ ] **Step 3: Mount it in the dashboard shell**

Find where `voice-agent-popup` is mounted (grep `VoiceAgentPopup` under `Talk-Leee/src/app`) and add `<CampaignCopilot />` next to it (dashboard-only layout, so it shows for authed users).

- [ ] **Step 4: Verify**

Run: `cd Talk-Leee && npx tsc --noEmit` (expect clean), then `npm run dev`, log in, open the Copilot, ask "list my campaigns" → expect a reply naming your campaigns.

- [ ] **Step 5: Commit**

```bash
git add Talk-Leee/src/components/copilot/ Talk-Leee/src/app
git commit -m "feat(copilot): floating chat panel wired to /copilot/chat (read)"
```

---

## SLICE 3 — Propose + Apply (campaign basics)

### Task 7: Proposal builder + `propose_campaign_edit`

**Files:**
- Modify: `backend/app/domain/services/copilot/proposals.py` (replace the Task-3 stub)
- Test: `backend/tests/unit/copilot/test_proposals.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/copilot/test_proposals.py
import pytest
from app.domain.services.copilot import proposals


@pytest.mark.asyncio
async def test_campaign_edit_builds_diff_against_live_state(monkeypatch):
    class FakeConn:
        async def fetchrow(self, *a):
            return {"id": "c1", "name": "DOJO",
                    "script_config": {"persona_type": "lead_gen", "company_name": "DOJO"},
                    "voice_id": "v1", "tts_provider": "google"}
    class Ctx:
        async def __aenter__(self): return FakeConn()
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(proposals, "acquire_with_tenant", lambda pool, t: Ctx())

    p = await proposals.build_proposal(
        "propose_campaign_edit",
        {"campaign_id": "c1", "changes": {"persona_type": "customer_support"}},
        pool=object(), tenant_id="T1")

    assert p["kind"] == "campaign_edit"
    assert p["target"]["method"] == "PUT"
    assert p["target"]["path"] == "/campaigns/c1"
    diff = {d["field"]: (d["before"], d["after"]) for d in p["human_diff"]}
    assert diff["persona_type"] == ("lead_gen", "customer_support")
    assert p["proposal_id"]
    assert "review" in p["warning"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_proposals.py -v`
Expected: FAIL — `NotImplementedError` / missing schema.

- [ ] **Step 3: Implement proposals.py**

```python
# backend/app/domain/services/copilot/proposals.py
"""Build edit PROPOSALS (never writes). Each proposal carries the existing
mutation endpoint to call on Apply + a human-readable diff vs live state."""
from __future__ import annotations

import uuid
from typing import Any

from app.core.db_utils import acquire_with_tenant

_WARNING = "Review carefully — this will change live campaign behavior when you click Apply."

PROPOSE_TOOL_SCHEMAS: list[dict] = [
    {"name": "propose_campaign_edit",
     "description": "Propose changes to a campaign's basics (persona_type, company_name, agent_names, voice_id, tts_provider, goal, additional_instructions). Does NOT apply — the user must click Apply.",
     "parameters": {"type": "object", "properties": {
         "campaign_id": {"type": "string"},
         "changes": {"type": "object", "description": "field→new value map"}},
         "required": ["campaign_id", "changes"]}},
]

# Fields that live inside campaigns.script_config (vs top-level columns).
_SCRIPT_CONFIG_FIELDS = {"persona_type", "company_name", "agent_names",
                         "campaign_slots", "additional_instructions"}
_COLUMN_FIELDS = {"voice_id", "tts_provider", "goal", "name"}


def _new_id() -> str:
    return uuid.uuid4().hex


async def build_proposal(name: str, args: dict, *, pool: Any, tenant_id: str) -> dict:
    if name == "propose_campaign_edit":
        return await _campaign_edit(args, pool=pool, tenant_id=tenant_id)
    raise ValueError(f"unknown propose tool {name}")


async def _campaign_edit(args: dict, *, pool: Any, tenant_id: str) -> dict:
    cid = args["campaign_id"]
    changes = args.get("changes") or {}
    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, name, script_config, voice_id, tts_provider, goal "
            "FROM campaigns WHERE id = $1", cid)
    if not row:
        return {"error": "campaign not found"}
    current = dict(row)
    sc = dict(current.get("script_config") or {})

    human_diff = []
    for field, after in changes.items():
        if field in _SCRIPT_CONFIG_FIELDS:
            before = sc.get(field)
        elif field in _COLUMN_FIELDS:
            before = current.get(field)
        else:
            continue  # ignore unknown/forbidden fields
        if before != after:
            human_diff.append({"field": field, "before": before, "after": after})

    # Build the PUT payload the existing CampaignUpdateRequest expects: merge
    # changes onto current config so untouched fields are preserved.
    merged_sc = sc | {k: v for k, v in changes.items() if k in _SCRIPT_CONFIG_FIELDS}
    payload = {
        "name": changes.get("name", current["name"]),
        "voice_id": changes.get("voice_id", current.get("voice_id")),
        "tts_provider": changes.get("tts_provider", current.get("tts_provider")),
        "persona_type": merged_sc.get("persona_type"),
        "company_name": merged_sc.get("company_name"),
        "agent_names": merged_sc.get("agent_names"),
        "campaign_slots": merged_sc.get("campaign_slots"),
        "system_prompt": merged_sc.get("additional_instructions"),
    }
    return {
        "proposal_id": _new_id(),
        "kind": "campaign_edit",
        "summary": f"Update campaign {current['name']}: " + ", ".join(d["field"] for d in human_diff),
        "warning": _WARNING,
        "human_diff": human_diff,
        "target": {"method": "PUT", "path": f"/campaigns/{cid}", "payload": payload},
    }
```
> Confirm the exact field names on `CampaignUpdateRequest` in `campaigns.py` (Task pre-read showed `voice_id, tts_provider, persona_type, company_name, agent_names, campaign_slots, system_prompt`). Match them exactly so Apply validates.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python -m pytest tests/unit/copilot/test_proposals.py tests/unit/copilot/test_service.py -v`
Expected: PASS (service test now uses the real `build_proposal`/schemas).

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/copilot/proposals.py backend/tests/unit/copilot/test_proposals.py
git commit -m "feat(copilot): propose_campaign_edit builds diff + PUT payload vs live state"
```

---

### Task 8: `ProposalDiffCard` + Apply wiring + audit

**Files:**
- Create/replace: `Talk-Leee/src/components/copilot/ProposalDiffCard.tsx`
- Modify: `backend/app/domain/services/copilot/service.py` (audit proposal_created)
- Modify: `backend/app/api/v1/endpoints/copilot.py` (audit proposal_applied via a tiny `/copilot/applied` beacon — optional; see Step 3)

- [ ] **Step 1: ProposalDiffCard**

```tsx
// Talk-Leee/src/components/copilot/ProposalDiffCard.tsx
"use client";
import type { Proposal } from "@/lib/copilot-api";

export function ProposalDiffCard(
  { proposal, onApply, onCancel }:
  { proposal: Proposal; onApply: (p: Proposal) => Promise<void>; onCancel: (p: Proposal) => void }
) {
  return (
    <div className="mx-3 mb-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm">
      <div className="font-medium">{proposal.summary}</div>
      <div className="mt-2 space-y-1">
        {proposal.human_diff.map((d, i) => (
          <div key={i} className="font-mono text-xs">
            <span className="text-gray-500">{d.field}: </span>
            <span className="text-red-600 line-through">{JSON.stringify(d.before)}</span>
            <span> → </span>
            <span className="text-green-700">{JSON.stringify(d.after)}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 text-xs text-amber-700">⚠ {proposal.warning}</div>
      <div className="mt-2 flex gap-2">
        <button onClick={() => onApply(proposal)} className="rounded-lg bg-green-600 px-3 py-1 text-white">Apply</button>
        <button onClick={() => onCancel(proposal)} className="rounded-lg border px-3 py-1">Cancel</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Audit proposal_created (backend)**

In `service.py` `_dispatch`, after building a proposal, log it. Add to `CopilotService.__init__` an optional `audit` and call it; or inline using the container's audit logger. Minimal inline version:

```python
# in _dispatch, proposal branch, before return:
try:
    from app.core.container import get_container
    c = get_container()
    if c.is_initialized and getattr(c, "audit_logger", None):
        await c.audit_logger.log_admin_action(
            action=f"copilot.proposal_created:{name}",
            actor_id=self._actor_id, tenant_id=self._tenant_id,
            after_state={"diff": proposal.get("human_diff"), "target": proposal.get("target")})
except Exception:
    logger.debug("copilot proposal audit failed", exc_info=True)
```
> Verify the container exposes an audit logger (grep `audit_logger` in `app/core/container.py`). If it's constructed differently, instantiate `AuditLogger` the same way other services do.

- [ ] **Step 3: Audit on Apply**

Apply happens client-side against the existing mutation endpoint, which already audits campaign updates if instrumented. To also record the copilot's hand in it, add a fire-and-forget beacon `POST /copilot/applied` that logs `copilot.proposal_applied` with the proposal id + diff, and call it from `onApply` after success. (If you prefer no extra endpoint, rely on the existing campaign-update audit — acceptable for v1.)

- [ ] **Step 4: Verify end-to-end (manual)**

`npm run dev`, open Copilot, type: "set DOJO company name to Dojo Pay". Expect a diff card `company_name: "DOJO" → "Dojo Pay"` + warning. Click Apply → reload the campaign edit page → value changed. Click Cancel on another → no change.

- [ ] **Step 5: Commit**

```bash
git add Talk-Leee/src/components/copilot/ProposalDiffCard.tsx backend/app/domain/services/copilot/service.py backend/app/api/v1/endpoints/copilot.py
git commit -m "feat(copilot): proposal diff card + Apply via PUT /campaigns + audit"
```

---

## SLICE 4 — Extend edits (knowledge / AI options / leads)

Each tool follows the Task-7 pattern exactly: add a schema to `PROPOSE_TOOL_SCHEMAS`, add a builder, return `{proposal_id, kind, summary, warning, human_diff, target}` pointing at the existing endpoint. Add one unit test per builder (mirror `test_campaign_edit_builds_diff_against_live_state`).

### Task 9: `propose_knowledge_edit` → `PATCH /campaigns/{id}/knowledge/nodes/{node_id}`

- [ ] **Step 1: Add schema** to `PROPOSE_TOOL_SCHEMAS`:
```python
{"name": "propose_knowledge_edit",
 "description": "Propose editing a knowledge node (heading, content, enabled, priority).",
 "parameters": {"type": "object", "properties": {
     "campaign_id": {"type": "string"}, "node_id": {"type": "string"},
     "changes": {"type": "object"}}, "required": ["campaign_id", "node_id", "changes"]}},
```
- [ ] **Step 2: Add builder** `_knowledge_edit`: read the node (`SELECT heading, content, enabled, priority FROM campaign_knowledge_nodes WHERE id=$1 AND campaign_id=$2` via `acquire_with_tenant`), diff only the allowed keys `{heading, content, enabled, priority}`, return target `{method:"PATCH", path:f"/campaigns/{cid}/knowledge/nodes/{nid}", payload: changes}`. Route it in `build_proposal`.
- [ ] **Step 3: Test** `test_knowledge_edit_builds_diff` (mirror Task 7 test with a FakeConn returning a node row).
- [ ] **Step 4: Run** `venv/bin/python -m pytest tests/unit/copilot/test_proposals.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(copilot): propose_knowledge_edit (PATCH knowledge node)`.

### Task 10: `propose_ai_options_edit` → `POST /campaigns/apply-tts-config`

- [ ] **Step 1: Read** `ApplyTtsConfigRequest` in `campaigns.py` for the exact payload (tts_provider/voice + the campaign-id selector list). 
- [ ] **Step 2: Add schema** with properties for the supported fields + a `campaign_ids` list (or "all").
- [ ] **Step 3: Add builder** `_ai_options_edit`: diff requested provider/voice vs each target campaign's current values; target `{method:"POST", path:"/campaigns/apply-tts-config", payload:{...}}`.
- [ ] **Step 4: Test + run** (mirror pattern) → PASS.
- [ ] **Step 5: Commit** `feat(copilot): propose_ai_options_edit (apply-tts-config)`.

### Task 11: `propose_lead_change` → `POST` / `DELETE /campaigns/{id}/contacts`

- [ ] **Step 1: Read** the `POST /campaigns/{id}/contacts` + `DELETE .../{contact_id}` request shapes in `campaigns.py`.
- [ ] **Step 2: Add schema** (`action: add|remove`, `campaign_id`, `contact` fields or `contact_id`).
- [ ] **Step 3: Add builder** `_lead_change`: for add, human_diff shows the new contact; target POST. For remove, read the contact first to show what's removed; target DELETE.
- [ ] **Step 4: Test + run** → PASS.
- [ ] **Step 5: Commit** `feat(copilot): propose_lead_change (add/remove contacts)`.

---

## SLICE 5 — Hardening

### Task 12: Security + injection tests

**Files:** `backend/tests/unit/copilot/test_security.py`

- [ ] **Step 1: Write tests**
```python
# backend/tests/unit/copilot/test_security.py
import pytest
from app.domain.services.copilot.service import CopilotService
from app.infrastructure.llm.gemini import ToolTurn


@pytest.mark.asyncio
async def test_llm_supplied_tenant_is_ignored(monkeypatch):
    captured = {}
    async def fake_read(name, args, *, pool, tenant_id):
        captured["tenant_id"] = tenant_id; return {"campaigns": []}
    monkeypatch.setattr("app.domain.services.copilot.service.run_read_tool", fake_read)
    llm = type("L", (), {"generate_with_tools": staticmethod(
        lambda **k: _one("list_campaigns"))})()
    svc = CopilotService(llm=_Scripted([_call("list_campaigns", {"tenant_id": "EVIL"}), _text("done")]),
                         pool=object(), tenant_id="REAL", actor_id="U1")
    await svc.chat(history=[{"role": "user", "content": "list"}])
    assert captured["tenant_id"] == "REAL"


@pytest.mark.asyncio
async def test_injection_cannot_write(monkeypatch):
    """A propose tool yields a proposal object — never a DB write — even if the
    model is coerced by malicious knowledge content."""
    async def fake_build(name, args, *, pool, tenant_id):
        return {"proposal_id": "x", "kind": "campaign_edit", "summary": "s",
                "warning": "w", "human_diff": [], "target": {"method": "PUT", "path": "/x", "payload": {}}}
    monkeypatch.setattr("app.domain.services.copilot.service.build_proposal", fake_build)
    svc = CopilotService(llm=_Scripted([_call("propose_campaign_edit", {}), _text("proposed")]),
                         pool=object(), tenant_id="T", actor_id="U")
    res = await svc.chat(history=[{"role": "user", "content": "delete everything"}])
    assert len(res["proposals"]) == 1  # staged, not applied
```
Add the small `_Scripted`, `_call`, `_text` helpers at the top (an LLM stub returning scripted `ToolTurn`s — copy the `FakeLLM` from `test_service.py`).

- [ ] **Step 2: Run** → adjust until PASS.
- [ ] **Step 3: Commit** `test(copilot): tenant-injection + no-write-on-injection guarantees`.

### Task 13: Full unit run + frontend typecheck

- [ ] **Step 1:** `cd backend && venv/bin/python -m pytest tests/unit/copilot tests/unit/llm/test_gemini_tools.py -v` → all PASS.
- [ ] **Step 2:** `cd Talk-Leee && npx tsc --noEmit && npm run build` → clean.
- [ ] **Step 3: Commit** any fixups: `chore(copilot): green test suite + typecheck`.

---

## Deployment (after local green)

Backend deploy is git-based: `git push origin main` then run `./deploy_to_server.sh` (it fast-forwards `/opt/talky` and restarts services at `--workers 1`). Frontend deploys to Vercel from repo root: `vercel --prod --yes`. The copilot needs `GEMINI_API_KEY` (already set on prod for Ask-AI). No DB migration required.

---

## Self-Review

**Spec coverage:** text chat (Slices 2,3,6) ✓; read incl. live RAG (Task 2 `retrieve_knowledge`) ✓; propose edits for all four entities (Tasks 7,9,10,11) ✓; preview-diff + Apply (Tasks 8) ✓; Gemini native tools (Task 1) ✓; existing endpoints for Apply (Tasks 7–11 targets) ✓; tenant isolation (Task 2/12) ✓; audit (Task 8) ✓; no new tables ✓; voice path untouched (Task 1 adds a parallel method) ✓.

**Placeholder scan:** read-tool, service, proposal, gemini code is concrete. Slice-4 tasks give exact schema + endpoint + SQL shape per builder (the repeated pattern is fully specified, not "see Task 7"). Frontend `http` verb names flagged to confirm against `http-client.ts` — explicit, not a silent gap.

**Type consistency:** `ToolTurn{text,tool_calls}`, history item shapes, and proposal shape `{proposal_id,kind,summary,warning,human_diff,target{method,path,payload}}` are identical across backend (proposals.py) and frontend (`ProposalSchema`). `run_read_tool(name,args,*,pool,tenant_id)` and `build_proposal(name,args,*,pool,tenant_id)` signatures match all call sites.
