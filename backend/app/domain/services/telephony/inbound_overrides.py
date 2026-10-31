"""Validate and apply the inbound-only AI overrides used by live calls."""

from __future__ import annotations

from typing import Any, Mapping


_TEXT_LENGTHS = {
    "purpose": 2_000,
    "persona": 2_000,
    "system_prompt": 20_000,
    "voice_id": 255,
}
_SUPPORTED = {*_TEXT_LENGTHS, "silence_timeout_seconds"}


def _neutral(key: str, value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if key == "silence_timeout_seconds":
        try:
            return float(value) == 8.0
        except (TypeError, ValueError):
            return False
    return False


def validate_qualification_overrides(
    value: Any,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """Return supported values plus unsupported and invalid key names."""

    if value is None:
        return {}, (), ()
    if not isinstance(value, Mapping):
        return {}, (), ("qualification_config",)
    supported: dict[str, Any] = {}
    unsupported: list[str] = []
    invalid: list[str] = []
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if _neutral(key, raw_value):
            continue
        if key not in _SUPPORTED:
            unsupported.append(key)
            continue
        if key == "silence_timeout_seconds":
            if isinstance(raw_value, bool):
                invalid.append(key)
                continue
            try:
                timeout = float(raw_value)
            except (TypeError, ValueError):
                invalid.append(key)
                continue
            if not 3.0 <= timeout <= 60.0:
                invalid.append(key)
                continue
            supported[key] = int(timeout) if timeout.is_integer() else timeout
            continue
        if not isinstance(raw_value, str):
            invalid.append(key)
            continue
        normalized = raw_value.strip()
        if not normalized or len(normalized) > _TEXT_LENGTHS[key]:
            invalid.append(key)
            continue
        supported[key] = normalized
    return supported, tuple(sorted(set(unsupported))), tuple(sorted(set(invalid)))


def apply_qualification_overrides(
    campaign: Mapping[str, Any], qualification_config: Any
) -> dict[str, Any]:
    """Apply supported values to a copy of the exact admitted campaign."""

    supported, unsupported, invalid = validate_qualification_overrides(
        qualification_config
    )
    if unsupported or invalid:
        keys = ",".join((*unsupported, *invalid))
        raise ValueError(f"unsupported inbound qualification overrides: {keys}")
    merged = dict(campaign)
    if "voice_id" in supported:
        merged["voice_id"] = supported["voice_id"]
    instruction_blocks: list[str] = []
    if "purpose" in supported:
        instruction_blocks.append(
            "INBOUND CALL PURPOSE\n" + str(supported["purpose"])
        )
        merged["goal"] = supported["purpose"]
    if "persona" in supported:
        instruction_blocks.append(
            "INBOUND AGENT STYLE\n" + str(supported["persona"])
        )
    if "system_prompt" in supported:
        instruction_blocks.append(
            "INBOUND-SPECIFIC INSTRUCTIONS\n" + str(supported["system_prompt"])
        )
        merged["system_prompt"] = supported["system_prompt"]
    if instruction_blocks:
        raw_script = merged.get("script_config")
        script = dict(raw_script) if isinstance(raw_script, Mapping) else {}
        # This is the bounded tenant-authored instruction path consumed by the
        # shared session composer. Append to (rather than overwrite) the base
        # campaign instructions so inbound specialization cannot erase its
        # approved identity, facts, or compliance rules.
        existing = str(script.get("additional_instructions") or "").strip()
        parts = ([existing] if existing else []) + instruction_blocks
        script["additional_instructions"] = "\n\n".join(parts)
        merged["script_config"] = script
    if "silence_timeout_seconds" in supported:
        merged["_inbound_silence_timeout_seconds"] = supported[
            "silence_timeout_seconds"
        ]
    return merged
