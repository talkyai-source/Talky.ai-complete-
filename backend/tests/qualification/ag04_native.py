"""Offline native qualification observations, not model or acoustic approval.

The real provider event parsers, independent prompt composer and RealtimeBridge
consume synthetic wire events. Socket, playback receipts and campaign sources
are local fixtures. No connect(), credentials, database or delivery is used.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch
from uuid import uuid4

from app.domain.services.transcript_service import TranscriptService
from app.domain.services.voice_pipeline.action_tools import action_results_for_session, action_tool_system_addendum
from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import OpenAIRealtimeSession, knowledge_lookup_tool
from app.realtime.prompt_config import prepare_realtime_prompt
from app.realtime.tools import realtime_voice_action_tools
from app.realtime.xai import XAIRealtimeSession


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class FixtureSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def close(self):
        self.closed = True


class FixtureGateway:
    """Synthetic acknowledgments explicitly cannot establish human hearing."""
    playback_evidence = "transport_played"

    def __init__(self, config):
        self.config = dict(config)
        self.set_receipt(config.get("receipt", "completed"))
        self.submissions = []
        self.receipts = []
        self.controls = []
        self.clears = 0
        self.current = None
        self.current_text = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.queue = asyncio.Queue()
        self.blocked = False

    def set_receipt(self, kind):
        if kind not in {"completed", "stale", "unknown", "transmitted"}:
            raise ValueError("Unknown synthetic playback receipt")
        self.config["receipt"] = kind
        self.playback_evidence = "transmitted" if kind == "transmitted" else "transport_played"

    def block_next(self, phase):
        if phase not in {"send", "finish"}:
            raise ValueError("Unknown synthetic playback block")
        self.config["block"] = phase
        self.blocked = False
        self.entered.clear()
        self.release.clear()

    def set_realtime_output(self, *args):
        pass

    def get_audio_queue(self, call_id):
        return self.queue

    async def send_control_event(self, call_id, payload):
        self.controls.append(payload)
        if payload.get("type") == "llm_response":
            self.current_text = payload.get("text")

    async def begin_playback(self, call_id, utterance_id):
        self.current = utterance_id

    async def _block(self, phase):
        if self.config.get("block") != phase or self.blocked:
            return
        self.blocked = True
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.config.get("swallow_cancel"):
                raise

    async def send_audio(self, call_id, audio):
        self.submissions.append({"utterance_id": self.current, "candidate_text": self.current_text,
            "pcm_bytes": len(audio), "sha256": hashlib.sha256(audio).hexdigest()})
        await self._block("send")

    async def finish_playback(self, call_id, utterance_id):
        await self._block("finish")
        kind = self.config.get("receipt", "completed")
        receipt = {"utterance_id": "expired-utterance" if kind == "stale" else utterance_id,
            "status": "unknown" if kind == "unknown" else "completed",
            "evidence": {"unknown": "unknown", "transmitted": "transmitted"}.get(kind, "transport_played"),
            "played_ms": 0 if kind in {"unknown", "transmitted"} else sum(row["pcm_bytes"] for row in self.submissions
                if row["utterance_id"] == utterance_id) // 16}
        self.receipts.append(receipt)
        return receipt

    def playback_receipt(self, call_id, utterance_id):
        return {"utterance_id": utterance_id, "status": "unknown", "evidence": "unknown", "played_ms": 0}

    async def clear_output_buffer(self, call_id):
        self.clears += 1


class NativeReplay:
    def __init__(self, scenario, provider_name, corpus):
        self.scenario, self.provider_name, self.corpus = scenario, provider_name, corpus
        self.profile = _profile(corpus, provider_name)
        self.call_id = "ag04-synthetic-" + uuid4().hex
        self.socket = FixtureSocket()
        self.gateway = FixtureGateway(scenario.get("gateway", {}))
        self.transcripts = TranscriptService()
        # Explicit fixture receipts replace only the connected executor port.
        # Without one, missing business identities keep external actions unavailable.
        self.action_attempts = []
        self.contact_checkpoints = []
        self.fixture_actions = scenario.get("synthetic_action_results", {})
        self.session = SimpleNamespace(_voice_action_capabilities={
            name: "Synthetic result port" for name in self.fixture_actions}, _voice_action_context_loaded=True,
            _voice_action_pool=object(), call_id=self.call_id, captured_slots=None)
        self.config = SimpleNamespace(direction="inbound", agent_config=SimpleNamespace(
            agent_name="Ava", company_name="Northwind Systems"), realtime_prompt={
                "persona": "assistant", "goal": "Answer the caller using verified information.",
                "instructions": corpus["campaign_guidance"]}, realtime_opening_greeting="",
            realtime_message_intake=False)
        self.instructions = prepare_realtime_prompt(self.config,
            capability_instructions=action_tool_system_addendum({"end_call", *self.fixture_actions}))
        self.tools = [knowledge_lookup_tool(), *realtime_voice_action_tools(self.session)]
        if provider_name == "openai":
            self.provider = OpenAIRealtimeSession(api_key="offline-unused", model=self.profile["model"], voice=self.profile["voice"],
                instructions=self.instructions, tools=self.tools, call_id=self.call_id,
                settings=self.profile["settings"])
        elif provider_name == "xai":
            self.provider = XAIRealtimeSession(api_key="offline-unused", model=self.profile["model"], voice=self.profile["voice"],
                instructions=self.instructions, tools=self.tools, call_id=self.call_id, settings=self.profile["settings"])
        else:
            raise ValueError("Unsupported offline native profile")
        self.provider._ws = self.socket
        self.provider._prompt_identity = {"template": self.config.prompt_template, "version": self.config.prompt_version}
        self.initial_wire = self.provider._build_session_update()
        self.shutdown_count = 0
        self.raw = []
        self.raw_events = []
        self.dnc_receipts = []
        self.response_counter = 0
        self.item_counter = 0
        self.observed_exception = None
        self.bridge = RealtimeBridge(call_id=self.call_id, realtime_session=self.provider,
            media_gateway=self.gateway, contact_session=self.session, action_session=self.session,
            transcript_service=self.transcripts, on_end_call=self.on_end, call_direction="inbound",
            greet_on_start=scenario.get("opening", False),
            tenant_id="synthetic-tenant", campaign_id="synthetic-campaign",
            knowledge_snapshot_nodes=scenario.get("source_facts", []))

    async def on_end(self):
        self.shutdown_count += 1

    async def execute_fixture_action(self, session, action, arguments, caller):
        assert session is self.session
        if action not in self.fixture_actions:
            raise AssertionError("Undeclared synthetic connected action")
        result = {"version": 1, "action": action, **self.fixture_actions[action]}
        self.action_attempts.append({"action": action, "arguments": dict(arguments),
            "receipt": dict(result), "origin": "synthetic connected executor; no provider send"})
        return result

    async def persist_dnc(self, session):
        """Replay the existing persistence port, never perform a database write."""
        assert session is self.session
        acknowledged = self.scenario.get("dnc_acknowledgement")
        if acknowledged is not None and type(acknowledged) is not bool:
            raise ValueError("Synthetic DNC acknowledgement must be true, false or null")
        self.dnc_receipts.append({"acknowledged": acknowledged,
            "origin": "synthetic persistence port", "database_writes": 0})
        return acknowledged

    async def wire(self, event):
        self.raw_events.append(event)
        await self.provider._handle_server_event(event)

    async def drain(self, *, playback=True):
        self.provider._offer_event(None)
        await asyncio.wait_for(self.bridge._pump_model_events(), 2)
        if playback and self.bridge._playback_task:
            await asyncio.wait_for(asyncio.gather(self.bridge._playback_task, return_exceptions=True), 2)
        if playback and self.bridge._tool_tasks:
            await asyncio.wait_for(asyncio.gather(*self.bridge._tool_tasks), 2)
        if self.bridge._goodbye_completed.is_set() and self.bridge._termination_task:
            await asyncio.wait_for(self.bridge._termination_task, 2)

    async def emit(self, step):
        kind = step["kind"]
        if kind in {"caller", "start"}:
            self.item_counter += 1
            item_id = step.get("item", f"caller-{self.item_counter}")
            if kind == "start":
                await self.wire({"type": "input_audio_buffer.speech_started", "item_id": item_id,
                    "audio_start_ms": step.get("offset", self.item_counter * 1000)})
            else:
                if not step.get("revision"):
                    await self.wire({"type": "input_audio_buffer.committed", "item_id": item_id})
                await self.wire({"type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item_id, "transcript": step["text"],
                    **({"confidence": step["confidence"]} if "confidence" in step else {})})
        elif kind in {"response", "tool"}:
            self.response_counter += 1
            rid = f"response-{self.response_counter}"
            await self.wire({"type": "response.created", "response": {"id": rid}})
            if kind == "response":
                if "receipt" in step:
                    self.gateway.set_receipt(step["receipt"])
                if "block" in step:
                    self.gateway.block_next(step["block"])
                text = step.get("text")
                self.raw.append({"response_id": rid, "text": text, "status": step.get("status", "completed")})
                audio = b"\xff" * step.get("audio_bytes", 320)
                common = {"response_id": rid, "item_id": rid + "-item", "content_index": 0}
                await self.wire({"type": "response.output_audio.delta", **common,
                    "delta": base64.b64encode(audio).decode()})
                if text is not None:
                    await self.wire({"type": "response.output_audio_transcript.done", **common, "transcript": text})
            else:
                await self.wire({"type": "response.function_call_arguments.done", "response_id": rid,
                    "call_id": rid + "-tool", "name": step["name"], "arguments": json.dumps(step.get("arguments", {}))})
            response = {"id": rid, "status": step.get("status", "completed")}
            if response["status"] == "incomplete":
                response["status_details"] = {"reason": "max_output_tokens"}
            await self.wire({"type": "response.done", "response": response})
        else:
            raise ValueError(f"Not a provider event fixture: {kind}")

    async def step(self, step):
        kind = step["kind"]
        if kind == "batch":
            for event in step["events"]:
                await self.emit(event)
            await self.drain(playback=step.get("wait", True))
        elif kind == "ordinary_turns":
            for _ in range(step["count"]):
                await self.emit({"kind": "caller", "text": "What information is available?"})
                await self.drain()
        elif kind == "wait_gateway":
            await asyncio.wait_for(self.gateway.entered.wait(), 2)
        elif kind == "release":
            self.gateway.release.set()
            if self.bridge._playback_task:
                await asyncio.wait_for(asyncio.gather(self.bridge._playback_task, return_exceptions=True), 2)
        elif kind == "disconnect":
            self.provider._closed.set()
            self.provider._offer_event(None)
            try:
                await asyncio.wait_for(self.bridge.run(), 2)
            except RuntimeError as exc:
                self.observed_exception = str(exc)
        else:
            await self.emit(step)
            await self.drain(playback=step.get("wait", True))
        if "checkpoint" in step:
            self.contact_checkpoints.append({"id": step["checkpoint"], "contacts": self._contacts()})

    def _contacts(self):
        slots = self.session.captured_slots
        contacts = {}
        for kind in ("email", "phone"):
            capture = getattr(slots, f"{kind}_capture", None)
            contacts[kind] = {
                "value": getattr(slots, kind, None),
                "confirmed": bool(getattr(slots, f"{kind}_confirmed", False)),
                "capture_status": getattr(getattr(capture, "status", None), "value", None),
                "confirmation_evidence": getattr(capture, "confirmation_evidence", None),
                **{field: asdict(value) if (value := getattr(capture, field, None)) else None
                    for field in ("value_source", "confirmation_source", "status_source", "readback")},
            }
        return contacts

    def result(self):
        history = self.transcripts.get_transcript_json(self.call_id)
        function_results = [json.loads(message["item"]["output"]) for message in self.socket.sent
            if message.get("type") == "conversation.item.create"
            and message.get("item", {}).get("type") == "function_call_output"]
        repairs = [message for message in self.socket.sent if message.get("type") == "response.create"
            and "REPAIR THIS TURN" in message.get("response", {}).get("instructions", "")]
        normal_continuations = [message for message in self.socket.sent
            if message == {"type": "response.create"}]
        slots = self.session.captured_slots
        contacts = self._contacts()
        accepted = sum(attempt["receipt"].get("success") is True and
            attempt["receipt"].get("status") in {"accepted", "provider_accepted", "succeeded", "completed"}
            for attempt in self.action_attempts)
        observed = {
            "submissions": len(self.gateway.submissions), "clears": self.gateway.clears,
            "shutdowns": self.shutdown_count, "end_requested": bool(getattr(self.session, "_end_call_requested", False)),
            "dnc": bool(getattr(self.session, "_caller_opted_out", False)),
            "dnc_attempts": len(self.dnc_receipts),
            "dnc_acknowledged": self.bridge._opt_out_acknowledged,
            "llm_controls": [item["text"] for item in self.gateway.controls
                if item.get("type") == "llm_response"],
            "identity_introduced": self.bridge._live_state.identity_introduced,
            "opening_interrupted": self.bridge._opening_interrupted,
            "opening_state_on_wire": any("opening=interrupted" in message.get("session", {}).get("instructions", "")
                for message in self.socket.sent),
            "email_confirmed": bool(getattr(slots, "email_confirmed", False)),
            "email": getattr(slots, "email", None),
            "email_capture_status": contacts["email"]["capture_status"],
            "email_value_source": contacts["email"]["value_source"],
            "email_confirmation_source": contacts["email"]["confirmation_source"],
            "email_readback": contacts["email"]["readback"],
            "phone": contacts["phone"]["value"], "phone_confirmed": contacts["phone"]["confirmed"],
            "effect_attempts": len(self.action_attempts), "accepted_actions": accepted,
            "action_message_ids": {name: result.get("message_id") for name, result
                in action_results_for_session(self.session).items() if name != "end_call"},
            "action_results": {name: result for name, result
                in action_results_for_session(self.session).items() if name != "end_call"},
            "repair_requests": len(repairs), "normal_continuations": len(normal_continuations),
            "failure": bool(self.bridge._failure_reason), "connection_lost": self.bridge._connection_lost,
            "tool_statuses": [item.get("status") for item in function_results],
            "last_tool_success": self.bridge._live_state.last_tool_success,
            "delivered_text": getattr(self.session, "_voice_action_delivered_text", ""),
        }
        controls = [{"id": key, "pass": key in observed and observed[key] == expected,
            "detail": {"expected": expected, "observed": observed.get(key)}}
            for key, expected in self.scenario["expect"].items()]
        checkpoints = {item["id"]: item["contacts"] for item in self.contact_checkpoints}
        for name, contacts_expected in self.scenario.get("contact_checkpoint_expectations", {}).items():
            for kind, fields in contacts_expected.items():
                actual = checkpoints.get(name, {}).get(kind, {})
                for field, expected in fields.items():
                    controls.append({"id": f"contact:{name}:{kind}:{field}",
                        "pass": field in actual and actual[field] == expected,
                        "detail": {"expected": expected, "observed": actual.get(field),
                                   "checkpoint_present": name in checkpoints}})
        wire = self.initial_wire
        submitted_ids = {row["utterance_id"] for row in self.gateway.submissions}
        submitted_speech = [row["content"] for row in history if row["role"] == "assistant"
            and (row.get("metadata", {}).get("delivery") or {}).get("utterance_id") in submitted_ids
            and (row.get("metadata", {}).get("delivery") or {}).get("status") == "completed"
            and (row.get("metadata", {}).get("delivery") or {}).get("evidence") == "transport_played"]
        if "expected_submitted_speech" in self.scenario:
            expected = self.scenario["expected_submitted_speech"]
            controls.append({"id": "useful_submitted_speech", "pass": submitted_speech == expected,
                "detail": {"expected": expected, "observed": submitted_speech,
                           "scope": "Authored control preservation, not model comprehension or hearing."}})
            assistant_history = [turn["content"] for turn in history if turn["role"] == "assistant"]
            controls.append({"id": "useful_assistant_history", "pass": assistant_history == expected,
                "detail": {"expected": expected, "observed": assistant_history}})
        for index, expected in enumerate(self.scenario.get("knowledge_expectations", [])):
            actual = function_results[index] if index < len(function_results) else {}
            sources = [{key: source.get(key) for key in ("node_id", "version", "source_id", "source_version")}
                       for source in actual.get("sources", [])]
            knowledge_observed = {"status": actual.get("status"), "source_policy": actual.get("source_policy"),
                        "sources": sources}
            expected_identity = {key: expected[key] for key in knowledge_observed}
            controls.append({"id": f"knowledge_identity:{index}", "pass": knowledge_observed == expected_identity,
                "detail": {"expected": expected_identity, "observed": knowledge_observed}})
            text = actual.get("text", "")
            blocks = re.findall(r"<company_knowledge>(.*?)</company_knowledge>", text, re.DOTALL)
            controls.append({"id": f"knowledge_source_fence:{index}",
                "pass": any(expected["source_text"] in block for block in blocks),
                "detail": "Actual matched lookup output must contain the supplied source inside its data fence."})
        return {
            "scenario_id": self.scenario["id"], "semantic_ids": self.scenario["semantic_ids"],
            "engine": "native", "profile": self.profile,
            "provenance": {"type": "proposed_synthetic_control", "corpus_version": self.corpus["version"],
                "provider_calls": 0, "full_session_admission_exercised": False},
            "source_facts": self.scenario.get("source_facts", []),
            "requests": [{"model": self.provider._model, "instructions_sha256": hashlib.sha256(self.instructions.encode()).hexdigest(),
                "wire_sha256": _digest(wire), "messages": [], "instructions": self.instructions,
                "tool_names": [tool["name"] for tool in self.tools], "session_update": wire,
                "session_update_origin": "actual serializer; no handshake sent or acknowledged",
                "instruction_updates": [message["session"] for message in self.socket.sent
                    if message.get("type") == "session.update"],
                "repair_requests": repairs,
                "repair_request_origin": "actual adapter serializer; synthetic socket only",
                "provider_event_sha256": _digest(self.raw_events)}],
            "raw_output": self.raw, "candidate_text": [value["text"] for value in self.raw],
            "submitted_speech": submitted_speech,
            "submitted_speech_evidence": "Full utterances from correlated synthetic completion receipts only; not human hearing.",
            "history": history,
            "contacts": contacts,
            "contact_checkpoints": self.contact_checkpoints,
            "end": {"requested": observed["end_requested"], "shutdown_count": self.shutdown_count,
                "dnc_flag": observed["dnc"], "dnc_effect_count": None},
            "effects": {"attempts": [event["name"] for event in self.raw_events
                if event.get("type") == "response.function_call_arguments.done"], "accepted": accepted,
                "executor_attempts": self.action_attempts,
                "recorded_results": action_results_for_session(self.session),
                "external_execution": "not_run; any declared action receipt is a synthetic executor result",
                "tool_results": function_results,
                "dnc_persistence_receipts": self.dnc_receipts,
                "dnc_database_writes": 0},
            "media": {"generated_codec": "pcmu/8000", "submitted_codec": "pcm_s16le/8000",
                "generated_bytes": sum(len(base64.b64decode(event["delta"])) for event in self.raw_events
                    if event.get("type") == "response.output_audio.delta"),
                "submitted_bytes": sum(entry["pcm_bytes"] for entry in self.gateway.submissions),
                "submissions": self.gateway.submissions, "receipts": self.gateway.receipts,
                "receipt_origin": "synthetic gateway fixture", "clear_count": self.gateway.clears,
                "truncate_events": [event for event in self.socket.sent if event.get("type") == "conversation.item.truncate"]},
            "observed": observed,
            "findings": {"control": controls, "semantic": [{"id": "profile_quality", "status": "unreviewed",
                "detail": "Scripted responses test runtime admission, not whether this model produces the right answer."}]},
            "scope": "Offline real event parser + independent prompt composer + bridge; synthetic socket/media/source facts.",
            "limitations": ["No actual provider, STT, TTS, carrier or acoustic quality was evaluated.",
                "The provider/voice is a replay label, not an approved or available sale profile.",
                "No durable DNC, Lead, CRM or connector write occurred; downstream acceptance belongs to its package.",
                "Partial audio bytes have no word-level mapping; full candidate text is not claimed submitted or heard.",
                "Session creation/credential admission is separately covered by AG01, not this replay harness."],
        }

    async def run(self):
        # Keep the real bridge's task, acknowledgement and speech gates. Only
        # replace the external persistence port, including its bounded stop
        # drain. A scripted acknowledgement is not durable DNC evidence.
        with ExitStack() as patches:
            patches.enter_context(patch("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", self.persist_dnc))
            if self.fixture_actions:
                patches.enter_context(patch(
                    "app.domain.services.voice_pipeline.action_execution.execute_connected_voice_action",
                    self.execute_fixture_action))
            try:
                for step in self.scenario["steps"]:
                    await self.step(step)
                return self.result()
            finally:
                self.gateway.release.set()
                await self.bridge.stop()
                self.transcripts.clear_buffer(self.call_id)


def _profile(corpus, provider_name):
    saved = corpus["profiles"][provider_name]
    return {"provider": provider_name, "model": saved["model"], "voice": saved["voice"],
        "settings": saved["settings"], "mode": "offline_replay", "hidden_opt_in": provider_name == "xai"}


def _corpus(root):
    corpus_path = root / "backend/tests/fixtures/conversation/ag04_native.json"
    return json.loads(corpus_path.read_text(encoding="utf-8"))


def case_inventory(root: Path) -> list[dict]:
    corpus = _corpus(root)
    return [{"scenario_id": scenario["id"], "semantic_ids": scenario["semantic_ids"],
        "engine": "native", "profile": _profile(corpus, provider)}
        for scenario in corpus["scenarios"] for provider in corpus["providers"]]


async def run_cases(root: Path) -> list[dict]:
    corpus = _corpus(root)
    results = []
    for scenario in corpus["scenarios"]:
        for provider in corpus["providers"]:
            results.append(await NativeReplay(scenario, provider, corpus).run())
    return results
