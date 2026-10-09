"""Exercise the production STT adapter with an explicitly supplied test WAV.

Run from backend: python -m scripts.probe_assemblyai --wav synthetic.wav
The clip is sent to AssemblyAI once per requested mode. Credentials are read
only from ASSEMBLYAI_API_KEY. No credential, audio, or transcript is logged.
Use --expect to check a known synthetic email/name against the final result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import wave
from pathlib import Path

from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.infrastructure.stt.assemblyai import AssemblyAISTTProvider


def read_clip(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getcomptype() != "NONE":
            raise ValueError("Use an uncompressed mono 16-bit PCM WAV")
        rate = clip.getframerate()
        duration = clip.getnframes() / rate
        if not 8000 <= rate <= 96000 or not 0 < duration <= 30:
            raise ValueError("Use a WAV of at most 30 seconds, at 8000–96000 Hz")
        return clip.readframes(clip.getnframes()), rate


async def probe(pcm: bytes, rate: int, mode: str, expected: list[str]) -> dict:
    provider = AssemblyAISTTProvider()
    call_id = f"assemblyai-synthetic-{mode}"
    started = time.monotonic()
    finals: list[str] = []
    ended_turns = 0

    async def audio():
        # Existing telephone input uses 20 ms chunks; exercise adapter framing.
        size = rate * 2 // 50
        for offset in range(0, len(pcm), size):
            yield AudioChunk(data=pcm[offset : offset + size], sample_rate=rate)
        # Give all three mode presets enough silence to finalize naturally.
        for _ in range(150):
            yield AudioChunk(data=b"\x00" * size, sample_rate=rate)

    try:
        await provider.initialize(
            {
                "api_key": os.environ["ASSEMBLYAI_API_KEY"],
                "sample_rate": rate,
                "assemblyai_settings": {"mode": mode},
            }
        )
        # pre_connect succeeds only after the provider acknowledges model/mode.
        await provider.pre_connect(call_id)
        await provider.update_agent_context(call_id, "Please tell me your name and email address.")
        async with asyncio.timeout(60):
            async for item in provider.stream_transcribe(audio(), language="en", call_id=call_id):
                if isinstance(item, TranscriptChunk) and item.is_final:
                    if item.text:
                        finals.append(item.text)
                    else:
                        ended_turns += 1
        transcript = " ".join(finals).casefold()
        matches = [needle.casefold() in transcript for needle in expected]
        success = bool(finals) and ended_turns == len(finals) and all(matches)
        return {
            "mode": mode,
            "success": success,
            "model_acknowledged": "universal-3-6-pro",
            "language_sent": ["en"],
            "final_turns": len(finals),
            "end_markers": ended_turns,
            "expected_matches": matches,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        await provider.cleanup()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument(
        "--mode", action="append", choices=["balanced", "min_latency", "max_accuracy"]
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="Synthetic text that must appear in final recognition",
    )
    args = parser.parse_args()
    if not os.getenv("ASSEMBLYAI_API_KEY", "").strip():
        print(json.dumps({"success": False, "reason": "ASSEMBLYAI_API_KEY is not configured"}))
        return 2
    try:
        pcm, rate = read_clip(args.wav)
    except (OSError, ValueError, wave.Error):
        print(
            json.dumps(
                {
                    "success": False,
                    "reason": "Invalid test WAV: mono PCM16, 8–96 kHz, at most 30 seconds required",
                }
            )
        )
        return 2
    results = []
    for mode in args.mode or ["balanced", "min_latency", "max_accuracy"]:
        try:
            results.append(await probe(pcm, rate, mode, args.expect))
        except Exception as exc:
            # Even a library exception can contain a URL with contextual PII.
            results.append({"mode": mode, "success": False, "error_type": type(exc).__name__})
    print(
        json.dumps(
            {
                "audio_sha256": hashlib.sha256(pcm).hexdigest(),
                "sample_rate": rate,
                "results": results,
            },
            indent=2,
        )
    )
    return 0 if all(result["success"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
