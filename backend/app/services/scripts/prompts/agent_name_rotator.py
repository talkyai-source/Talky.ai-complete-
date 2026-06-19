"""Per-call agent-name rotator.

The campaign creator supplies 1-3 names. For each outbound call, one
name is picked uniformly at random from that pool and stays stable for
the whole call.

Call-site: `campaign_service._create_job_for_lead` picks the name when
creating the DialerJob so the choice is durable in Redis and survives
restarts.
"""
from __future__ import annotations

import random
from typing import Mapping, Optional, Sequence

MAX_POOL_SIZE = 3


def pick_agent_name_for_voice(
    pool: Sequence[str],
    genders: Optional[Mapping[str, str]],
    voice_gender: Optional[str],
    *,
    seed: Optional[str] = None,
) -> str:
    """Pick an agent name whose gender matches the selected voice.

    Resolution order:
      1. If we know the voice gender AND have name→gender tags, pick at
         random from the pool names tagged with that gender.
      2. If the pool has no name of that gender (or no tags), fall back to a
         built-in name of the voice's gender — so the voice never speaks a
         clearly-mismatched name.
      3. If the voice gender is unknown, fall back to the legacy random pick
         over the whole pool.

    ``seed`` (optional): when given, the pick is DETERMINISTIC for that seed —
    pass a stable per-call value (e.g. the lead or call id) so a retried or
    restarted call keeps the same agent name instead of re-rolling. When None,
    selection is uniformly random as before.

    Never raises — on any inconsistency it degrades to a plain pool pick.
    """
    chooser = random.Random(seed) if seed is not None else random
    vg = (voice_gender or "").strip().lower()
    if vg in ("male", "female"):
        gmap = {str(k).strip().lower(): str(v).strip().lower() for k, v in (genders or {}).items()}
        matching = [n for n in pool if n and gmap.get(str(n).strip().lower()) == vg]
        if matching:
            return chooser.choice(matching)
        # No matching-gender name configured — use a built-in gendered name.
        try:
            from app.domain.services.global_ai_config import get_random_agent_name
            return get_random_agent_name(vg)
        except Exception:
            pass
    # Unknown voice gender (or fallback failed) → legacy behaviour.
    return pick_agent_name(pool, seed=seed)


def pick_agent_name(pool: Sequence[str], *, seed: Optional[str] = None) -> str:
    """Return one agent name from the pool.

    The pool is passed through light validation — an empty pool or one
    larger than MAX_POOL_SIZE is a configuration error the campaign
    creation form should have caught.

    ``seed`` (optional): when given, the pick is DETERMINISTIC for that seed
    (pass a stable per-call value so a retry keeps the same name); when None,
    the pick is uniformly random as before.

    Raises
    ------
    ValueError
        If the pool is empty or exceeds MAX_POOL_SIZE, or contains a
        non-string / blank entry.
    """
    if not pool:
        raise ValueError("agent-name pool is empty")
    if len(pool) > MAX_POOL_SIZE:
        raise ValueError(
            f"agent-name pool has {len(pool)} entries, "
            f"max is {MAX_POOL_SIZE}"
        )
    cleaned: list[str] = []
    for entry in pool:
        if not isinstance(entry, str):
            raise ValueError(f"agent-name entry must be str, got {type(entry).__name__}")
        name = entry.strip()
        if not name:
            raise ValueError("agent-name entry is blank")
        cleaned.append(name)
    chooser = random.Random(seed) if seed is not None else random
    return chooser.choice(cleaned)


def validate_pool(pool: Sequence[str]) -> list[str]:
    """Validate and normalize an agent-name pool. Used by
    CampaignCreateRequest to reject bad input at the API boundary.

    Returns the normalized list (stripped, non-empty). Raises ValueError
    with a user-friendly message if invalid.
    """
    if not pool:
        raise ValueError("Provide at least one agent name.")
    if len(pool) > MAX_POOL_SIZE:
        raise ValueError(f"Up to {MAX_POOL_SIZE} agent names — got {len(pool)}.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in pool:
        if not isinstance(entry, str):
            raise ValueError("Agent names must be plain text.")
        name = entry.strip()
        if not name:
            raise ValueError("Agent names cannot be blank.")
        key = name.lower()
        if key in seen:
            raise ValueError(f"Duplicate agent name: {name!r}.")
        seen.add(key)
        cleaned.append(name)
    return cleaned
