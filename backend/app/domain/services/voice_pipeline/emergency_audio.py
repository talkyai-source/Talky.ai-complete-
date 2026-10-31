"""Provider-independent voice clips for a failed TTS path.

These assets are already G.711 PCMU at 8 kHz, so they bypass STT/TTS provider
formats and go directly through the telephony gateway's proven wire path. The
checksums make a damaged, truncated, or accidentally regenerated clip fail
loudly before any bytes are sent to a caller.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

VOICE_HOLD = "voice_hold"
VOICE_TERMINAL = "voice_terminal"

_ASSET_DIR = Path(__file__).resolve().parents[3] / "assets" / "telephony"
_CLIPS = {
    VOICE_HOLD: (
        "voice_hold.ulaw",
        "ce7e74ceb70b5f0d323d5524f27bbb9cfd1bc85de4b58b2ea915d3c1a6547292",
    ),
    VOICE_TERMINAL: (
        "voice_terminal.ulaw",
        "174de187dc3d1ef9e001738280589c1845d3ec9413f0978cdb586b1c97fe806c",
    ),
}


@lru_cache(maxsize=len(_CLIPS))
def load_emergency_clip(name: str) -> bytes:
    try:
        filename, expected_sha256 = _CLIPS[name]
    except KeyError as exc:
        raise ValueError(f"unknown emergency clip: {name}") from exc

    data = (_ASSET_DIR / filename).read_bytes()
    if not data or len(data) > 96_000 or len(data) % 160:
        raise RuntimeError(f"invalid emergency clip framing name={name} bytes={len(data)}")
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"emergency clip checksum mismatch name={name} sha256={actual_sha256}")
    return data


async def play_emergency_clip(
    media_gateway: Any,
    call_id: str,
    name: str,
    *,
    barge_in_event: Optional[Any] = None,
) -> bool:
    """Play one validated clip through the raw PCMU gateway contract."""

    play = getattr(media_gateway, "play_pcmu_clip", None)
    if not callable(play):
        return False
    try:
        result = await play(
            call_id,
            load_emergency_clip(name),
            barge_in_event=barge_in_event,
        )
        if isinstance(result, dict):
            return bool(result.get("ok") or result.get("interrupted"))
        return bool(result)
    except Exception as exc:  # noqa: BLE001 - terminal caller fallback below
        logger.critical(
            "emergency_voice_clip_failed call=%s clip=%s err=%s",
            call_id[:12],
            name,
            exc,
            exc_info=True,
        )
        return False
