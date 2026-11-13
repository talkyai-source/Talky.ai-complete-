"""Fresh installs create no SECURITY DEFINER function without a pinned search_path.

A definer function resolves unqualified names through the caller's search_path, so a
caller who can create objects on that path can make it run their code with the owner's
rights. Production's dead log_audit_event was such a function; migration 0094 drops it,
and the fresh-install file must not create it, or anything like it, again.
"""
from __future__ import annotations

from pathlib import Path
import re

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "complete_schema.sql"
_FUNCTION_HEADER = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?(\w+)\s*\(.*?\bAS\s+\$",
    re.IGNORECASE | re.DOTALL,
)
_PINNED_LATER = re.compile(
    r"ALTER\s+FUNCTION\s+(?:public\.)?(\w+)\s*\([^;]*?\)\s+SET\s+search_path", re.IGNORECASE
)


def test_every_security_definer_function_pins_its_search_path():
    text = SCHEMA.read_text(encoding="utf-8")
    pinned_later = set(_PINNED_LATER.findall(text))
    unpinned = []
    for match in _FUNCTION_HEADER.finditer(text):
        header = match.group(0)
        if (re.search(r"\bSECURITY\s+DEFINER\b", header, re.IGNORECASE)
                and not re.search(r"\bSET\s+search_path\b", header, re.IGNORECASE)
                and match.group(1) not in pinned_later):
            unpinned.append(match.group(1))
    assert unpinned == []


def test_the_dead_log_audit_event_is_not_created():
    assert "FUNCTION log_audit_event" not in SCHEMA.read_text(encoding="utf-8")
