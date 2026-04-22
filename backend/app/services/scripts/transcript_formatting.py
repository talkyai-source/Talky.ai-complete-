"""Pure view-model helpers for transcript turns.

No I/O. Safe to call from anywhere. Only touches data shapes.

The DB stores the full `TranscriptTurn.to_dict()` shape which includes
Deepgram-specific fields (event_type, is_final, audio_window_start, ...) and
interim/eager partials. The UI only wants final user/assistant utterances
with a timestamp. These helpers do that trim.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def format_transcript_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a single turn dict to the UI contract.

    Contract:
      {"role": "user" | "assistant", "content": str, "timestamp": ISO-8601 str}
    """
    return {
        "role": turn.get("role") or "assistant",
        "content": (turn.get("content") or "").strip(),
        "timestamp": turn.get("timestamp") or "",
    }


def format_transcript_turns(turns: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop partial STT frames and empty content; keep user/assistant only.

    A turn is kept iff:
      - role in {"user", "assistant"}
      - include_in_plaintext is truthy (default True for older records that
        predate the flag)
      - content is non-empty
    """
    out: List[Dict[str, Any]] = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        if role not in ("user", "assistant"):
            continue
        if not turn.get("include_in_plaintext", True):
            continue
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        out.append(format_transcript_turn(turn))
    return out
