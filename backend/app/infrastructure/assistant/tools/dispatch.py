"""Shared tool dispatch for the assistant.

One source of truth for routing a tool name to its implementation, used by
BOTH the LangGraph ``tool_executor`` (agent.py) and the streaming ReAct loop
(streaming.py). Returns the raw tool-result dict; each caller wraps it
(``ToolMessage`` for the graph, a ``role=tool`` dict for the stream) as needed.

Keeping the routing here means the two execution paths can never drift.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.security.rbac import (
    Permission,
    check_permission,
    get_effective_permissions,
)
from app.infrastructure.assistant.proposals import PROPOSAL_TOOLS
from app.infrastructure.assistant.tools import ACTION_TOOLS, ALL_TOOLS
from app.infrastructure.assistant.tools.coercion import coerce_bool

logger = logging.getLogger(__name__)

# Tools that accept a ``conversation_id`` kwarg (for action attribution /
# audit). Every other tool is called with just (tenant_id, db_client, **args).
_CONVO_AWARE = {"send_email", "send_sms", "initiate_call", "start_campaign", "report_issue", "create_campaign"}

_ACTOR_AWARE = {
    "get_knowledge_tree",
    "retrieve_knowledge",
    "update_knowledge_node",
}


@dataclass(frozen=True, slots=True)
class AssistantActionAuthorizationPolicy:
    """Declarative live-RBAC boundary for one assistant action tool."""

    required_permissions: tuple[Permission, ...] = ()
    delegated: bool = False
    supported: bool = True
    overwrite_permission: Permission | None = None


# Only permissions that already exist in the canonical RBAC model belong here.
# Communication actions stay unavailable until the product defines a real
# send-authority; connector configuration permissions are not send authority.
ACTION_AUTHORIZATION_POLICIES = {
    "create_campaign": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CAMPAIGNS_CREATE,),
        overwrite_permission=Permission.CAMPAIGNS_UPDATE,
    ),
    "start_campaign": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CAMPAIGNS_UPDATE,),
    ),
    "update_campaign_config": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CAMPAIGNS_UPDATE,),
    ),
    "manage_lead": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CAMPAIGNS_UPDATE,),
    ),
    "apply_campaign_voice": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CAMPAIGNS_UPDATE,),
    ),
    "initiate_call": AssistantActionAuthorizationPolicy(
        required_permissions=(Permission.CALLS_CREATE,),
    ),
    # The knowledge lease re-reads direction and live grants on the same
    # transaction/connection as its mutation, which is stronger than a
    # dispatcher pre-check. Do not weaken it into a separate TOCTOU check.
    "update_knowledge_node": AssistantActionAuthorizationPolicy(delegated=True),
    "send_email": AssistantActionAuthorizationPolicy(supported=False),
    "send_sms": AssistantActionAuthorizationPolicy(supported=False),
    "report_issue": AssistantActionAuthorizationPolicy(supported=False),
}

# Boolean flags small models routinely emit as strings ("confirm": "true").
# The Groq schemas accept ["boolean", "string"] for these so validation can't
# reject the call; this funnel normalises them exactly once, with the same
# defaults the schemas advertise.
_BOOL_ARGS = {
    "confirm": False,
    "unread_only": False,
    "only_leads": False,
    "today_only": True,
}


def _authorization_failure(
    error: str,
    message: str,
    *,
    required: str | list[str] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"error": error, "message": message}
    if required is not None:
        result["required"] = required
    return result


async def _authorize_action_tool(
    func_name: str,
    tenant_id: str,
    db_client: Any,
    actor_user_id: Optional[str],
    call_args: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Return a stable failure, or None when this execution is authorized."""

    actor = str(actor_user_id or "").strip()
    if not actor:
        return _authorization_failure(
            "permission_denied",
            "Authenticated user context is required for assistant actions.",
        )

    policy = ACTION_AUTHORIZATION_POLICIES.get(func_name)
    if policy is None or not policy.supported:
        return _authorization_failure(
            "tool_authorization_policy_unavailable",
            "This assistant action is unavailable until an authorization policy is configured.",
        )
    if policy.delegated:
        return None
    if not policy.required_permissions:
        return _authorization_failure(
            "tool_authorization_policy_unavailable",
            "This assistant action is unavailable until an authorization policy is configured.",
        )

    pool = getattr(db_client, "pool", None)
    if pool is None or not str(tenant_id or "").strip():
        return _authorization_failure(
            "authorization_unavailable",
            "Authorization is temporarily unavailable. Please try again.",
        )

    required_permissions = list(policy.required_permissions)
    if policy.overwrite_permission is not None and call_args.get(
        "overwrite_campaign_id"
    ):
        required_permissions.append(policy.overwrite_permission)

    try:
        current_permissions = await get_effective_permissions(
            pool,
            actor,
            tenant_id,
        )
        missing_permission = next(
            (
                required_permission
                for required_permission in required_permissions
                if not check_permission(current_permissions, required_permission)
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - action authorization fails closed
        logger.error(
            "assistant_action_authorization_unavailable tool=%s tenant=%s "
            "actor=%s err_type=%s",
            func_name,
            str(tenant_id)[:8],
            actor[:8],
            type(exc).__name__,
        )
        return _authorization_failure(
            "authorization_unavailable",
            "Authorization is temporarily unavailable. Please try again.",
        )

    if missing_permission is not None:
        return _authorization_failure(
            "permission_denied",
            "You do not have permission to run this assistant action.",
            required=missing_permission.value,
        )
    return None


async def dispatch_tool(
    func_name: str,
    tenant_id: str,
    db_client: Any,
    conversation_id: Optional[str],
    args: Optional[Dict[str, Any]],
    *,
    actor_user_id: Optional[str] = None,
    trusted_proposal_apply: bool = False,
) -> Dict[str, Any]:
    """Route ``func_name`` to its tool and return the raw result dict.

    Never raises: a missing tool, bad arguments, or an unexpected error all
    come back as ``{"error": ...}`` so the agent loop can keep going and the
    model can react to the failure.
    """
    entry = ALL_TOOLS.get(func_name)
    if not entry or not entry.get("function"):
        return {"error": f"Unknown tool: {func_name}"}

    fn = entry["function"]
    call_args = dict(args) if isinstance(args, dict) else {}
    # The model/proposal payload cannot choose the security principal.
    call_args.pop("actor_user_id", None)
    # This is an internal dispatcher control, never a tool argument. Only the
    # server path that atomically consumes an actor-bound proposal may set the
    # real keyword argument below.
    call_args.pop("trusted_proposal_apply", None)
    for key, default in _BOOL_ARGS.items():
        if key in call_args:
            call_args[key] = coerce_bool(call_args[key], default)

    if func_name in ACTION_TOOLS:
        actor = str(actor_user_id or "").strip()
        if not actor:
            return _authorization_failure(
                "permission_denied",
                "Authenticated user context is required for assistant actions.",
            )
        if (
            func_name in PROPOSAL_TOOLS
            and call_args.get("confirm") is True
            and trusted_proposal_apply is not True
        ):
            return _authorization_failure(
                "proposal_confirmation_required",
                "Preview this action and apply its server-issued proposal before confirming.",
            )
        authorization_failure = await _authorize_action_tool(
            func_name,
            tenant_id,
            db_client,
            actor,
            call_args,
        )
        if authorization_failure is not None:
            return authorization_failure

    try:
        if func_name in _ACTOR_AWARE:
            return await fn(
                tenant_id,
                db_client,
                actor_user_id=actor_user_id,
                **call_args,
            )
        if func_name in _CONVO_AWARE:
            return await fn(tenant_id, db_client, conversation_id=conversation_id, **call_args)
        return await fn(tenant_id, db_client, **call_args)
    except TypeError as exc:
        # Bad / extra kwargs from the model — surface as a tool error rather
        # than crashing the turn.
        logger.warning("dispatch_tool %s bad args %s: %s", func_name, call_args, exc)
        return {"error": f"Invalid arguments for {func_name}: {exc}"}
    except Exception as exc:  # tool internals already guard, but be safe
        logger.error("dispatch_tool %s failed: %s", func_name, exc, exc_info=True)
        return {"error": str(exc)}
