"""Pre-warm injection of campaign knowledge into a call session (vectorless RAG, P2).

Runs once, at pre-originate warmup (async, DB pool in hand) — NOT on the hot
per-turn path. It reads the campaign's ``knowledge_mode`` and, for inline /
map_retrieve campaigns, bakes the (compacted) knowledge tree straight into
``call_session.system_prompt`` so every turn already has it for free. For
retrieve mode it injects nothing here — that KB is too big to inline and is
served per-turn by ``turn_streamer`` instead.

It also stamps ``call_session.tenant_id`` + ``knowledge_mode`` so the turn loop
can do tenant-scoped per-turn retrieval without re-loading the campaign.

Fail-soft by contract: a knowledge hiccup must never break call setup, so every
path is wrapped and falls back to "no knowledge" (the call proceeds on its
persona prompt exactly as before the feature existed).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.scripts.knowledge.retrieval import compact_tree, knowledge_enabled

logger = logging.getLogger(__name__)

_INLINE_HEADER = (
    "## Company knowledge\n"
    "Use the following to answer the caller. Speak naturally in your own words — "
    "do NOT read it verbatim, and never mention that you are reading from notes. "
    "If the answer isn't here, say you'll follow up rather than guessing."
)
_MAP_HEADER = (
    "## Company knowledge — topics you can speak to\n"
    "These are the subjects you know about. Answer from them naturally; more "
    "detail on a topic is provided as the caller asks about it."
)


def _row_get(row: Any, key: str) -> Optional[Any]:
    """Read a field off a campaign row that may be a dict or an object."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


async def apply_campaign_knowledge(call_session, campaign_row: Any, *, pool) -> None:
    """Stamp knowledge_mode/tenant_id and bake inline knowledge into the prompt.

    No-op (leaves ``knowledge_mode`` None) when the feature flag is off, the
    campaign has no knowledge, or anything goes wrong. Never raises.
    """
    if call_session is None or not knowledge_enabled():
        return
    try:
        mode = (_row_get(campaign_row, "knowledge_mode") or "none").strip().lower()
        if mode not in ("inline", "map_retrieve", "retrieve"):
            return  # 'none' or unknown → no knowledge layer for this call

        tenant_id = _row_get(campaign_row, "tenant_id")
        campaign_id = _row_get(campaign_row, "id") or call_session.campaign_id
        if not (tenant_id and campaign_id and pool is not None):
            return
        tenant_id, campaign_id = str(tenant_id), str(campaign_id)

        # The turn loop needs these to do tenant-scoped per-turn retrieval.
        call_session.tenant_id = call_session.tenant_id or tenant_id
        call_session.knowledge_mode = mode

        # inline → whole tree; map_retrieve → skeleton (TOC); retrieve → nothing
        # here (served per-turn, too large to inline).
        if mode == "inline":
            tree = await compact_tree(pool, tenant_id, campaign_id)
            header = _INLINE_HEADER
        elif mode == "map_retrieve":
            tree = await compact_tree(pool, tenant_id, campaign_id, skeleton_only=True)
            header = _MAP_HEADER
        else:
            return

        if tree and tree.strip():
            # The baked-in tree is tenant data, so delimit it (Microsoft
            # "Spotlighting" / OWASP LLM01) and tell the model it's data, not
            # instructions — same fence the per-turn retrieve path uses.
            from app.services.scripts.prompts.prompt_safety import (
                DATA_ONLY_NOTE,
                fence_untrusted,
                scan_for_injection,
            )

            # Content-integrity (issue #3): drop any line shaped like an
            # instruction to the model (poisoned KB entry) BEFORE baking, mirroring
            # the per-turn retrieve path (turn_streamer). The fence alone isn't
            # enough — a model can still act on instruction-shaped fenced text.
            _all_lines = tree.splitlines()
            _clean_lines = [ln for ln in _all_lines if not scan_for_injection(ln)]
            _dropped = len(_all_lines) - len(_clean_lines)
            tree = "\n".join(_clean_lines).strip()
            if _dropped:
                logger.warning(
                    "campaign_knowledge dropped %d line(s) flagged as injection "
                    "campaign=%s mode=%s",
                    _dropped, campaign_id[:12], mode,
                )
            if not tree:
                return  # everything was flagged — bake nothing

            _KB_TAG = "company_knowledge"
            fenced = fence_untrusted(tree, tag=_KB_TAG)
            call_session.system_prompt = (
                f"{call_session.system_prompt}\n\n{header}\n"
                f"{DATA_ONLY_NOTE(_KB_TAG)}\n{fenced}"
            )
            logger.info(
                "campaign_knowledge_injected campaign=%s mode=%s chars=%d",
                campaign_id[:12], mode, len(tree),
            )
    except Exception as exc:
        logger.warning("apply_campaign_knowledge failed (continuing without KB): %s", exc)
