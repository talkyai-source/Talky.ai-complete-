"""Replay a recorded call through the live agent, offline, and show what it does.

Rebuilds the agent exactly as the browser Test Agent does (the campaign's
prompt, its pinned knowledge snapshot, the tools offered, the tenant's chosen
model with the same failover), then plays the call's recorded caller lines
through the real turn runner. For every turn it reports what the agent said
and every tool call it made, with the arguments and the tool's result, plus
the contact state afterwards.

Nothing is written anywhere: the database is only read (call, campaign,
knowledge, AI settings), and contact saves go to an in-memory recorder. The
only outside calls are to the model provider.

Why it exists (2026-10-08): four test calls lost every confirmed contact and
one answered "0"; the tool arguments were never logged, so the cause could not
be read from production. This turns a real call into a reproducible test of
the real model, before and after a change.

usage, in backend/ on the server (reads .env for the database and model keys):
  sudo venv/bin/python scripts/replay_call.py --call <calls.id> [--turns N]
      [--provider deepseek --model deepseek-flash] [--json out.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Import the app from the checkout being tested: the current directory when it
# holds app/, otherwise the backend this script lives in.
sys.path.insert(0, os.getcwd() if Path('app').is_dir() else str(Path(__file__).resolve().parents[1]))


def load_env(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without printing any of them."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class RecorderPool:
    """Stands in for the database on writes: records them, persists nothing."""

    def __init__(self):
        self.writes = []

    @asynccontextmanager
    async def acquire(self, *args, **kwargs):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, *args):
        return "OK"

    async def fetchrow(self, sql, *args):
        if "is_test" in sql and "SELECT" in sql.upper():
            return {"is_test": True}
        self.writes.append(" ".join(str(sql).split())[:120])
        return {"id": str(uuid.uuid4())}

    async def fetch(self, sql, *args):
        return []

    async def fetchval(self, sql, *args):
        return None


async def read_call(pool, call_id: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            row = await conn.fetchrow(
                "SELECT id, tenant_id, campaign_id, transcript_json FROM calls WHERE id = $1::uuid", call_id)
    if row is None:
        raise SystemExit(f"call {call_id} not found")
    transcript = row["transcript_json"]
    if isinstance(transcript, str):
        transcript = json.loads(transcript)
    return {"tenant_id": str(row["tenant_id"]), "campaign_id": str(row["campaign_id"]),
            "transcript": transcript or []}


def caller_turns(transcript: list) -> tuple[str | None, list[str]]:
    """The agent's opener (if it spoke first) and the caller's lines, with
    consecutive caller fragments joined the way the model saw them."""
    opener, turns, pending = None, [], []
    for item in transcript:
        role = item.get("role")
        text = (item.get("content") or item.get("text") or "").strip()
        if not text:
            continue
        if role == "assistant":
            if pending:
                turns.append(" ".join(pending))
                pending = []
            elif not turns and opener is None:
                opener = text
        elif role == "user":
            pending.append(text)
    if pending:
        turns.append(" ".join(pending))
    return opener, turns


def contact_summary(state) -> dict:
    out = {}
    for kind in ("email", "phone", "full_name", "company_name"):
        capture = getattr(state, f"{kind}_capture", None)
        if capture is not None:
            out[kind] = {"status": getattr(getattr(capture, "status", None), "value", str(capture.status)),
                         "value": capture.normalized_value}
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--call", required=True)
    parser.add_argument("--turns", type=int, default=0, help="replay only the first N caller turns")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    load_env(Path(args.env))
    import asyncpg
    from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config
    from app.api.v1.endpoints.campaign_test_ws import _fetch_campaign_row
    from app.domain.models.conversation import Message, MessageRole
    from app.domain.models.session import CallSession, CallState
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.tenant_ai_config_resolver import get_tenant_ai_config_resolver
    from app.domain.services.voice_orchestrator import (
        Direction, VoiceOrchestrator, opening_mode_from_first_speaker,
    )
    from app.domain.services.voice_pipeline import turn_streamer
    from app.domain.services.voice_pipeline.transcript_handler import _accept_caller_turn
    from app.domain.services.voice_pipeline_service import VoicePipelineService
    from app.domain.services.voice_tuning import get_voice_tuning_resolver
    from app.services.scripts.call_state_tracker import CallState as CapturedSlots
    from app.services.scripts.knowledge.session_inject import apply_campaign_knowledge, copy_prepared_knowledge

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    call = await read_call(pool, args.call)
    tenant_id, campaign_id = call["tenant_id"], call["campaign_id"]

    async def ai_lookup(tid):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                return await _fetch_tenant_config(conn, tid)

    get_tenant_ai_config_resolver().set_db_lookup(ai_lookup)
    ai_cfg = await get_tenant_ai_config_resolver().for_tenant_async(tenant_id, require_available=True)
    voice_tuning = await get_voice_tuning_resolver().for_tenant_async(tenant_id)
    campaign_row = await _fetch_campaign_row(pool, tenant_id, campaign_id)
    opener, turns = caller_turns(call["transcript"])
    first_speaker = "agent" if opener else "user"  # whoever spoke first on the recording
    config = build_telephony_session_config(
        gateway_type="browser", campaign=campaign_row, direction=Direction.OUTBOUND,
        opening_mode=opening_mode_from_first_speaker(first_speaker),
        ai_config_override=ai_cfg, voice_tuning_override=voice_tuning, allow_browser_barge_in=True,
    )
    if args.provider:
        config.llm_provider_type = args.provider
    if args.model:
        config.llm_model = args.model
    await apply_campaign_knowledge(config, campaign_row, pool=pool)
    llm = await VoiceOrchestrator(db_client=None)._create_llm_provider(config)

    pipeline = VoicePipelineService(stt_provider=AsyncMock(), llm_provider=llm,
                                    tts_provider=AsyncMock(), media_gateway=AsyncMock())
    pipeline.latency_tracker = MagicMock()
    spoken: list[str] = []

    async def speak(session, text, *a, **k):
        spoken.append(text)
        return False

    pipeline.synthesize_and_send_audio = speak

    session = CallSession(
        call_id=f"replay-{uuid.uuid4()}", campaign_id=config.campaign_id, lead_id=str(config.lead_id or "replay"),
        provider_call_id="replay", state=CallState.ACTIVE, agent_config=config.agent_config,
        persona_type=config.persona_type, system_prompt=config.system_prompt,
        llm_model=config.llm_model, llm_temperature=config.llm_temperature,
        llm_max_tokens=config.llm_max_tokens, voice_id=config.voice_id or "replay",
        contact_phone_region=config.contact_phone_region,
    )
    copy_prepared_knowledge(config, session)
    session.talklee_call_id = "replay"
    session.barge_in_event = asyncio.Event()
    session.captured_slots = CapturedSlots()
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    recorder = RecorderPool()
    session._voice_action_pool = recorder
    session._dialer_call_id = str(uuid.uuid4())
    session._dialer_tenant_id = tenant_id
    session._dialer_campaign_id = campaign_id
    if opener:
        session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content=opener))

    tool_log: list[dict] = []

    def logged(name, fn):
        async def wrapper(*a, **k):
            arguments = a[1] if len(a) > 1 else k.get("arguments")
            result = await fn(*a, **k)
            entry = {"tool": name, "arguments": arguments}
            try:
                parsed = json.loads(result) if isinstance(result, str) else result
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                entry["result"] = {key: parsed.get(key) for key in (
                    "status", "reason", "saved", "success", "validation_status", "value",
                    "expanded", "more_section_ids", "message") if key in parsed}
            elif isinstance(result, str):
                entry["result"] = {"text": result[:160]}
            tool_log.append(entry)
            return result
        return wrapper

    turn_streamer.record_contact = logged("record_contact", turn_streamer.record_contact)
    turn_streamer.run_knowledge_lookup = logged("lookup_company_knowledge", turn_streamer.run_knowledge_lookup)
    turn_streamer.run_voice_action = logged("action", turn_streamer.run_voice_action)

    report = {"call": args.call, "provider": config.llm_provider_type, "model": config.llm_model,
              "campaign": campaign_id, "opener": opener, "turns": []}
    print(f"replay call={args.call} provider={config.llm_provider_type} model={config.llm_model} "
          f"turns={len(turns)} knowledge={'yes' if getattr(session, '_knowledge_catalog', None) else 'no'}")
    if opener:
        print(f"  AGENT : {opener}")
    for index, text in enumerate(turns[: args.turns or None], start=1):
        spoken.clear()
        tool_log.clear()
        recorded = pipeline.transcript_service.accumulate_turn(
            call_id=session.call_id, role="user", content=text, talklee_call_id="replay",
            turn_index=session.turn_id, event_type="end_of_turn", is_final=True, metadata={})
        session._latest_final_transcript_turn = recorded
        order = _accept_caller_turn(session, pipeline.transcript_service)
        started = time.monotonic()
        task = asyncio.create_task(pipeline._run_turn(session, text, None, session.turn_id))
        task._caller_turn_order = order
        try:
            await task
            error = None
        except Exception as exc:  # report and keep going
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        session.turn_id += 1
        turn = {"n": index, "caller": text, "agent": " ".join(spoken), "seconds": round(elapsed, 2),
                "tools": list(tool_log), "contacts": contact_summary(session.captured_slots), "error": error}
        report["turns"].append(turn)
        print(f"  CALLER: {text}")
        for entry in tool_log:
            print(f"    tool {entry['tool']} args={json.dumps(entry['arguments'], ensure_ascii=False)[:300]}")
            print(f"         -> {json.dumps(entry.get('result'), ensure_ascii=False)[:300]}")
        print(f"  AGENT : {turn['agent'] or '(silent)'}  [{turn['seconds']} s]" + (f"  ERROR {error}" if error else ""))
        if turn["contacts"]:
            print(f"    contacts: {json.dumps(turn['contacts'], ensure_ascii=False)}")
    report["contact_writes"] = recorder.writes
    print(f"contact writes recorded (not persisted): {len(recorder.writes)}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
