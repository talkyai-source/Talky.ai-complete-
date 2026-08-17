"""A TTS provider that returns nothing, cleanly, and leaves the caller in silence.

2026-08-17, call b3350aee: a turn was recorded as
``turn_silent_reason reason=provider_empty_stream``. The agent had composed a
reply and spoke none of it — dead air on a live phone call, and no recovery of
any kind ran.

The reason it had no recovery is the reason it had no symptom. This loop already
protected against the two failure modes that announce themselves:

  * the provider RAISES        -> caught, and a spoken fallback plays;
  * the provider goes QUIET    -> asyncio.TimeoutError, and the synthesis is
                                  retried once.

A provider that ends its stream immediately with zero chunks does neither. It
does not raise, and it cannot time out, because there is nothing to wait for.
``StopAsyncIteration`` arrives instantly and looks exactly like a completed
utterance — the same shape as the STT socket that accepted 400 chunks and
answered nothing on 2026-08-13.

These tests pin the recovery and, just as importantly, pin that it does not fire
on healthy traffic: a retry that triggered on a normal turn would double every
sentence the agent says.
"""
from __future__ import annotations

import types

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback


class _Chunk:
    def __init__(self, data: bytes):
        self.data = data


class _Provider:
    """Yields ``scripts[n]`` on the n-th stream_synthesize call, so a test can
    say "empty first, audio on the retry" and observe which path ran."""

    name = "fake-tts"

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = 0
        self.texts = []

    def stream_synthesize(self, text, **kwargs):
        idx = self.calls
        self.calls += 1
        self.texts.append(text)
        chunks = self._scripts[idx] if idx < len(self._scripts) else []

        async def _gen():
            for c in chunks:
                yield _Chunk(c)

        return _gen()


class _Gateway:
    def __init__(self):
        self.sent = []

    async def send_audio(self, call_id, raw):
        self.sent.append(raw)

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


class _Latency:
    def mark_tts_first_chunk(self, *a, **k): pass
    def mark_response_start(self, *a, **k): pass
    def mark_audio_start(self, *a, **k): pass
    def mark_tts_end(self, *a, **k): pass
    def mark_completed(self, *a, **k): pass
    def mark_interrupted(self, *a, **k): pass


class _Pipeline:
    def __init__(self, provider):
        self.tts_provider = provider
        self.media_gateway = _Gateway()
        self.latency_tracker = _Latency()
        self.tts_sample_rate = 16000
        self.silent_turns = []

    def _record_silent_turn(self, call_id, reason):
        self.silent_turns.append(reason)


def _session():
    from app.domain.models.session import CallState

    return types.SimpleNamespace(
        call_id="b3350aee-76aa-4248-89fb-acc13d8ceddd",
        state=CallState.SPEAKING,
        tts_active=True,
        voice_id="v1",
        current_ai_response="",
        current_user_input="",
    )


async def _speak(provider, text="Thursday it is."):
    pipe = _Pipeline(provider)
    pb = TtsPlayback(pipeline=pipe)
    interrupted = await pb.synthesize_and_send(_session(), text, None, track_latency=False)
    return pipe, interrupted


# ── the failure, and its recovery ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_empty_stream_is_retried_once():
    """THE FIX. First synthesis returns nothing; the retry returns audio, and
    the caller hears the reply they were supposed to hear."""
    provider = _Provider([[], [b"\x01\x02" * 80]])
    pipe, interrupted = await _speak(provider)

    assert provider.calls == 2, "the empty stream was not retried"
    assert pipe.media_gateway.sent, "the caller heard nothing after a successful retry"
    assert interrupted is False
    assert pipe.silent_turns == [], "a recovered turn must not be recorded as silent"


@pytest.mark.asyncio
async def test_the_retry_asks_for_the_same_words():
    """The retry must re-synthesise the ORIGINAL reply, not a substitute — the
    text was fine, only the audio was missing."""
    provider = _Provider([[], [b"\x01\x02" * 80]])
    await _speak(provider, "Tuesday works — shall I book it?")

    assert provider.texts[:2] == [
        "Tuesday works — shall I book it?",
        "Tuesday works — shall I book it?",
    ]


@pytest.mark.asyncio
async def test_two_empty_streams_still_produce_speech():
    """If the retry is empty too, the turn must not end in silence. Dead air is
    the worst outcome on a phone call — the caller cannot tell it from a dropped
    line, so they hang up."""
    provider = _Provider([[], [], [b"\x09" * 40]])
    pipe, _ = await _speak(provider)

    assert provider.calls == 3, "no fallback was spoken after two empty streams"
    assert pipe.media_gateway.sent, "the caller still heard nothing"
    assert provider.texts[2] != provider.texts[0], (
        "the fallback re-sent the text that had already failed twice"
    )


@pytest.mark.asyncio
async def test_total_tts_failure_is_still_recorded_as_a_silent_turn():
    """When even the fallback yields nothing there is genuinely no audio. That
    must be REPORTED, not swallowed — an unrecoverable turn we know about is
    worth far more than one we do not."""
    provider = _Provider([[], [], []])
    pipe, _ = await _speak(provider)

    assert pipe.media_gateway.sent == []
    assert "provider_empty_stream" in pipe.silent_turns


# ── it must not fire on healthy traffic ─────────────────────────────────────

@pytest.mark.asyncio
async def test_a_working_provider_is_synthesised_exactly_once():
    """The load-bearing negative. A retry that fired on a normal turn would
    make the agent say every sentence twice."""
    provider = _Provider([[b"\x01" * 80, b"\x02" * 80]])
    pipe, _ = await _speak(provider)

    assert provider.calls == 1
    assert len(pipe.media_gateway.sent) == 2
    assert pipe.silent_turns == []


@pytest.mark.asyncio
async def test_a_provider_that_yields_one_chunk_then_stops_is_not_retried():
    """Audio HAS reached the caller, so re-synthesising would repeat words they
    already heard. The retry is only ever safe before the first chunk."""
    provider = _Provider([[b"\x01" * 80], [b"\xff" * 80]])
    pipe, _ = await _speak(provider)

    assert provider.calls == 1, "retried after audio had already played"
    assert len(pipe.media_gateway.sent) == 1


# ── the caller's own interruption is not a provider fault ───────────────────
#
# THE REGRESSION THIS SECTION EXISTS TO PREVENT.
#
# The production incident that motivated the retry (b3350aee, 21:02:29) was not
# a broken provider at all. The journal shows the silent turn landing INSIDE an
# interrupt teardown:
#
#   21:02:29 barge_in_detected
#   21:02:29 interrupt_step=begin           interrupt_id=5fcc4011a71a
#   21:02:29 interrupt_step=state_listening
#   21:02:29 interrupt_step=task_cancelled
#   21:02:29 interrupt_step=buffers_cleared
#   21:02:29 interrupt_step=cpp_interrupt
#   21:02:29 turn_silent_reason reason=provider_empty_stream   <-- here
#   21:02:29 interrupt_step=tts_provider_cleared
#
# The caller interrupted before the reply began playing, so the turn correctly
# produced no audio — and it was filed as a provider fault. A retry driven by
# that label would re-synthesise the reply and speak it over the person who had
# just interrupted, which is worse than the silence it set out to fix.

class _InterruptedProvider(_Provider):
    """An empty stream produced BECAUSE an interrupt ran mid-turn.

    ``synthesize_and_send`` sets ``tts_active = True`` on entry, so the flag
    cannot be pre-set by a test — it has to be cleared while the turn is in
    flight, which is exactly what ``interrupt_playback``'s first step does and
    exactly the ordering the b3350aee journal shows.
    """

    def __init__(self, scripts, session):
        super().__init__(scripts)
        self._session = session

    def stream_synthesize(self, text, **kwargs):
        gen = super().stream_synthesize(text, **kwargs)
        session = self._session

        async def _wrapped():
            session.tts_active = False   # <- interrupt_playback step 1
            async for c in gen:
                yield c

        return _wrapped()


async def _speak_interrupted(scripts, *, via_event: bool):
    """Reproduce a barge-in landing before the first chunk, by each of the two
    routes the pipeline actually uses."""
    import asyncio

    session = _session()
    event = None
    if via_event:
        provider = _Provider(scripts)
        event = asyncio.Event()
        event.set()
    else:
        provider = _InterruptedProvider(scripts, session)
    pipe = _Pipeline(provider)
    pb = TtsPlayback(pipeline=pipe)
    await pb.synthesize_and_send(
        session, "Thursday it is.", None,
        barge_in_event=event, track_latency=False,
    )
    return pipe


@pytest.mark.parametrize("via_event", [True, False])
@pytest.mark.asyncio
async def test_a_barge_in_before_the_first_chunk_is_never_retried(via_event):
    """The agent must NOT re-speak a reply the caller talked over."""
    pipe = await _speak_interrupted([[], [b"\x01" * 80]], via_event=via_event)

    assert pipe.media_gateway.sent == [], "spoke over a caller who interrupted"
    assert pipe.tts_provider.calls <= 1, "re-synthesised a reply the caller interrupted"


@pytest.mark.asyncio
async def test_a_barge_in_before_the_first_chunk_gets_no_fallback_either():
    """"Sorry — could you say that again?" is exactly the wrong thing to say to
    someone who just interrupted you."""
    pipe = await _speak_interrupted([[], [], [b"\x01" * 80]], via_event=False)

    assert pipe.media_gateway.sent == []
    assert all("say that again" not in t for t in pipe.tts_provider.texts)


@pytest.mark.asyncio
async def test_the_two_silent_causes_are_labelled_differently():
    """The mislabel is the defect. A caller-stopped turn and a dead provider
    need different names or the logs send the next person down the same wrong
    path this one did."""
    stopped = await _speak_interrupted([[]], via_event=False)
    assert stopped.silent_turns == ["interrupted_before_audio"]

    genuine, _ = await _speak(_Provider([[], [], []]))
    assert genuine.silent_turns == ["provider_empty_stream"]


@pytest.mark.asyncio
async def test_empty_chunks_do_not_count_as_audio():
    """A provider emitting zero-length chunks has still delivered nothing
    audible; that is the empty-stream case wearing a disguise."""
    provider = _Provider([[b"", b""], [b"\x07" * 80]])
    pipe, _ = await _speak(provider)

    assert provider.calls == 2
    assert pipe.media_gateway.sent == [b"\x07" * 80]
