"""Formatting, control-artifact and privacy cleanup for spoken model output."""
import logging
import re

logger = logging.getLogger(__name__)


class LLMGuardrails:
    """Prepare model output for TTS without judging its conversational meaning."""

    def clean_response(self, response: str, *, preserve_audio_tags: bool = False, tts_model_id=None, protected_values=None) -> str:
        """
        Clean LLM response by removing common artifacts.

        Removes:
        - Explicit reasoning/control artifacts
        - Hidden reasoning blocks / stray tags
        - Markdown formatting markers
        - Excessive whitespace
        - Incomplete sentences at the end

        ``preserve_audio_tags``: when False (default), inline bracket audio tags
        like [laughs]/[sighs]/[pause] are STRIPPED — most TTS engines can't
        perform them and would read them aloud. Pass True ONLY when the live
        voice supports them (ElevenLabs eleven_v3), so they reach the engine
        intact. Plain-word fillers ("um", "hmm") are never affected either way.
        """
        if not response:
            return response

        cleaned = response.strip()

        # Remove hidden reasoning or XML-like wrappers before anything else.
        cleaned = re.sub(r'<think\b[^>]*>[\s\S]*?</think>', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<reasoning\b[^>]*>[\s\S]*?</reasoning>', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<analysis\b[^>]*>[\s\S]*?</analysis>', ' ', cleaned, flags=re.IGNORECASE)

        # Collapse markdown links into plain text — MUST run before audio-tag
        # stripping so "[text](url)" becomes "text" and isn't mistaken for a tag.
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)

        # Hard gate for audio tags: physically remove [laughs]/[sighs]/etc unless
        # the voice can perform them. This is the production safety net — the
        # prompt also gates them, but a disobedient LLM must never leak a tag as
        # spoken text on a non-supporting engine (Cartesia/Google/Deepgram/flash).
        from app.domain.services.voice_pipeline.expressive_caps import (
            strip_audio_tags, strip_stage_directions, strip_unsupported_audio_tags,
        )
        # Always remove *asterisk*/(paren)-wrapped stage directions ("*laughs*",
        # "(sighs)") — wrong format on every engine, and the markdown pass below
        # would otherwise leave the bare word "laughs" to be read aloud.
        cleaned = strip_stage_directions(cleaned)
        # Bracket audio tags ([laughs]) — keep only the ones the LIVE engine
        # performs (per-provider, default-deny). tts_model_id is the precise path;
        # preserve_audio_tags is the legacy binary fallback for callers without it.
        if tts_model_id is not None:
            cleaned = strip_unsupported_audio_tags(cleaned, tts_model_id)
        elif not preserve_audio_tags:
            cleaned = strip_audio_tags(cleaned)
        cleaned = re.sub(r'```[\s\S]*?```', ' ', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        cleaned = re.sub(r'^\s*#{1,6}\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*>\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*(?:[-*+•]|\d+[.)])\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\*\*\*?|\*\*?|__?|~~', '', cleaned)
        cleaned = re.sub(r'<[^>]+>', ' ', cleaned)

        # Clean up whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Numbered markdown lists can collapse onto one line during streaming,
        # which makes "1." / "2." look like sentence endings and truncates
        # package answers incorrectly. Strip those inline list markers here.
        cleaned = re.sub(r'(?:(?<=\s)|^)\d+[.)]\s+(?=[A-Za-z])', '', cleaned)

        # Output-side safety net (OWASP LLM02 — treat model output as untrusted):
        # a disobedient or jailbroken model must never speak the technical
        # disclosure the prompt forbids (model/vendor names, system prompt,
        # infra). Redact the offending sentence(s) before TTS. The honest
        # "I'm an AI assistant for {company}" admission is intentionally allowed.
        from app.services.scripts.prompts.prompt_safety import scan_output_for_leakage
        leaked, cleaned = scan_output_for_leakage(cleaned, protected_values or ())
        if leaked:
            logger.warning("Redacted technical disclosure from agent reply before TTS")

        return cleaned


_guardrails_instance = LLMGuardrails()


def get_guardrails() -> LLMGuardrails:
    """Return the shared stateless output cleaner."""
    return _guardrails_instance
