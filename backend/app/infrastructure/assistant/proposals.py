"""Pending edit proposals for the assistant diff accept/reject flow.

When an edit tool runs with ``confirm=false`` it returns a PREVIEW
(``{"preview": True, "changes": [...]}`` or, for voice, ``{"preview": True,
"campaigns": [...]}``). Instead of feeding that back to the model and asking the
user to "type yes", the streaming loop hands it here as a pending proposal. The
client renders a diff card with Apply / Reject buttons; an ``apply_proposal``
message re-runs the SAME tool with ``confirm=true``.

Storing the tool + args server-side (not trusting the client to echo them) is
what guarantees the applied change equals the previewed one — the client only
sends back a ``proposal_id``.

Storage is an in-process dict. The assistant WebSocket is served by the
``talky-api`` process, which runs ``uvicorn --workers 1`` (see the telephony
single-worker invariant), so this dict is process-global and survives the WS
reconnect churn (idle drop / token rotation) within a process lifetime. A
process restart (deploy) drops pending proposals — acceptable: the user simply
re-asks. We deliberately avoid a DB round-trip on the hot path.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Edit tools eligible for the proposal flow. Each returns the standard preview
# shape when called with confirm=false.
PROPOSAL_TOOLS = {
    "create_campaign",
    "update_campaign_config",
    "update_knowledge_node",
    "manage_lead",
    "apply_campaign_voice",
    "send_email",
}

# proposal_id -> proposal dict
_PENDING: Dict[str, Dict[str, Any]] = {}


def new_proposal_id() -> str:
    return "prop_" + uuid.uuid4().hex[:16]


def is_preview_result(result: Any) -> bool:
    """True when a tool result is an unapplied preview with something to show."""
    return (
        isinstance(result, dict)
        and result.get("preview") is True
        and (bool(result.get("changes")) or bool(result.get("campaigns")))
    )


def store_proposal(
    *,
    tool: str,
    args: Optional[Dict[str, Any]],
    result: Dict[str, Any],
    tenant_id: str,
    actor_user_id: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a pending proposal and return it (includes proposal_id)."""
    actor = str(actor_user_id or "").strip()
    if not actor:
        raise ValueError("actor_user_id is required")
    proposal_id = new_proposal_id()
    proposal = {
        "proposal_id": proposal_id,
        "tool": tool,
        # Drop confirm — apply re-adds confirm=true. Keep everything else verbatim.
        "args": {k: v for k, v in (args or {}).items() if k != "confirm"},
        "changes": result.get("changes") or [],
        "campaigns": result.get("campaigns") or [],
        "note": result.get("note") or "",
        "warnings": result.get("warnings") or [],
        # Duplicate-campaign info (create_campaign): lets the card offer
        # Create anyway / Overwrite existing / Cancel, and lets apply-with-
        # mode=overwrite resolve the target id SERVER-side (never trusting a
        # client-echoed campaign id).
        "duplicate": result.get("duplicate"),
        "tenant_id": tenant_id,
        "actor_user_id": actor,
        "conversation_id": conversation_id,
    }
    _PENDING[proposal_id] = proposal
    return proposal


def get_proposal(
    proposal_id: str,
    tenant_id: str,
    actor_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Return a proposal only to its authenticated tenant/user owner.

    Read-only (does NOT consume). Prefer :func:`pop_proposal` on the APPLY path
    so a proposal can be applied exactly once — get+later-clear leaves a window
    where two connections (two browser tabs) both read the same pending proposal
    and each fire the INSERT, double-creating the campaign.
    """
    p = _PENDING.get(proposal_id)
    if p is None:
        return None
    actor = str(actor_user_id or "").strip()
    if p.get("tenant_id") != tenant_id or p.get("actor_user_id") != actor:
        logger.warning(
            "proposal principal mismatch: proposal=%s tenant=%s actor=%s",
            proposal_id,
            tenant_id,
            actor[:8],
        )
        return None
    return p


def pop_proposal(
    proposal_id: str,
    tenant_id: str,
    actor_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Atomically fetch/remove a proposal after tenant and actor checks.

    ``dict.pop`` is atomic under the GIL and talky-api runs a single worker, so
    exactly one caller wins the pop; a concurrent second apply of the same
    proposal_id gets None and is told "no longer available" — closing the
    two-tab / double-click double-insert race (Case 4 hardening). A tenant
    principal mismatch re-inserts the entry (it was not this user's to consume).
    """
    p = _PENDING.pop(proposal_id, None)
    if p is None:
        return None
    actor = str(actor_user_id or "").strip()
    if p.get("tenant_id") != tenant_id or p.get("actor_user_id") != actor:
        _PENDING[proposal_id] = p  # not ours — put it back
        logger.warning(
            "proposal principal mismatch on pop: proposal=%s tenant=%s actor=%s",
            proposal_id,
            tenant_id,
            actor[:8],
        )
        return None
    return p


def clear_proposal(proposal_id: str, tenant_id: str, actor_user_id: str) -> bool:
    """Reject only a proposal owned by this authenticated tenant principal."""

    return pop_proposal(proposal_id, tenant_id, actor_user_id) is not None
