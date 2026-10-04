"""Offline AG04 controls through the real traditional turn and provider paths.

Only synthetic, source-controlled fixtures enter this runner. Provider adapters
receive fake SDK streams, never keys; media and effect ports are intercepted.
Authored answers are NOT evidence of model quality. The captured Groq failure
is retained as a failed semantic case even when runtime safety checks pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import time
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.models.agent_config import AgentConfig, AgentGoal
from app.domain.models.conversation import Message, MessageRole, TranscriptChunk
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.llm.request_profile import _json_default, record_traditional_request
from app.infrastructure.llm.streaming import LLMStreamStalled
from app.services.scripts.prompts.composer import compose_prompt_document

FIXTURE = "backend/tests/fixtures/conversation/ag04_traditional.json"
ENGINE = "traditional"


def _json(value):
    return json.loads(json.dumps(value, default=_json_default, sort_keys=True))


def _load(root):
    return json.loads((root / FIXTURE).read_text(encoding="utf-8"))


def _profile(spec):
    return {
        **spec,
        "voice": "synthetic-no-audio",
        "settings": {
            "temperature": 0.2,
            "visible_token_target": 300,
        },
        "mode": "offline_replay",
    }


def case_inventory(root: Path) -> list[dict]:
    data = _load(root)
    return [
        {"scenario_id": case["id"], "engine": ENGINE, "profile": _profile(profile)}
        for case in data["cases"]
        for profile in data["profiles"]
    ]


class _ScriptedSDK:
    """Fake provider transport; the actual adapter still builds/parses wire data."""

    def __init__(self, name):
        self.name = name
        self.turn = {}
        self.round = 0
        self.requests = []
        self.observed = []

    def begin(self, turn):
        self.turn = turn
        self.round = 0

    async def chunks(self):
        round_index = self.round
        self.round += 1
        turn = self.turn
        if turn.get("failure") == "timeout":
            raise LLMStreamStalled("Synthetic no-output timeout")
        tool = turn.get("tool") if round_index == 0 else None
        if tool:
            yield {"tool": tool}
        else:
            for text in turn.get("chunks", []):
                if text:
                    self.observed.append(text)
                    yield {"text": text}
        reason = "tool_calls" if tool else "stop"
        if turn.get("failure") == "length":
            reason = "length"
        elif turn.get("failure") == "eof":
            return
        yield {"reason": reason}

    async def create(self, **kwargs):
        self.requests.append(_json(kwargs))

        async def stream():
            async for item in self.chunks():
                text, tool, reason = item.get("text"), item.get("tool"), item.get("reason")
                if self.name == "gemini":
                    part = NS(text=text, thought=False)
                    calls = []
                    if tool:
                        call = NS(name=tool["name"], args=tool.get("arguments", {}))
                        part = NS(function_call=call, text=None, thought=False)
                        calls = [call]
                    terminal = "MAX_TOKENS" if reason == "length" else "STOP" if reason else None
                    yield NS(
                        text=text,
                        function_calls=calls,
                        candidates=[
                            NS(
                                finish_reason=terminal,
                                content=NS(parts=[part] if text or tool else []),
                            )
                        ],
                    )
                else:
                    calls = (
                        None
                        if not tool
                        else [
                            NS(
                                index=0,
                                id="synthetic-action",
                                function=NS(
                                    name=tool["name"],
                                    arguments=json.dumps(tool.get("arguments", {})),
                                ),
                            )
                        ]
                    )
                    yield NS(
                        choices=[NS(delta=NS(content=text, tool_calls=calls), finish_reason=reason)]
                    )

        return stream()

    @asynccontextmanager
    async def stream(self, _method, _path, *, json):
        self.requests.append(_json(json))

        async def lines():
            async for item in self.chunks():
                delta = {}
                if item.get("text"):
                    delta["content"] = item["text"]
                if item.get("tool"):
                    tool = item["tool"]
                    delta["tool_calls"] = [
                        {
                            "index": 0,
                            "id": "synthetic-action",
                            "function": {
                                "name": tool["name"],
                                "arguments": __import__("json").dumps(tool.get("arguments", {})),
                            },
                        }
                    ]
                yield "data: " + __import__("json").dumps(
                    {
                        "choices": [
                            {
                                "delta": delta,
                                "finish_reason": item.get("reason"),
                            }
                        ]
                    }
                )

        yield NS(raise_for_status=lambda: None, aiter_lines=lines)


def _provider(spec, sdk):
    module = importlib.import_module("app.infrastructure.llm." + spec["provider"])
    cls = {
        "groq": "GroqLLMProvider",
        "cerebras": "CerebrasLLMProvider",
        "openai": "OpenAILLMProvider",
        "gemini": "GeminiLLMProvider",
    }[spec["provider"]]
    provider = getattr(module, cls)()
    provider._model = spec["model"]
    provider._temperature, provider._max_tokens = 0.2, 300
    provider._client = (
        sdk
        if spec["provider"] == "openai"
        else (
            NS(aio=NS(models=NS(generate_content_stream=sdk.create)))
            if spec["provider"] == "gemini"
            else NS(chat=NS(completions=NS(create=sdk.create)))
        )
    )
    return module, provider


def _knowledge(rows):
    return [
        {
            **row,
            "id": f"synthetic-fact-{index}",
            "source_id": "synthetic-source",
            "source_version": 1,
            "version": "2026-10-05T00:00:00Z",
            "coverage": 1.0,
        }
        for index, row in enumerate(rows, 1)
    ]


async def _run_case(root, data, case, spec):
    started = datetime.now(timezone.utc).isoformat()
    sdk = _ScriptedSDK(spec["provider"])
    module, provider = _provider(spec, sdk)
    prompt = compose_prompt_document(
        "lead_gen",
        data["agent"],
        data["company"],
        {},
        knowledge_driven=True,
        direction="outbound",
        additional_instructions=data["campaign_instructions"],
    )
    facts = _knowledge(case.get("knowledge", data["default_knowledge"]))
    session = CallSession(
        call_id="synthetic-" + case["id"],
        tenant_id="synthetic-tenant",
        campaign_id="synthetic-campaign",
        lead_id="synthetic-lead",
        provider_call_id="synthetic-provider",
        voice_id="synthetic-voice",
        system_prompt=prompt.system_prompt,
        knowledge_mode="retrieve",
        agent_config=AgentConfig(
            goal=AgentGoal.LEAD_QUALIFICATION,
            business_type="payments",
            agent_name=data["agent"],
            company_name=data["company"],
        ),
    )
    session.turn_id = 4  # after pickup; never used as caller temporal order
    session._call_direction = "outbound"
    session._has_introduced = case.get("introduced", True)
    session._line_phone_checked = True
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {
        name: "Synthetic executor" for name in case.get("capabilities", [])
    }
    session._voice_action_results = {}
    session._knowledge_snapshot_nodes = facts
    session.llm_model, session.llm_temperature, session.llm_max_tokens = spec["model"], 0.2, 300
    session.barge_in_event = asyncio.Event()
    session.conversation_history = [
        Message(role=MessageRole(row["role"]), content=row["content"])
        for row in case.get(
            "history", [{"role": "assistant", "content": "What would you like help with?"}]
        )
    ]
    stt = MagicMock()
    stt.detect_turn_end.side_effect = lambda t: t.is_final and not t.text
    gateway = MagicMock()
    gateway.hangup_call = AsyncMock(return_value=True)
    gateway.wait_for_playback_complete = AsyncMock(return_value=True)
    gateway.on_call_ended = AsyncMock()
    service = VoicePipelineService(
        stt_provider=stt,
        llm_provider=provider,
        tts_provider=MagicMock(),
        media_gateway=gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.transcript_service = MagicMock()
    service.transcript_service.flush_to_database = AsyncMock()
    service._barge_in_events[session.call_id] = session.barge_in_event
    websocket = NS(send_json=AsyncMock(), close=AsyncMock())
    requests, submissions, attempts, turns, errors, effects = [], [], [], [], [], []
    current_turn = {}

    def record(**kwargs):
        profile = record_traditional_request(**kwargs)
        request = _json(kwargs["request"])
        requests.append(
            {
                "model": request["model"],
                "instructions_sha256": profile["instructions_sha256"],
                "wire_sha256": profile["request_envelope_sha256"],
                "messages": request.get("messages", request.get("contents", [])),
                "tool_names": profile.get("tool_names", []),
                "wire": request,
            }
        )
        return profile

    async def speak(s, text, *_args, **_kwargs):
        attempts.append(text)
        # Real TTS owns this lifecycle flag and clears it when its await ends.
        # The boundary double must do so too; otherwise a subsequent final is
        # spuriously treated as a barge-in on audio that never existed.
        s.tts_active = False
        media = current_turn.get("media")
        if media == "exception":
            raise RuntimeError("Synthetic TTS failure")
        if media == "disconnect":
            s._tts_delivery_failed = True
            return True
        if media in {"interrupt", "continue"}:
            s.barge_in_event.set()
            s._caller_speaking = True
            s._caller_speaking_since = time.monotonic()
            s._last_caller_activity_monotonic = time.monotonic()
            s.current_user_input = "Wait, what are your opening hours?"
            return True
        submissions.append(text)
        # Fake TTS acceptance is not a playback receipt or proof of hearing.
        return False

    async def dnc(s):
        if getattr(s, "_qualification_dnc_written", False):
            return True
        accepted = current_turn.get("dnc_result", True)
        effects.append(
            {
                "kind": "dnc",
                "status": "accepted" if accepted else "failed",
                "origin": "synthetic_external_port",
            }
        )
        s._qualification_dnc_written = accepted
        return accepted

    async def connected(s, action, arguments, caller):
        result = {
            "version": 1,
            "action": action,
            **current_turn.get(
                "tool_result",
                {
                    "success": False,
                    "status": "unavailable",
                    "confirmation_allowed": False,
                    "message": "Synthetic action unavailable.",
                },
            ),
        }
        effects.append(
            {
                "kind": action,
                "arguments": arguments,
                "status": result["status"],
                "origin": "synthetic_external_executor",
            }
        )
        return result

    service.synthesize_and_send_audio = speak
    with ExitStack() as patches:
        patches.enter_context(patch.object(module, "record_traditional_request", record))
        patches.enter_context(
            patch("app.core.container.get_container", return_value=NS(is_initialized=False))
        )
        patches.enter_context(
            patch("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", dnc)
        )
        patches.enter_context(
            patch(
                "app.domain.services.voice_pipeline.action_execution.execute_connected_voice_action",
                connected,
            )
        )
        patches.enter_context(patch.dict("os.environ", {"TELEPHONY_FILLER_DELAY_MS": "0"}))
        for current_turn in case["turns"]:
            sdk.begin(current_turn)
            before = len(session.conversation_history)
            prior_submissions, prior_requests = len(submissions), len(requests)
            session._tts_delivery_failed = False
            session._caller_speaking = False
            session.barge_in_event.clear()
            try:
                await service.handle_transcript(
                    session,
                    TranscriptChunk(
                        text=current_turn["caller"],
                        is_final=True,
                        confidence=current_turn.get("confidence"),
                    ),
                    websocket,
                )
                await service.handle_transcript(
                    session, TranscriptChunk(text="", is_final=True), websocket
                )
                pending = service._pending_llm_tasks.get(session.call_id)
                if pending is not None:
                    await asyncio.wait_for(pending, 5)
            except Exception as exc:
                errors.append(type(exc).__name__)
            turns.append(
                {
                    "caller": current_turn["caller"],
                    "requests": len(requests) - prior_requests,
                    "raw_output": "".join(current_turn.get("chunks", [])),
                    "submitted_speech": submissions[prior_submissions:],
                    "history_added": [
                        {"role": m.role.value, "content": m.content}
                        for m in session.conversation_history[before:]
                    ],
                    "knowledge_status": (getattr(session, "_knowledge_evidence", {}) or {}).get(
                        "status"
                    ),
                    "end_requested": bool(getattr(session, "_end_call_requested", False)),
                }
            )

    history = [{"role": m.role.value, "content": m.content} for m in session.conversation_history]
    state = getattr(session, "_live_structured_state", None)
    slots = getattr(session, "captured_slots", None)
    controls = []

    def check(id, passed, detail):
        controls.append({"id": id, "pass": bool(passed), "detail": detail})

    expected = case["expected"]
    check("runner_completed", not errors, "No runner exception; errors=" + repr(errors))
    check(
        "close_authority",
        bool(gateway.hangup_call.await_count) == expected["end"],
        f"Synthetic gateway hangup count={gateway.hangup_call.await_count}; expected end={expected['end']}",
    )
    check(
        "optout_authority",
        bool(getattr(session, "_caller_opted_out", False)) == expected["dnc"],
        "Opt-out flag must follow actual caller intent, not generated text.",
    )
    check(
        "optout_effect",
        sum(effect["kind"] == "dnc" for effect in effects) == int(expected["dnc"]),
        "A claimed opt-out needs the synthetic persistence port once; no real database was written.",
    )
    non_dnc = [effect for effect in effects if effect["kind"] != "dnc"]
    accepted = [
        effect for effect in non_dnc if effect["status"] in {"accepted", "succeeded", "completed"}
    ]
    check(
        "no_unapproved_effect",
        len(accepted) == expected["effects"],
        "Actual intercepted external executor results.",
    )
    if case.get("capabilities"):
        check(
            "failed_executor_exercised",
            len(non_dnc) == 1,
            "Failure case must actually invoke the offered synthetic executor once.",
        )
    spoken = " ".join(submissions)
    assistant_history = " ".join(m["content"] for m in history if m["role"] == "assistant")
    for forbidden in case.get("forbidden", []):
        check(
            "forbidden_speech:" + forbidden,
            forbidden.casefold() not in spoken.casefold(),
            "Explicit fixture safety prohibition.",
        )
    for forbidden in case.get("forbidden_history", []):
        check(
            "unsaid_history",
            forbidden not in assistant_history,
            "Failed media text must not appear as delivered assistant history.",
        )
    if "expected_relationship" in case:
        actual = getattr(getattr(state, "customer_relationship", None), "value", "unknown")
        check(
            "relationship",
            actual == case["expected_relationship"],
            f"Relationship={actual}; expected={case['expected_relationship']}",
        )
    for kind in case.get("unconfirmed", []):
        check(
            kind + "_unconfirmed",
            not getattr(slots, kind + "_confirmed", False),
            "Ambiguous/corrected/third-party values remain unconfirmed.",
        )
    if "expected_email" in case:
        value = getattr(getattr(slots, "email_capture", None), "normalized_value", None)
        check(
            "revised_email",
            value == case["expected_email"],
            "An explicit correction replaces the candidate without claiming confirmation.",
        )
    if any(turn.get("failure") for turn in case["turns"]):
        check(
            "no_error_replay",
            len(requests) == len(case["turns"]),
            "A failed/partial provider turn is not regenerated or replayed.",
        )
    if any(turn.get("media") for turn in case["turns"]):
        check(
            "no_invented_hearing",
            not getattr(session, "_tts_playout_completed", False),
            "Synthetic submission alone is never a playback receipt.",
        )
    for index, (source_turn, observed_turn) in enumerate(zip(case["turns"], turns)):
        if not source_turn.get("media"):
            speech = " ".join(observed_turn["submitted_speech"]).strip()
            saved = " ".join(
                m["content"] for m in observed_turn["history_added"] if m["role"] == "assistant"
            ).strip()
            check(
                f"history_submission:{index}",
                speech == saved,
                "Actual committed assistant history equals successful synthetic TTS submissions; neither proves hearing.",
            )
    captured = case.get("captured")
    semantic = {"id": "semantic_rubric", "status": "unreviewed", "detail": case["rubric"]}
    if captured and spec["provider"] == captured["provider"] and spec["model"] == captured["model"]:
        original = root / captured["artifact"]
        check(
            "captured_source_unchanged",
            hashlib.sha256(original.read_bytes()).hexdigest() == captured["sha256"],
            "Exact archived actual-provider evidence preserved.",
        )
        semantic.update(status="fail", detail=captured["reason"])
        expected_prior = case["history"][-1]["content"]
        check(
            "actual_prior_question_on_wire",
            any(expected_prior in json.dumps(r["messages"]) for r in requests),
            "Reproduction must include the actual prior assistant question.",
        )
    return {
        "scenario_id": case["id"],
        "engine": ENGINE,
        "profile": _profile(spec),
        "provenance": {
            "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "fixture": FIXTURE,
            "fixture_sha256": hashlib.sha256((root / FIXTURE).read_bytes()).hexdigest(),
            "captured": captured,
            "output_origin": "captured_historical_failure" if captured else "authored_control",
        },
        "source_facts": facts,
        "requests": requests,
        "raw_output": ["".join(turn.get("chunks", [])) for turn in case["turns"]],
        "provider_chunks_observed": sdk.observed,
        "submitted_speech": submissions,
        "tts_requests": attempts,
        "history": history,
        "turns": turns,
        "state": {
            "relationship": getattr(
                getattr(state, "customer_relationship", None), "value", "unknown"
            ),
            "contact_capture": str(getattr(slots, "email_capture", None)),
            "email_confirmed": bool(getattr(slots, "email_confirmed", False)),
            "phone_confirmed": bool(getattr(slots, "phone_confirmed", False)),
        },
        "end": {
            "requested": bool(getattr(session, "_end_call_requested", False)),
            "shutdown_count": gateway.hangup_call.await_count,
            "dnc_flag": bool(getattr(session, "_caller_opted_out", False)),
            "dnc_effect_count": sum(effect["kind"] == "dnc" for effect in effects),
        },
        "effects": {"attempts": effects, "accepted": len(accepted)},
        "findings": {"control": controls, "semantic": [semantic]},
        "scope": "Real traditional scheduling, turn preparation, capture parser, guards and provider request adapter; synthetic boundary results only.",
        "limitations": data["limitations"]
        + [
            "No database/durable effect proof; connected action executor is intercepted.",
            "TTS is intercepted before audio; submitted text proves neither transport completion nor human hearing.",
        ],
    }


async def run_cases(root: Path) -> list[dict]:
    data = _load(root)
    rows = []
    for case in data["cases"]:
        for profile in data["profiles"]:
            rows.append(await _run_case(root, data, case, profile))
    return rows
