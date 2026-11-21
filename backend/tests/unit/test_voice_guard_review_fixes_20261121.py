"""Review fixes for the self-dialogue guard (turn_streamer.py), 21 Nov 2026.

A2: a spoken lead-in before a tool call must not be read as a glued caller turn.
A1: a cut stops the SPEAKING, not the same round's tool calls.
A3: an email address or web address read aloud is not a turn boundary.

The two-round shape goes through the REAL ``stream_tool_turn`` with a fake
provider, so the real (space-less) round join is what the guard sees.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.action_tools import VOICE_ACTION_NAMES
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.llm.streaming import stream_tool_turn


def _call(name, args, cid="call_1"):
    raw = json.dumps(args)
    return {"id": cid, "name": name, "arguments": args, "arguments_raw": raw, "arguments_valid": True}


class _RoundsProvider:
    """Each provider round yields its tokens VERBATIM (no joining space)."""

    supports_tools = True

    def __init__(self, rounds):
        self.rounds = rounds
        self.n = 0
        self.extra_seen = []

    async def stream_chat_with_timeout(self, messages, timeout_seconds=None, tools=None,
                                       tool_choice=None, tool_calls_sink=None,
                                       extra_messages=None, **kwargs):
        self.extra_seen.append(extra_messages)
        tokens, calls = self.rounds[min(self.n, len(self.rounds) - 1)]
        self.n += 1
        for t in tokens:
            yield t
        if tool_calls_sink is not None:
            tool_calls_sink.extend(calls)

    def stream_chat_with_tools(self, messages, **kwargs):
        return stream_tool_turn(self, messages, **kwargs)


def _session(user_text):
    session = CallSession(
        call_id="call-review-fix-1", campaign_id="campaign-1", lead_id="lead-1",
        tenant_id="tenant-1", provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.", voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {n: "synthetic test executor" for n in VOICE_ACTION_NAMES}
    if user_text:
        session.conversation_history.append(Message(role=MessageRole.USER, content=user_text))
    return session


async def _run(llm, monkeypatch, user_text="What are your hours?"):
    # A long filler delay keeps the "One moment" filler out of the spoken list.
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "60000")
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=llm,
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    getattr(llm, 'bind', lambda svc: None)(service)
    session = _session(user_text)
    service._barge_in_events[session.call_id] = session.barge_in_event
    result = await service._stream_llm_and_tts(session)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, session, result


_LOOKUP = _call("send_email", {})


# ---------------------------------------------------------------- A2

@pytest.mark.asyncio
@pytest.mark.parametrize("lead_in", ["Let me check.", "Sure!"])
async def test_a2_lead_in_before_a_tool_call_is_spoken_with_the_answer(monkeypatch, lead_in):
    llm = _RoundsProvider([([lead_in], [_LOOKUP]), (["Every hour, from five am."], [])])
    spoken, session, _ = await _run(llm, monkeypatch)
    assert llm.n == 2
    assert " ".join(spoken) == f"{lead_in} Every hour, from five am."
    assert session._spoken_sentences == spoken


@pytest.mark.asyncio
async def test_a2_lead_in_split_over_tokens_before_a_tool_call(monkeypatch):
    llm = _RoundsProvider([(["Let me", " check."], [_LOOKUP]), (["Every", " hour."], [])])
    spoken, _, _ = await _run(llm, monkeypatch)
    assert " ".join(spoken) == "Let me check. Every hour."


@pytest.mark.asyncio
async def test_a2_guard_ignores_a_round_boundary_even_if_a_provider_forgets_the_space(monkeypatch):
    class _Forgetful(_RoundsProvider):
        # A provider that bypasses stream_tool_turn and glues the rounds itself,
        # running the tool between them through the caller's tool_runner.
        async def stream_chat_with_tools(self, messages, tool_runner=None, tools=None, **kw):
            yield "Let me check."
            await tool_runner("send_email", {})
            yield "Every hour, from five am."

    spoken, _, _ = await _run(_Forgetful([]), monkeypatch)
    assert " ".join(spoken) == "Let me check. Every hour, from five am."


@pytest.mark.asyncio
async def test_a2_the_space_is_between_rounds_only_and_payloads_are_untouched():
    args = {"to": "a@b.co", "subject": "Hi.There"}
    llm = _RoundsProvider([(["Let me check."], [_call("send_email", args)]), (["Done."], [])])
    seen = []

    async def runner(name, a):
        seen.append((name, a))
        return json.dumps({"success": True})

    tools = [{"type": "function", "function": {
        "name": "send_email", "parameters": {"type": "object", "additionalProperties": True}}}]
    out = [t async for t in stream_tool_turn(llm, [], tools=tools, tool_runner=runner, max_tool_rounds=3)]
    assert "".join(out) == "Let me check. Done."
    assert seen == [("send_email", args)]
    assistant = llm.extra_seen[1][0]
    assert assistant["content"] == "Let me check."  # no space leaks into the model's own history
    assert assistant["tool_calls"][0]["function"]["arguments"] == json.dumps(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds,expected", [
    # a JSON envelope is never given a separator, before or after
    ([([], [_LOOKUP]), (['{"action":"end_session","reason":"user_goodbye"}'], [])],
     '{"action":"end_session","reason":"user_goodbye"}'),
    ([(['{"action":"end_session",'], [_LOOKUP]), (['"reason":"user_goodbye"}'], [])],
     '{"action":"end_session","reason":"user_goodbye"}'),
    # a single round is yielded exactly as the provider wrote it
    ([(["Let me", " check."], [])], "Let me check."),
])
async def test_a2_envelope_and_single_round_streams_are_byte_identical(rounds, expected):
    llm = _RoundsProvider(rounds)

    async def runner(name, a):
        return json.dumps({"success": True})

    tools = [{"type": "function", "function": {
        "name": "send_email", "parameters": {"type": "object", "additionalProperties": True}}}]
    out = [t async for t in stream_tool_turn(llm, [], tools=tools, tool_runner=runner, max_tool_rounds=3)]
    assert "".join(out) == expected


# ---------------------------------------------------------------- A1

@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [
    ["Understood.", "Sorry for the trouble."],
    ["Understood.Sorry for the trouble."],
])
async def test_a1_a_cut_does_not_drop_the_same_rounds_do_not_call(monkeypatch, chunks):
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    end_call = _call("end_call", {
        "reason": "caller asked not to be called", "do_not_call": True,
        "opt_out_quote": "Don't bother us again",
    })
    llm = _RoundsProvider([(chunks, [end_call])])
    spoken, session, result = await _run(llm, monkeypatch, user_text="Don't bother us again.")
    assert purge.await_count == 1
    assert session._end_call_requested is True
    # N6: the accepted end_call after the cut gets the one default farewell.
    assert spoken == ["Understood.", "Goodbye, take care."]
    assert session._spoken_sentences == spoken
    assert "Sorry" not in str(result)
    assert all("Sorry" not in m.content for m in session.conversation_history)


@pytest.mark.asyncio
async def test_a1_the_close_still_follows_the_normal_rules(monkeypatch):
    # No opt-out words from the caller: the end_call is refused as usual, the call stays open.
    end_call = _call("end_call", {"reason": "done", "do_not_call": True, "opt_out_quote": "stop calling me"})
    llm = _RoundsProvider([(["Understood.", "Sorry for the trouble."], [end_call])])
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    spoken, session, _ = await _run(llm, monkeypatch, user_text="Tell me more about pricing.")
    assert purge.await_count == 0
    assert getattr(session, "_end_call_requested", False) is not True
    assert spoken == ["Understood."]


@pytest.mark.asyncio
async def test_a1_a_cut_does_not_start_another_provider_round(monkeypatch):
    llm = _RoundsProvider([(["Okay.Sure thing."], [_LOOKUP]), (["Every hour."], [])])
    spoken, _, _ = await _run(llm, monkeypatch)
    assert spoken == ["Okay."]
    assert "Every" not in " ".join(spoken)
    assert llm.n == 1, "a cut decided at the token must not start another provider round"


# ---------------------------------------------------------------- A3

@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,expected", [
    (["Is that John.Smith@gmail.com?"], "Is that John.Smith@gmail.com?"),
    (["Is that John.", "Smith@gmail.com?"], "Is that John.Smith@gmail.com?"),
    (["Sign up at https://acme.example/signup?ref=abc please."],
     "Sign up at https://acme.example/signup?ref=abc please."),
])
async def test_a3_emails_and_urls_are_spoken_in_full(monkeypatch, chunks, expected):
    class _S:
        async def stream_chat_with_timeout(self, *a, **k):
            for c in chunks:
                yield c

    spoken, _, _ = await _run(_S(), monkeypatch, user_text="Where do I sign up?")
    assert "".join(spoken) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [
    ["Is that right?Yes, that's right."],
    ["Is that right?", "Yes, that's right."],
    ["Is that your email?", "john@gmail.com is right."],
])
async def test_a3_the_original_glued_caller_turn_is_still_cut(monkeypatch, chunks):
    class _S:
        async def stream_chat_with_timeout(self, *a, **k):
            for c in chunks:
                yield c

    spoken, _, _ = await _run(_S(), monkeypatch, user_text="Hello")
    assert spoken[0] in ("Is that right?", "Is that your email?")
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_a3_bare_capitalised_domain_keeps_the_ambiguity_defence(monkeypatch):
    # "Acme.Com" cannot be told from "Okay.Sure" without a TLD catalogue (the
    # segmenter documents the same ambiguity), so the guard's purpose wins and a
    # bare word.Upper with no '@' or '/' is still cut.
    class _S:
        async def stream_chat_with_timeout(self, *a, **k):
            yield "Visit Acme.Com now."

    spoken, _, _ = await _run(_S(), monkeypatch, user_text="Where?")
    assert spoken == ["Visit Acme."]


@pytest.mark.asyncio
async def test_a1_envelope_in_the_cut_token_keeps_a_verified_do_not_call(monkeypatch):
    from app.domain.services.end_session_action import parse_end_session_action, verified_opt_out

    envelope = ('{"action":"end_session","reason":"user_goodbye","farewell":"Sorry, bye now.",'
                '"do_not_call":true,"opt_out_quote":"Don\'t bother us again"}')

    class _S:
        async def stream_chat_with_timeout(self, *a, **k):
            yield "Understood!"
            yield "Sorry for that. " + envelope

    spoken, _, result = await _run(_S(), monkeypatch, user_text="Don't bother us again.")
    action = parse_end_session_action(result[0])
    assert action and action["do_not_call"] is True
    assert verified_opt_out(action, "Don't bother us again.")
    assert action["farewell"] == "Goodbye, take care."  # the invented-dialogue farewell is not used
    assert spoken == ["Understood!"]


@pytest.mark.asyncio
async def test_a1_envelope_in_the_cut_token_without_caller_words_is_still_ignored(monkeypatch):
    from app.domain.services.end_session_action import parse_end_session_action

    envelope = ('{"action":"end_session","reason":"user_goodbye","farewell":"Bye.",'
                '"do_not_call":true,"opt_out_quote":"stop calling me"}')

    class _S:
        async def stream_chat_with_timeout(self, *a, **k):
            yield "Understood!"
            yield "Sorry. " + envelope

    _, _, result = await _run(_S(), monkeypatch, user_text="Tell me about pricing.")
    assert parse_end_session_action(result[0]) is None


# ---------------------------------------------------------------- N5 (real token shapes)

class _Tokens:
    def __init__(self, tokens):
        self._tokens = tokens

    async def stream_chat_with_timeout(self, *a, **k):
        for t in self._tokens:
            yield t


@pytest.mark.asyncio
async def test_n5_capitalised_email_read_back_in_separate_tokens_is_spoken_in_full(monkeypatch):
    spoken, _, _ = await _run(
        _Tokens(["Is your email ", "John", ".", "Smith", "@", "gmail.com", "?"]),
        monkeypatch, user_text="It is john.smith at gmail.",
    )
    # 21 Nov: the address is one spoken piece now (it used to be split at the dot).
    assert spoken == ["Is your email John.Smith@gmail.com?"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens,expected", [
    (["Is that john@gmail.com?Yes that's right."], "Is that john@gmail.com?"),
    (["Is that ", "john", "@", "gmail.com", "?", "Yes", " that's", " right."], "Is that john@gmail.com?"),
    (["So that's john@example.com.Yes that's right."], "So that's john@example.com."),
    (["So that's ", "john", "@", "example.com", ".", "Yes", " that's", " right."], "So that's john@example.com."),
])
async def test_n5_a_caller_turn_glued_after_an_address_is_cut(monkeypatch, tokens, expected):
    spoken, session, _ = await _run(_Tokens(tokens), monkeypatch, user_text="Hello")
    assert "".join(spoken) == expected
    assert "Yes" not in " ".join(session._spoken_sentences)


@pytest.mark.asyncio
async def test_n5_url_in_small_tokens_is_spoken_in_full(monkeypatch):
    spoken, _, _ = await _run(
        _Tokens(["Sign up at ", "https://", "example.com", "/signup", "?", "ref", "=", "abc", " please."]),
        monkeypatch, user_text="Where?",
    )
    assert "".join(spoken) == "Sign up at https://example.com/signup?ref=abc please."


@pytest.mark.asyncio
async def test_n5_query_string_without_a_slash_is_not_cut(monkeypatch):
    spoken, _, _ = await _run(_Tokens(["Go to acme.com?ref=abc now."]), monkeypatch, user_text="Where?")
    assert "".join(spoken) == "Go to acme.com?ref=abc now."


@pytest.mark.asyncio
async def test_n5_original_glued_turn_is_still_cut_in_small_tokens(monkeypatch):
    spoken, _, _ = await _run(
        _Tokens(["Is that ", "right", "?", "Yes", ", that's", " right."]), monkeypatch, user_text="Hello",
    )
    assert spoken == ["Is that right?"]


@pytest.mark.asyncio
async def test_n5_glued_turn_at_the_very_end_of_the_stream_is_still_cut(monkeypatch):
    spoken, _, _ = await _run(_Tokens(["Is that right?Yes"]), monkeypatch, user_text="Hello")
    assert spoken == ["Is that right?"]


# ---------------------------------------------------------------- N6

@pytest.mark.asyncio
async def test_n6_accepted_end_call_after_a_cut_gets_one_default_farewell(monkeypatch):
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell",
                        AsyncMock(return_value=True))
    end_call = _call("end_call", {"reason": "caller asked not to be called", "do_not_call": True,
                                  "opt_out_quote": "Don't bother us again"})
    llm = _RoundsProvider([(["Is that all?", "Yes, that's all."], [end_call])])
    spoken, session, _ = await _run(llm, monkeypatch, user_text="Don't bother us again.")
    assert spoken == ["Is that all?", "Goodbye, take care."]
    assert session._spoken_sentences == spoken


@pytest.mark.asyncio
async def test_n6_no_second_farewell_when_the_spoken_words_already_said_goodbye(monkeypatch):
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell",
                        AsyncMock(return_value=True))
    end_call = _call("end_call", {"reason": "caller asked not to be called", "do_not_call": True,
                                  "opt_out_quote": "Don't bother us again"})
    llm = _RoundsProvider([(["Goodbye, take care!", "Yes, thanks."], [end_call])])
    spoken, _, _ = await _run(llm, monkeypatch, user_text="Don't bother us again.")
    assert spoken == ["Goodbye, take care!"]


@pytest.mark.asyncio
async def test_n6_no_farewell_when_the_end_call_was_refused(monkeypatch):
    end_call = _call("end_call", {"reason": "done"})
    llm = _RoundsProvider([(["Is that all?", "Yes, that's all."], [end_call])])
    spoken, _, _ = await _run(llm, monkeypatch, user_text="Tell me about pricing.")
    assert spoken == ["Is that all?"]


# ---------------------------------------------------------------- R3 (a late-decided cut starts no round)

@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [
    ["Happy to help for you.Sure"],
    ["Happy", " to", " help", " for", " you", ".", "Sure"],
])
async def test_r3_a_cut_decided_late_does_not_start_another_provider_round(monkeypatch, tokens):
    # The glued word is the LAST word of the round: the boundary is still held
    # when the round's tool call runs.
    llm = _RoundsProvider([(tokens, [_LOOKUP]), (["Every hour."], [])])
    spoken, _, _ = await _run(llm, monkeypatch)
    assert llm.n == 1
    assert spoken == ["Happy to help for you."]


# ---------------------------------------------------------------- R2 (hold length, errors)

class _Probe:
    """Records how many sentences had reached TTS before each token was handed over."""

    def __init__(self, tokens, error=None):
        self.tokens, self.error, self.counts, self._service = tokens, error, [], None

    def bind(self, service):
        self._service = service

    def _sent(self):
        return len(self._service.synthesize_and_send_audio.await_args_list)

    async def stream_chat_with_timeout(self, *a, **k):
        try:
            for t in self.tokens:
                self.counts.append(self._sent())
                yield t
            self.counts.append(self._sent())
            if self.error is not None:
                raise self.error
        finally:
            # also reached when the streamer stops reading (a cut) and closes us
            if len(self.counts) <= len(self.tokens):
                self.counts.append(self._sent())


@pytest.mark.asyncio
async def test_r2_a_cut_is_decided_at_the_first_whitespace_not_at_the_end(monkeypatch):
    probe = _Probe(["We", " open", " at", " 5am", ".", "We", " close", " at", " 9pm", "."])
    spoken, _, _ = await _run(probe, monkeypatch, user_text="When?")
    assert spoken == ["We open at 5am."]
    # counts[i] = sentences sent before token i was handed over: the sentence
    # is sent while token 6 (" close", the first whitespace after "We") is processed.
    assert probe.counts[6] == 0 and probe.counts[7] == 1


@pytest.mark.asyncio
async def test_r2_an_address_is_one_piece_and_does_not_hold_its_sentence_back(monkeypatch):
    # 21 Nov: an address is one spoken piece now (it used to be held, then split at
    # the dot); this keeps the timing rule: the sentence is sent at the token after
    # its real end, not at the end of the stream.
    probe = _Probe(["Email", " us", " at", " John", ".", "Smith", "@", "gmail.com", ",", " please", ".",
                    " Thanks", "."])
    spoken, _, _ = await _run(probe, monkeypatch, user_text="How?")
    assert spoken == ["Email us at John.Smith@gmail.com, please.", "Thanks."]
    # token 11 (" Thanks") follows the final "." (token 10): the piece is sent while it is processed
    assert probe.counts[11] == 0 and probe.counts[12] == 1


@pytest.mark.asyncio
async def test_r2_a_stream_error_during_a_hold_does_not_lose_the_sentence(monkeypatch):
    from app.infrastructure.llm.groq import LLMTimeoutError

    probe = _Probe(["We", " open", " at", " 5am", ".", "We"], error=LLMTimeoutError("stalled"))
    spoken, session, _ = await _run(probe, monkeypatch, user_text="When?")
    # main speaks the sentence before the terminator the moment "We" arrives,
    # then drops the rest on the error; the hold must not turn that into the apology.
    assert spoken == ["We open at 5am."]
    assert session._spoken_sentences == ["We open at 5am."]


@pytest.mark.asyncio
async def test_r2_a_plain_stream_error_without_a_hold_is_unchanged(monkeypatch):
    from app.infrastructure.llm.groq import LLMTimeoutError

    probe = _Probe(["We open at 5am. We close"], error=LLMTimeoutError("stalled"))
    spoken, _, _ = await _run(probe, monkeypatch, user_text="When?")
    assert spoken == ["We open at 5am."]


# ---------------------------------------------------------------- R4 (only end_call acts after a cut)

class _RecordingProvider(_RoundsProvider):
    def __init__(self, rounds):
        super().__init__(rounds)
        self.results = []

    def stream_chat_with_tools(self, messages, tool_runner=None, **kwargs):
        async def rec(name, args):
            result = await tool_runner(name, args)
            self.results.append((name, result))
            return result

        return stream_tool_turn(self, messages, tool_runner=rec, **kwargs)


_CUT_SHAPES = {
    "one_token": ["Is that right?Yes"],
    "edge": ["Is that right?", "Yes"],
    "late": ["Is", " that", " right", "?", "Yes"],
}
_TOOLS = {
    "schedule_callback": {"requested_time": "2026-12-01T10:00:00Z", "timezone": "Europe/London"},
    "transfer_call": {"destination": "sales"},
    "record_contact": {"kind": "email", "operation": "set", "value": "invented@example.com",
                       "expected_value": None, "source_quote": "yes that is right"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(_CUT_SHAPES))
@pytest.mark.parametrize("tool", list(_TOOLS))
async def test_r4_after_a_cut_no_tool_but_end_call_runs(monkeypatch, shape, tool):
    action = AsyncMock(return_value={"success": True, "status": "done", "action": tool})
    contact = AsyncMock(return_value={"saved": True})
    mod = "app.domain.services.voice_pipeline.turn_streamer"
    monkeypatch.setattr(f"{mod}.run_voice_action", action)
    monkeypatch.setattr(f"{mod}.record_contact", contact)
    llm = _RecordingProvider([
        (_CUT_SHAPES[shape], [_call(tool, _TOOLS[tool])]),
        ([], [_call("transfer_call", {"destination": "sales"}, "call_2")]),
        (["Every hour."], []),
    ])
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "60000")
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=llm, tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _session("What are your hours?")
    session._contact_turn = object()  # offers the contact tool
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    assert action.await_count == 0 and contact.await_count == 0
    assert [name for name, _ in llm.results] == [tool]
    assert json.loads(llm.results[0][1])["status"] == "caller_turn_cut"
    assert llm.n == 1, "no further provider request after the cut"
    assert spoken == ["Is that right?"]


@pytest.mark.asyncio
async def test_r4_end_call_with_a_do_not_call_runs_once_after_an_edge_cut(monkeypatch):
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", purge)
    end_call = _call("end_call", {"reason": "caller asked not to be called", "do_not_call": True,
                                  "opt_out_quote": "Don't bother us again"})
    llm = _RecordingProvider([(["Is that right?", "Yes"], [end_call])])
    spoken, session, _ = await _run(llm, monkeypatch, user_text="Don't bother us again.")
    assert purge.await_count == 1
    assert [n for n, _ in llm.results] == ["end_call"]
    assert session._end_call_requested is True
    assert spoken[0] == "Is that right?"


@pytest.mark.asyncio
async def test_r4_a_turn_without_a_cut_still_runs_its_tools(monkeypatch):
    action = AsyncMock(return_value={"success": True, "status": "done", "action": "send_email"})
    monkeypatch.setattr("app.domain.services.voice_pipeline.turn_streamer.run_voice_action", action)
    llm = _RecordingProvider([(["Let me check."], [_LOOKUP]), (["Every hour."], [])])
    spoken, _, _ = await _run(llm, monkeypatch)
    assert action.await_count == 1 and llm.n == 2
    assert " ".join(spoken) == "Let me check. Every hour."
