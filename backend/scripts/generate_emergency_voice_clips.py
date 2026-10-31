"""Generate the provider-independent emergency telephone voice clips.

This is a maintainer tool, not a production dependency. On Windows it uses an
installed SAPI voice to create 8 kHz mono PCM WAV, converts that deterministically
to G.711 mu-law, and writes the raw ``.ulaw`` assets loaded by the voice pipeline.

Run from the repository root with the backend virtualenv Python. Regenerating a
clip is an intentional product change: review it by listening to the temporary
WAV and update the checksum contract in ``emergency_audio.py``.
"""

from __future__ import annotations

import argparse
import audioop
import hashlib
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


CLIPS = {
    "voice_hold": "Sorry. I'm having trouble with my voice. Please hold.",
    "voice_terminal": ("Something has gone wrong on our side. Please call back in a moment."),
}


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _synthesize_wav(text: str, output: Path, voice: str) -> None:
    command = "; ".join(
        [
            "Add-Type -AssemblyName System.Speech",
            "$s=[System.Speech.Synthesis.SpeechSynthesizer]::new()",
            f"$s.SelectVoice({_powershell_quote(voice)})",
            "$fmt=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new("
            "8000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
            "[System.Speech.AudioFormat.AudioChannel]::Mono)",
            f"$s.SetOutputToWaveFile({_powershell_quote(str(output))},$fmt)",
            f"$s.Speak({_powershell_quote(text)})",
            "$s.Dispose()",
        ]
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
    )


def _wav_to_mulaw(source: Path) -> bytes:
    with wave.open(str(source), "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != 8000
            or handle.getcomptype() != "NONE"
        ):
            raise RuntimeError("SAPI output is not 8 kHz mono PCM16")
        pcm16 = handle.readframes(handle.getnframes())
    encoded = audioop.lin2ulaw(pcm16, 2)
    # The runtime sends complete 20 ms frames. Pad with the mu-law silence byte
    # so regeneration can never create a byte-short final packet.
    remainder = len(encoded) % 160
    if remainder:
        encoded += b"\xff" * (160 - remainder)
    return encoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app" / "assets" / "telephony",
    )
    parser.add_argument("--voice", default="Microsoft Zira Desktop")
    args = parser.parse_args()

    if sys.platform != "win32":
        parser.error("clip generation requires Windows SAPI; committed assets run anywhere")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="talky-emergency-clips-") as tmp:
        tmp_dir = Path(tmp)
        for name, text in CLIPS.items():
            wav_path = tmp_dir / f"{name}.wav"
            _synthesize_wav(text, wav_path, args.voice)
            encoded = _wav_to_mulaw(wav_path)
            output = args.output_dir / f"{name}.ulaw"
            output.write_bytes(encoded)
            print(
                f"{output}: bytes={len(encoded)} duration={len(encoded) / 8000:.2f}s "
                f"sha256={hashlib.sha256(encoded).hexdigest()}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
