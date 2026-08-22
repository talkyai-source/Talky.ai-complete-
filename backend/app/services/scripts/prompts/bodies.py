"""Which persona body should this call compose from? (goals.md §6 rollback)

`versions.py` answers *what did this call run on*. This answers *what should it
run on*, which is a different question the moment you want to go back.

THE PROBLEM ROLLBACK ACTUALLY HAS
---------------------------------
Persona bodies are Python constants. Once `lead_gen@2` ships, `lead_gen@1`'s text
exists only in git history, so "roll back" meant revert-and-redeploy — a release
at the exact moment you least want one.

So: capture the body the first time a version composes, and let a campaign pin a
version. A pinned campaign composes from the stored body; everything else
composes from code and behaves exactly as before.

FAIL FORWARD, NEVER FAIL THE CALL
---------------------------------
Every function here degrades to the current code body. A missing table, an
unrecorded version, a pin naming something that was never captured, a database
that is briefly unreachable — all of them mean "compose normally". A rollback
mechanism that can prevent a call from happening is worse than no rollback
mechanism, because the failure is silent and total rather than cosmetic.

The one thing it will not do quietly is honour a pin it could not resolve: that
logs a warning, because a campaign someone deliberately held on an older prompt
silently running the newer one is the failure this module exists to prevent.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.services.scripts.prompts.versions import template_name, version_for

logger = logging.getLogger(__name__)


def current_bodies() -> dict[str, str]:
    """The raw template body per persona, as this build ships it.

    Imported lazily so importing this module never drags the persona modules
    into a process that does not need them.
    """
    from app.services.scripts.prompts.personas.customer_support import (
        CUSTOMER_SUPPORT_BODY,
    )
    from app.services.scripts.prompts.personas.lead_gen import LEAD_GEN_KD_BODY
    from app.services.scripts.prompts.personas.receptionist import RECEPTIONIST_BODY

    return {
        "lead_gen": LEAD_GEN_KD_BODY,
        "customer_support": CUSTOMER_SUPPORT_BODY,
        "receptionist": RECEPTIONIST_BODY,
    }


def body_sha(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


# ── the sync cache ──────────────────────────────────────────────────────────
#
# `build_telephony_session_config` is synchronous, so the composition path cannot
# await a database read. Caching is safe here rather than a shortcut: a recorded
# body is immutable by construction (the INSERT is ON CONFLICT DO NOTHING and
# deliberately never updates), so a cached body cannot go stale — it can only be
# absent.
#
# Absent means a version recorded *after* this process started is not pinnable
# until it restarts. That is an honest limitation and not the case rollback is
# for: you roll back TO an older version, which was already recorded when the
# process came up.
_BODY_CACHE: dict[str, tuple[str, str]] = {}   # version -> (persona_type, body)


def cache_size() -> int:
    return len(_BODY_CACHE)


def prime_cache(rows: list[dict[str, Any]] | None) -> int:
    """Load version bodies for synchronous lookup. Returns how many are held."""
    if not rows:
        return len(_BODY_CACHE)
    for r in rows:
        version, persona, body = r.get("version"), r.get("persona_type"), r.get("body")
        if version and persona and body:
            _BODY_CACHE[version] = (persona, body)
    return len(_BODY_CACHE)


def resolve_body_sync(persona_type: str, pinned_version: str | None) -> tuple[str | None, str | None]:
    """Synchronous counterpart of ``resolve_body``, backed by the cache.

    Same contract: ``(None, None)`` means "compose from code", which is the
    answer for an unpinned campaign and for every failure mode.
    """
    if not pinned_version:
        return None, None
    if pinned_version == version_for(persona_type):
        return None, None

    hit = _BODY_CACHE.get(pinned_version)
    if not hit:
        logger.warning(
            "prompt_pin_unresolved version=%r persona=%s cached=%d — composing "
            "from the CURRENT prompt instead; a pin that silently does nothing "
            "is the failure this exists to prevent",
            pinned_version, persona_type, len(_BODY_CACHE),
        )
        return None, None

    cached_persona, body = hit
    if cached_persona != persona_type:
        logger.warning(
            "prompt_pin_persona_mismatch version=%s belongs to %s not %s — ignoring",
            pinned_version, cached_persona, persona_type,
        )
        return None, None

    logger.info(
        "prompt_pin_applied persona=%s pinned=%s current=%s — rolled back",
        persona_type, pinned_version, version_for(persona_type),
    )
    return body, pinned_version


async def load_cache(db_pool: Any) -> int:
    """Fill the sync cache from the archive. Called at startup after capture."""
    if db_pool is None:
        return 0
    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, None) as conn:
            rows = await conn.fetch(
                "SELECT version, persona_type, body FROM prompt_template_versions"
            )
        n = prime_cache([dict(r) for r in rows])
        logger.info("prompt_body_cache_loaded versions=%d", n)
        return n
    except Exception:  # noqa: BLE001
        logger.warning("prompt_body_cache_load_failed", exc_info=True)
        return 0


async def record_current_versions(db_pool: Any) -> int:
    """Capture this build's bodies so they can be rolled back to later.

    Idempotent — ``ON CONFLICT (version) DO NOTHING``. Deliberately does NOT
    update an existing row: a version that has already been recorded is the text
    that actually ran under that name, and overwriting it would make the archive
    lie about history exactly when someone is relying on it.

    Returns how many were newly captured. Never raises.
    """
    if db_pool is None:
        return 0
    try:
        from app.core.db_utils import acquire_with_tenant

        recorded = 0
        bodies = current_bodies()
        async with acquire_with_tenant(db_pool, None) as conn:
            for persona, body in bodies.items():
                version = version_for(persona)
                if version == "unregistered":
                    continue
                result = await conn.execute(
                    """
                    INSERT INTO prompt_template_versions
                        (persona_type, template, version, body, body_sha)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    persona, template_name(persona), version, body, body_sha(body),
                )
                if isinstance(result, str) and not result.endswith(" 0"):
                    recorded += 1
                    logger.info(
                        "prompt_version_recorded persona=%s version=%s chars=%d",
                        persona, version, len(body),
                    )
        return recorded
    except Exception:  # noqa: BLE001 — see module docstring
        logger.warning("prompt_version_capture_failed", exc_info=True)
        return 0


async def resolve_body(
    db_pool: Any, persona_type: str, pinned_version: str | None
) -> tuple[str | None, str | None]:
    """Body to compose from, and the version it represents.

    Returns ``(None, None)`` for "no override — use the code body", which is the
    answer for every unpinned campaign and every failure mode.
    """
    if not pinned_version or db_pool is None:
        return None, None

    current = version_for(persona_type)
    if pinned_version == current:
        # Pinned to what we already ship. Compose from code so a pin cannot
        # accidentally serve a stale archived copy of the current version.
        return None, None

    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, None) as conn:
            row = await conn.fetchrow(
                """SELECT body, version, approved FROM prompt_template_versions
                    WHERE version = $1 AND persona_type = $2""",
                pinned_version, persona_type,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "prompt_pin_lookup_failed version=%s persona=%s — composing from code",
            pinned_version, persona_type, exc_info=True,
        )
        return None, None

    if not row:
        # Loud: a campaign held on an older prompt silently running the newer one
        # is the exact failure this module exists to prevent.
        logger.warning(
            "prompt_pin_unresolved version=%r persona=%s — that version was never "
            "recorded, so this call composes from the CURRENT prompt (%s) instead",
            pinned_version, persona_type, current,
        )
        return None, None

    if not row["approved"]:
        logger.warning(
            "prompt_pin_unapproved version=%s persona=%s — pinned to a version "
            "marked not approved; composing from it anyway because the pin is an "
            "explicit operator decision",
            pinned_version, persona_type,
        )

    logger.info(
        "prompt_pin_applied persona=%s pinned=%s current=%s — rolled back",
        persona_type, pinned_version, current,
    )
    return row["body"], row["version"]


async def list_versions(db_pool: Any, persona_type: str | None = None) -> list[dict[str, Any]]:
    """Versions available to roll back to, newest first."""
    if db_pool is None:
        return []
    from app.core.db_utils import acquire_with_tenant

    sql = """SELECT persona_type, template, version, body_sha, approved,
                    recorded_at, length(body) AS body_chars
               FROM prompt_template_versions"""
    args: list[Any] = []
    if persona_type:
        args.append(persona_type)
        sql += " WHERE persona_type = $1"
    sql += " ORDER BY recorded_at DESC"

    async with acquire_with_tenant(db_pool, None) as conn:
        rows = await conn.fetch(sql, *args)
    out = []
    for r in rows:
        d = dict(r)
        d["is_current"] = d["version"] == version_for(d["persona_type"])
        out.append(d)
    return out
