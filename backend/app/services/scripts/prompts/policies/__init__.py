"""Behaviour policies as Markdown: the agent's generic standards, outside code.

Each ``<name>.md`` here is one prompt block, read verbatim. An optional header
between ``---`` lines declares metadata (purpose, budget_tokens, placeholders);
everything below it is the exact prompt text. Business specifics never belong
here: they come from the campaign (script, knowledge, brief). The standards
these files implement are in docs/standards/voice-agent-standards.md, and
tests/unit/test_prompt_policies.py holds every file to its token budget.

PROMPT_POLICY_DIR may point at a directory of overrides with the same file
names. An override is used only when it declares the same placeholders as the
packaged file, so a bad edit falls back to the default instead of breaking
call setup. Files are read once per process; restart to pick up an edit.
"""
from __future__ import annotations

import logging
import os
import string
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

POLICY_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Policy:
    name: str
    body: str
    source: str
    meta: dict = field(default_factory=dict)


def _parse(text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            meta = {}
            for line in text[4:end].splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    meta[key.strip()] = value.strip()
            return meta, text[end + 5:]
    return {}, text


def placeholders(body: str) -> frozenset[str] | None:
    """Format fields in ``body``; None when its braces do not parse."""
    try:
        return frozenset(name for _, name, _, _ in string.Formatter().parse(body) if name)
    except ValueError:
        return None


@lru_cache(maxsize=None)
def policy(name: str) -> Policy:
    path = POLICY_DIR / f"{name}.md"
    meta, body = _parse(path.read_text(encoding="utf-8"))
    chosen = Policy(name, body, str(path), meta)
    override_dir = os.getenv("PROMPT_POLICY_DIR", "").strip()
    if override_dir:
        override = Path(override_dir) / f"{name}.md"
        if override.is_file():
            o_meta, o_body = _parse(override.read_text(encoding="utf-8"))
            if o_body.strip() and placeholders(o_body) == placeholders(body):
                chosen = Policy(name, o_body, str(override), {**meta, **o_meta})
            else:
                logger.error("prompt_policy_override_rejected name=%s path=%s", name, override)
    return chosen


def load_policy(name: str) -> str:
    """The prompt text of policy ``name`` (e.g. ``"how_to_speak"``)."""
    return policy(name).body


def policy_names() -> list[str]:
    return sorted(p.relative_to(POLICY_DIR).with_suffix("").as_posix() for p in POLICY_DIR.rglob("*.md"))
