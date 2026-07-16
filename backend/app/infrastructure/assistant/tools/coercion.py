"""Argument coercion for LLM-emitted tool parameters.

Small models routinely emit JSON strings where the schema wants primitives
("confirm": "true"). The Groq schemas therefore accept ["boolean", "string"]
for boolean flags, and every consuming tool normalises through here so a
quoted boolean can never fail validation or — worse — truthy-string into the
wrong branch (bool("false") is True in Python).
"""
from __future__ import annotations

from typing import Any

_TRUE_WORDS = {"true", "yes", "y", "1", "apply", "confirm", "confirmed"}
_FALSE_WORDS = {"false", "no", "n", "0", "preview", "none", "null", ""}


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Normalise an LLM-supplied boolean-ish value to a real bool.

    Unrecognised strings fall back to ``default`` (False for confirm flags,
    so an ambiguous value can only ever downgrade to a harmless preview).
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return default
