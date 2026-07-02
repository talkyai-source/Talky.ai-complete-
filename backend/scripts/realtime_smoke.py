"""Standalone smoke test for the OpenAI gpt-realtime-2 bridge (Phase 1).

Run this ON THE SERVER, where OPENAI_API_KEY lives in backend/.env. It proves
the round-trip end to end WITHOUT touching the live gateway or the cascaded
pipeline:

    session.connect()  ->  drive ONE exchange  ->  capture agent audio + text

Two modes
---------
  TEXT-IN  (default): create a user text turn + response.create, then capture
           the agent's spoken transcript (printed) and its μ-law audio (saved).
           Proves voice + instructions + expressiveness with no mic.

  AUDIO-IN (bonus):   pass a raw μ-law/8kHz sample path as argv[1]; it is
           streamed via input_audio_buffer.append and the response captured.

Usage
-----
    cd backend
    python scripts/realtime_smoke.py                 # text-in
    python scripts/realtime_smoke.py path/to/caller_ulaw_8k.raw   # audio-in

Outputs (written next to this script's CWD):
    realtime_smoke_out.raw   raw μ-law 8kHz bytes from the model
    realtime_smoke_out.wav   same audio decoded to PCM16 WAV (playable)

Prints: connection ok, session.updated ok, the agent transcript, bytes of
audio received, function-call requests (if any), and any errors.
"""
from __future__ import annotations

import asyncio
import os
import sys
import wave

# ── Top-of-file knobs ───────────────────────────────────────────────────────
TEXT_PROMPT = (
    "Hi! I just wanted to hear how you sound. Can you introduce yourself in a "
    "friendly way and tell me a quick joke?"
)
VOICE = "marin"
MODEL = "gpt-realtime-2"
# How long to collect the agent's response before we stop and report.
RESPONSE_COLLECT_TIMEOUT_S = 25.0
# For AUDIO-IN: pace frames like a real call (20 ms μ-law frames = 160 bytes).
AUDIO_FRAME_BYTES = 160
AUDIO_FRAME_PACING_S = 0.02
RAW_OUT_PATH = "realtime_smoke_out.raw"
WAV_OUT_PATH = "realtime_smoke_out.wav"

# Make `app...` importable when run from backend/.
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.infrastructure.realtime.openai_realtime import (  # noqa: E402
    OpenAIRealtimeSession,
    knowledge_lookup_tool,
)
from app.services.scripts.realtime_instructions import (  # noqa: E402
    RealtimePersona,
    build_realtime_instructions,
)

SAMPLE_PERSONA = RealtimePersona(
    agent_name="Alex",
    company_name="Talky",
    role="a warm, upbeat voice assistant",
    goal="greet the caller, sound genuinely human, and be helpful",
)


def _write_wav_from_mulaw(mulaw: bytes, path: str) -> None:
    """Decode μ-law 8kHz to PCM16 and write a playable WAV. Uses audioop when
    available (Python <=3.12); falls back to a raw-only note otherwise."""
    try:
        import audioop  # deprecated in 3.13 but present in the 3.11 venv
        pcm16 = audioop.ulaw2lin(mulaw, 2)
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not decode μ-law to WAV: {exc}; raw file still saved)")
        return
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm16)


async def run() -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment / .env")
        return 2
    print(f"OPENAI_API_KEY loaded ({api_key[:5]}...{api_key[-4:]})")

    audio_in_path = sys.argv[1] if len(sys.argv) > 1 else None
    mode = "AUDIO-IN" if audio_in_path else "TEXT-IN"
    print(f"Mode: {mode}   Model: {MODEL}   Voice: {VOICE}")

    instructions = build_realtime_instructions(SAMPLE_PERSONA)
    print(f"Instructions composed: {len(instructions)} chars, "
          f"{instructions.count(chr(10)) + 1} lines")

    session = OpenAIRealtimeSession(
        api_key=api_key,
        model=MODEL,
        voice=VOICE,
        instructions=instructions,
        tools=[knowledge_lookup_tool()],
        call_id="smoke-test",
    )

    ok = await session.connect()
    if not ok:
        print("FAIL: connect() returned False (see logs above)")
        return 1
    print("OK: connected, session.created + session.updated handshake complete")

    # Drive one exchange.
    if audio_in_path:
        try:
            with open(audio_in_path, "rb") as f:
                sample = f.read()
        except OSError as exc:
            print(f"FAIL: cannot read audio sample {audio_in_path}: {exc}")
            await session.close()
            return 1
        print(f"Streaming {len(sample)} μ-law bytes "
              f"(~{len(sample) / 8000:.1f}s) as 20ms frames...")
        for i in range(0, len(sample), AUDIO_FRAME_BYTES):
            await session.send_caller_audio(sample[i:i + AUDIO_FRAME_BYTES])
            await asyncio.sleep(AUDIO_FRAME_PACING_S)
        # Semantic VAD will detect end-of-turn from the trailing silence; if the
        # sample has no trailing silence, the server may wait — that's expected.
    else:
        print(f"Sending text turn: {TEXT_PROMPT!r}")
        await session.send_text(TEXT_PROMPT)

    # Collect the response.
    agent_words: list[str] = []
    caller_words: list[str] = []
    audio = bytearray()
    function_calls: list[str] = []
    errors: list[str] = []

    async def collect() -> None:
        async for ev in session.events():
            if ev.kind == "audio" and ev.audio:
                audio.extend(ev.audio)
            elif ev.kind == "agent_transcript" and ev.text:
                agent_words.append(ev.text)
            elif ev.kind == "caller_transcript" and ev.text:
                caller_words.append(ev.text)
            elif ev.kind == "function_call" and ev.function_call:
                fc = ev.function_call
                function_calls.append(f"{fc.name}({fc.arguments})")
                print(f"  [function_call] {fc.name} args={fc.arguments}")
                # Fulfil with a stub so the model can finish speaking.
                await session.send_function_result(
                    fc.call_id, {"result": "No knowledge base wired in smoke test."}
                )
            elif ev.kind == "interrupted":
                print("  [interrupted] caller barge-in — stale audio flushed")
            elif ev.kind == "response_done":
                print("  [response.done] model finished a response turn")
                break
            elif ev.kind == "error" and ev.text:
                errors.append(ev.text)
                print(f"  [error] {ev.text}")

    try:
        await asyncio.wait_for(collect(), timeout=RESPONSE_COLLECT_TIMEOUT_S)
    except asyncio.TimeoutError:
        print(f"  (stopped collecting after {RESPONSE_COLLECT_TIMEOUT_S}s)")

    # Report.
    print("\n───────── RESULTS ─────────")
    if caller_words:
        print(f"Caller transcript:  {''.join(caller_words)!r}")
    print(f"Agent transcript:   {''.join(agent_words)!r}")
    print(f"Audio received:     {len(audio)} μ-law bytes "
          f"(~{len(audio) / 8000:.2f}s @ 8kHz)")
    print(f"Function calls:     {function_calls or 'none'}")
    print(f"Errors:             {errors or 'none'}")
    print(f"Stats:              {session.stats.to_dict()}")

    if audio:
        with open(RAW_OUT_PATH, "wb") as f:
            f.write(bytes(audio))
        print(f"Saved raw μ-law -> {RAW_OUT_PATH}")
        _write_wav_from_mulaw(bytes(audio), WAV_OUT_PATH)
        if os.path.exists(WAV_OUT_PATH):
            print(f"Saved WAV       -> {WAV_OUT_PATH}")

    await session.close()
    print("Closed cleanly.")
    ok_overall = bool(agent_words or audio) and not errors
    print(f"\n{'PASS' if ok_overall else 'CHECK'}: "
          f"{'round-trip proven' if ok_overall else 'review output above'}")
    return 0 if ok_overall else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
