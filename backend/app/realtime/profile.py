"""Allowlisted native-session evidence. Never include prompt text or tool arguments."""
import hashlib
import re


def instruction_digest(text):
    return hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None


def knowledge_reference(session):
    checksum = getattr(session, "_knowledge_snapshot_checksum", None)
    if isinstance(checksum, str) and re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        return {"status": "versioned", "snapshot_sha256": checksum.lower()}
    return {"status": "unversioned", "snapshot_sha256": None}


def session_profile(session, *, model=None):
    """Missing echo fields stay unknown, rather than inheriting requested values."""
    def mapping(value):
        return value if isinstance(value, dict) else {}

    audio = mapping(session.get("audio"))
    audio_input = mapping(audio.get("input"))
    audio_output = mapping(audio.get("output"))
    detection = audio_input.get("turn_detection", session.get("turn_detection"))
    tools = session.get("tools")
    td_keys = ("type", "eagerness", "threshold", "prefix_padding_ms", "silence_duration_ms", "create_response", "interrupt_response")
    def selected(value, keys):
        return {key: value[key] for key in keys if key in value and isinstance(value[key], (str, int, float, bool, type(None)))} if isinstance(value, dict) else None
    return {
        "model": session.get("model", model),
        "voice": audio_output.get("voice", session.get("voice")),
        "instructions_sha256": instruction_digest(session.get("instructions")),
        "input_format": selected(audio_input.get("format"), ("type", "rate")),
        "output_format": selected(audio_output.get("format"), ("type", "rate")),
        "transcription_model": mapping(audio_input.get("transcription")).get("model"),
        "turn_detection": selected(detection, td_keys),
        "noise_reduction_specified": "noise_reduction" in audio_input,
        "noise_reduction": selected(audio_input.get("noise_reduction"), ("type",)),
        "speed": audio_output.get("speed"),
        "max_output_tokens": session.get("max_output_tokens"),
        "reasoning_effort": mapping(session.get("reasoning")).get("effort"),
        "enabled_tools": (sorted(tool["name"] for tool in tools
                                 if isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("name"), str))
                          if isinstance(tools, list) else None),
    }
