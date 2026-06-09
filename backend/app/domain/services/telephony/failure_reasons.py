"""Translate raw pipeline failure strings into a coarse category + a short,
caller-facing message — so the dashboard can show WHY a call failed without
leaking raw logs or stack traces.

Returns (category, human_message). category is one of:
    tts | llm | stt | telephony | prewarm | other
"""
from __future__ import annotations

from typing import Optional, Tuple


def humanize_failure(reason: Optional[str]) -> Tuple[str, str]:
    raw = (reason or "").strip()
    low = raw.lower()
    if not raw:
        return "prewarm", "The voice pipeline could not start."

    # --- Text-to-speech ---
    if "quota_exceeded" in low or "credits remaining" in low or "out of credits" in low:
        if "elevenlabs" in low:
            return "tts", (
                "Text-to-speech (ElevenLabs) is out of credits. Top up ElevenLabs "
                "or switch this campaign's voice provider."
            )
        return "tts", "Text-to-speech provider is out of credits / quota."
    if "elevenlabs" in low or "cartesia" in low or "tts" in low:
        if any(c in low for c in ("401", "403", "unauthorized", "api key", "invalid_api")):
            return "tts", "Text-to-speech provider rejected the request (auth/key). Check the provider key."
        return "tts", "Text-to-speech provider error — the agent had no voice."

    # --- Language model ---
    if "gemini-llm" in low and "open" in low:
        return "llm", "AI model is temporarily paused after repeated errors; it recovers on its own."
    if "gemini" in low or "groq" in low or "llm" in low:
        return "llm", "AI model error — the agent could not generate a reply."

    # --- Speech-to-text ---
    if "deepgram" in low or "flux" in low or "stt" in low:
        return "stt", "Speech-to-text provider error — the agent could not hear the caller."

    # --- Telephony / carrier ---
    if any(c in low for c in ("pjsip", "asterisk", "ari", "sip", "carrier", "408", "503 service")):
        return "telephony", "Telephony/carrier error placing the call."

    short = raw if len(raw) <= 180 else raw[:177] + "..."
    return "prewarm", f"Voice pipeline failed to start: {short}"
