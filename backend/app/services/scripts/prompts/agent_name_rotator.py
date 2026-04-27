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
from typing import Sequence

MAX_POOL_SIZE = 3


def pick_agent_name(pool: Sequence[str]) -> str:
    """Return one agent name from the pool, uniformly at random.

    The pool is passed through light validation — an empty pool or one
    larger than MAX_POOL_SIZE is a configuration error the campaign
    creation form should have caught.

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
    return random.choice(cleaned)


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
