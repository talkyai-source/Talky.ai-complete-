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


def _log_setup(call_session, state: str, **fields: Any) -> None:
    """One line per call saying whether the knowledge layer is wired, and why not.

    Every refusal below used to be a silent ``return``, and a campaign in plain
    ``retrieve`` mode logs nothing until a caller happens to ask a matching
    question. So "is knowledge even switched on for this call?" could not be
    answered from the logs at all -- only by reading the database. That gap is
    what made a live knowledge complaint undiagnosable on 2026-09-22.

    Greppable as ``KB_SETUP``. Never raises: this is diagnostics, not logic.
    """
    try:
        detail = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        logger.info(
            "KB_SETUP call=%s %s %s",
            str(getattr(call_session, "call_id", "?") or "?")[:8],
            state,
            detail,
        )
    except Exception:  # pragma: no cover - defensive
        pass


def apply_pinned_campaign_knowledge(call_session, snapshot: Any) -> None:
    """Apply the immutable knowledge captured by inbound admission.

    Unlike the ordinary outbound warmup path, this function performs no DB or
    environment read.  ``enabled``, mode, metadata, and every retrievable node
    are all taken from the durable route snapshot created before Answer.
    """
    if call_session is None:
        return
    if not isinstance(snapshot, dict):
        _log_setup(call_session, "OFF", reason="no_snapshot", path="inbound")
        return
    if snapshot.get("enabled") is not True:
        # pinned at admission from knowledge_enabled(), i.e. the env flag.
        _log_setup(call_session, "OFF", reason="feature_flag_off", path="inbound")
        return
    mode = str(snapshot.get("mode") or "none").strip().lower()
    if mode not in ("inline", "map_retrieve", "retrieve"):
        _log_setup(
            call_session, "OFF", reason="knowledge_mode_none",
            mode=mode, path="inbound",
        )
        return
    nodes = [dict(node) for node in (snapshot.get("nodes") or []) if isinstance(node, dict)]
    tenant_id = str(snapshot.get("tenant_id") or "").strip()
    campaign_id = str(snapshot.get("campaign_id") or "").strip()
    if not tenant_id or not campaign_id:
        _log_setup(
            call_session, "OFF", reason="snapshot_incomplete", path="inbound",
        )
        return
    if not nodes:
        _log_setup(
            call_session, "WARN", reason="no_nodes_pinned",
            configured=mode, campaign=campaign_id[:8], path="inbound",
        )

    call_session.tenant_id = call_session.tenant_id or tenant_id
    call_session._knowledge_snapshot_nodes = nodes
    call_session._knowledge_snapshot_checksum = snapshot.get("checksum")
    call_session.knowledge_mode = "retrieve"
    if mode == "retrieve":
        _log_setup(
            call_session, "ON", effective="retrieve", configured=mode,
            nodes=len(nodes), campaign=campaign_id[:8], path="inbound",
        )
        return

    from app.services.scripts.knowledge.retrieval import compact_tree_from_nodes

    tree = compact_tree_from_nodes(
        nodes,
        skeleton_only=(mode == "map_retrieve"),
        campaign_id=campaign_id,
    )
    header = _MAP_HEADER if mode == "map_retrieve" else _INLINE_HEADER
    if _bake_inline_knowledge(call_session, tree, header, campaign_id, mode):
        call_session.knowledge_mode = mode
    # Report what the call will ACTUALLY run, not what was configured. A bake
    # that renders nothing leaves the session in retrieve, and a line claiming
    # inline would send the next investigation the wrong way.
    _log_setup(
        call_session, "ON", effective=call_session.knowledge_mode,
        configured=mode, nodes=len(nodes), campaign=campaign_id[:8],
        path="inbound",
    )


async def apply_campaign_knowledge(call_session, campaign_row: Any, *, pool) -> None:
    """Stamp knowledge_mode/tenant_id and bake inline knowledge into the prompt.

    No-op (leaves ``knowledge_mode`` None) when the feature flag is off, the
    campaign has no knowledge, or anything goes wrong. Never raises.
    """
    if call_session is None:
        return
    if not knowledge_enabled():
        _log_setup(
            call_session, "OFF", reason="feature_flag_off", path="outbound",
        )
        return
    try:
        mode = (_row_get(campaign_row, "knowledge_mode") or "none").strip().lower()
        if mode not in ("inline", "map_retrieve", "retrieve"):
            # 'none' or unknown → no knowledge layer for this call
            _log_setup(
                call_session, "OFF", reason="knowledge_mode_none",
                mode=mode, path="outbound",
            )
            return

        tenant_id = _row_get(campaign_row, "tenant_id")
        campaign_id = _row_get(campaign_row, "id") or call_session.campaign_id
        if not (tenant_id and campaign_id and pool is not None):
            _log_setup(
                call_session, "OFF", reason="no_tenant_or_pool", path="outbound",
            )
            return
        tenant_id, campaign_id = str(tenant_id), str(campaign_id)

        # The turn loop needs these to do tenant-scoped per-turn retrieval.
        call_session.tenant_id = call_session.tenant_id or tenant_id
        # SAFE DEFAULT (issue #4): mark the session for PER-TURN retrieval up
        # front. We only "upgrade" to inline/map_retrieve once the tree is
        # ACTUALLY baked into the prompt below. This guarantees that if the
        # inline bake fails (raises) or yields nothing, the call is NOT left
        # marked "inline" with an empty prompt — the turn loop runs per-turn
        # retrieval ONLY for retrieve/map_retrieve, so a stale "inline" mark
        # would mean the call silently proceeds with ZERO knowledge.
        call_session.knowledge_mode = "retrieve"

        # retrieve → nothing to bake here (served per-turn, too large to inline).
        if mode == "retrieve":
            _log_setup(
                call_session, "ON", effective="retrieve", configured=mode,
                campaign=campaign_id[:8], path="outbound",
            )
            return

        # inline → whole tree; map_retrieve → skeleton (TOC).
        try:
            if mode == "inline":
                tree = await compact_tree(pool, tenant_id, campaign_id)
                header = _INLINE_HEADER
            else:  # map_retrieve
                tree = await compact_tree(pool, tenant_id, campaign_id, skeleton_only=True)
                header = _MAP_HEADER
            baked = _bake_inline_knowledge(call_session, tree, header, campaign_id, mode)
        except Exception as exc:
            # LOUD, not swallowed: the inline path failed, so we deliberately
            # LEAVE knowledge_mode == "retrieve" so per-turn retrieval still
            # serves this call's KB. The old code left it marked "inline" here,
            # which killed per-turn retrieval → the call had no knowledge.
            logger.error(
                "apply_campaign_knowledge INLINE BAKE FAILED for campaign=%s mode=%s — "
                "FALLING BACK to per-turn retrieve so the call still has knowledge: %s",
                campaign_id[:12], mode, exc, exc_info=True,
            )
            _log_setup(
                call_session, "ON", effective="retrieve", configured=mode,
                reason="bake_failed", campaign=campaign_id[:8], path="outbound",
            )
            return

        if baked:
            call_session.knowledge_mode = mode  # upgrade only on a real bake
        else:
            logger.warning(
                "apply_campaign_knowledge: %s produced NO knowledge to bake for "
                "campaign=%s — using per-turn retrieve instead (KB not lost)",
                mode, campaign_id[:12],
            )
        # Report the mode the call will ACTUALLY run in. Claiming the
        # CONFIGURED mode here is how a bake that rendered nothing looked
        # healthy in the log while the call ran on per-turn retrieval.
        _log_setup(
            call_session, "ON", effective=call_session.knowledge_mode,
            configured=mode, baked=bool(baked), campaign=campaign_id[:8],
            path="outbound",
        )
    except Exception as exc:
        logger.warning("apply_campaign_knowledge failed (continuing without KB): %s", exc)


def _bake_inline_knowledge(call_session, tree: str, header: str,
                           campaign_id: str, mode: str) -> bool:
    """Fence + injection-scan ``tree`` and append it to the session prompt.

    Returns True if something was baked, False if there was nothing to bake
    (empty tree, or every line flagged as injection). Raises only on a genuine
    error (e.g. an import failure) — the caller turns that into a retrieve-mode
    fallback so the call is never left with zero KB.
    """
    if not (tree and tree.strip()):
        return False
    # The baked-in tree is tenant data, so delimit it (Microsoft "Spotlighting"
    # / OWASP LLM01) and tell the model it's data, not instructions — same fence
    # the per-turn retrieve path uses.
    from app.services.scripts.prompts.prompt_safety import (
        DATA_ONLY_NOTE,
        fence_untrusted,
        scan_for_injection,
    )

    # Content-integrity: drop any line shaped like an instruction to the model
    # (poisoned KB entry) BEFORE baking, mirroring the per-turn retrieve path
    # (turn_streamer). The fence alone isn't enough — a model can still act on
    # instruction-shaped fenced text.
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
        return False  # everything was flagged — bake nothing

    _KB_TAG = "company_knowledge"
    fenced = fence_untrusted(tree, tag=_KB_TAG)
    # Price guard sits ADJACENT to the knowledge it scopes — the placement the
    # offline A/B showed is what actually stops small models inventing figures.
    from app.services.scripts.prompts.guardrails import KNOWLEDGE_PRICE_GUARD

    call_session.system_prompt = (
        f"{call_session.system_prompt}\n\n{header}\n"
        f"{DATA_ONLY_NOTE(_KB_TAG)}\n{fenced}\n{KNOWLEDGE_PRICE_GUARD}"
    )
    logger.info(
        "campaign_knowledge_injected campaign=%s mode=%s chars=%d prompt_chars=%d",
        campaign_id[:12], mode, len(tree), len(call_session.system_prompt or ""),
        # Attributed to the call (2026-08-17): without this the line logs as
        # [call=-] and cannot be joined to the call it belongs to, so the
        # scorecard reported knowledge=0 for every call regardless of the truth.
        # prompt_chars is here because knowledge injection is the one block that
        # varies most by campaign, and prompt SIZE is now the dominant term in
        # turn latency — see report 6.
        extra={"call_id": getattr(call_session, "call_id", None)},
    )
    return True
