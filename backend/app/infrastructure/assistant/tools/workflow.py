"""
Workflow orchestration tools for the assistant agent.
"""

import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class ScheduleReminderInput(BaseModel):
    """Input for schedule_reminder tool"""

    meeting_id: Optional[str] = Field(None, description="Meeting ID to attach reminder to")
    lead_id: Optional[str] = Field(None, description="Lead ID for reminder")
    offset: Optional[str] = Field(
        None, description="Time offset from meeting like '-1h', '-30m', '-10m'"
    )
    scheduled_at: Optional[str] = Field(None, description="Absolute scheduled time if no offset")
    message: Optional[str] = Field(None, description="Custom reminder message")
    reminder_type: str = Field(
        ..., description="Explicitly selected reminder type: 'sms' or 'email'"
    )
    confirm: bool = Field(False, description="Preview first; Apply confirms.")


class ExecuteActionPlanInput(BaseModel):
    """Input for execute_action_plan tool"""

    intent: str = Field(..., description="Natural language description of the workflow")
    actions: List[Dict[str, Any]] = Field(
        ..., description="List of action steps: [{type, ...params, use_result_from?, condition?}]"
    )
    context: Optional[Dict[str, Any]] = Field(
        None, description="Context data like lead_id, campaign_id"
    )
    confirm: bool = Field(False, description="Preview all steps first; Apply confirms.")


async def schedule_reminder(
    tenant_id: str,
    db_client: Client,
    meeting_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    offset: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    message: Optional[str] = None,
    reminder_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
    _expected_recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Schedule a reminder for a meeting or lead.

    Inserts a row into `reminders`, which the background reminder_worker picks up
    and delivers through the explicitly selected SMS or email channel. When a
    meeting_id is given, an `offset` is relative to the meeting start time;
    otherwise an explicit time with UTC offset is required.

    Delegates to the SAME module-level `schedule_reminder` used by
    execute_action_plan (assistant_plan_steps), so the tool path and the
    action-plan path can't drift. (Previously this called a nonexistent
    AssistantAgentService method and always failed.)
    """
    try:
        from app.services.assistant_plan_steps import schedule_reminder as _schedule_reminder_step

        # If meeting_id is provided, pull its start_time/title/link so an offset
        # can be applied relative to the meeting.
        chained_result: Dict[str, Any] = {}
        if meeting_id:
            try:
                meeting_response = (
                    db_client.table("meetings")
                    .select("id, title, start_time, join_link")
                    .eq("id", meeting_id)
                    .eq("tenant_id", tenant_id)
                    .single()
                    .execute()
                )
                if meeting_response.data:
                    chained_result = {
                        "meeting_id": meeting_response.data["id"],
                        "title": meeting_response.data.get("title"),
                        "start_time": meeting_response.data.get("start_time"),
                        "join_link": meeting_response.data.get("join_link"),
                    }
            except Exception as me:  # noqa: BLE001
                logger.warning("schedule_reminder: meeting lookup failed: %s", me)

        return await _schedule_reminder_step(
            db_client=db_client,
            tenant_id=tenant_id,
            params={
                "meeting_id": meeting_id,
                "lead_id": lead_id,
                "offset": offset,
                "scheduled_at": scheduled_at,
                "message": message,
                "reminder_type": reminder_type,
                "_expected_recipient": _expected_recipient,
            },
            chained_result=chained_result,
            conversation_id=conversation_id,
            preview=not confirm,
        )

    except Exception as e:
        logger.error(f"Error scheduling reminder: {e}")
        return {"success": False, "error": str(e)}


async def execute_action_plan(
    tenant_id: str,
    db_client: Client,
    intent: str,
    actions: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a multi-step action plan.

    Day 28: Core workflow orchestration tool.
    """
    try:
        from app.services.assistant_agent_service import get_assistant_agent_service
        from app.infrastructure.assistant.tools.dispatch import _authorize_action_tool

        async def authorize(action_type, parameters):
            return await _authorize_action_tool(
                action_type, tenant_id, db_client, actor_user_id, parameters
            )

        failure = await _authorize_action_tool(
            "execute_action_plan",
            tenant_id,
            db_client,
            actor_user_id,
            {"actions": actions},
        )
        if failure:
            return failure
        if not confirm:
            return await _preview_plan(
                tenant_id, db_client, intent, actions, context, conversation_id, actor_user_id
            )

        service = get_assistant_agent_service(db_client)

        plan = await service.create_plan(
            tenant_id=tenant_id,
            intent=intent,
            context=context or {},
            actions=actions,
            conversation_id=conversation_id,
            user_id=actor_user_id,
        )

        result = await service.execute_plan(plan, authorize_action=authorize)

        unknown = any(
            (step.result or {}).get("status") in {"unknown", "in_progress", "outcome_unknown"}
            for step in result.step_results
        )
        executed = [step for step in result.step_results if not step.skipped]
        proof_complete = (
            result.status == "completed"
            and bool(executed)
            and len(result.step_results) == len(result.actions)
            and {step.step_index for step in result.step_results} == set(range(len(result.actions)))
            and all(
                step.success and (step.result or {}).get("confirmation_allowed") is True
                for step in executed
            )
        )
        return {
            "success": result.status == "completed",
            "confirmation_allowed": proof_complete,
            "plan_id": result.id,
            "status": (
                "unknown"
                if unknown
                else result.status if isinstance(result.status, str) else result.status.value
            ),
            "steps_completed": result.successful_steps,
            "total_steps": len(result.actions),
            "results": [r.model_dump() for r in result.step_results],
            "error": result.error,
        }

    except ValueError as e:
        logger.warning(f"Action plan validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error executing action plan: {e}")
        return {"success": False, "error": str(e)}


async def _preview_plan(tenant_id, db_client, intent, actions, context, conversation_id, actor):
    """Resolve each preview once so Apply executes the reviewed destinations/content."""
    import json
    from app.infrastructure.assistant.tools import ACTION_TOOLS
    from app.infrastructure.assistant.tools.dispatch import dispatch_tool
    from app.infrastructure.assistant.proposals import PROPOSAL_TOOLS
    from app.services.assistant_plan_steps import apply_offset
    from datetime import datetime

    frozen, changes = [], []
    for index, step in enumerate(actions):
        name = step["type"]
        parameters = {
            **(context or {}),
            **{k: v for k, v in step.items() if k not in {"type", "use_result_from", "condition"}},
        }
        if "time" in parameters and "start_time" not in parameters:
            parameters["start_time"] = parameters.pop("time")
        if "template" in parameters and "template_name" not in parameters:
            parameters["template_name"] = parameters.pop("template")
        reference = step.get("use_result_from")
        if reference is not None:
            if (
                isinstance(reference, bool)
                or not isinstance(reference, int)
                or not 0 <= reference < index
            ):
                return {
                    "success": False,
                    "error": f"Step {index + 1} refers to an invalid earlier step.",
                }
            if name == "send_email" and parameters.get("template_name"):
                return {
                    "success": False,
                    "error": "Preview the booking first, then create its templated confirmation email. A plan can send an explicit reviewed message.",
                }
            if name == "schedule_reminder" and parameters.get("offset"):
                start = frozen[reference].get("start_time")
                if start:
                    parameters["scheduled_at"] = apply_offset(
                        datetime.fromisoformat(start.replace("Z", "+00:00")),
                        parameters.pop("offset"),
                    ).isoformat()
        schema = ACTION_TOOLS[name]["input_schema"]
        parameters = schema.model_validate(parameters).model_dump(exclude_none=True)
        parameters.pop("confirm", None)
        if name in PROPOSAL_TOOLS:
            preview = await dispatch_tool(
                name,
                tenant_id,
                db_client,
                conversation_id,
                {**parameters, "confirm": False},
                actor_user_id=actor,
            )
            if preview.get("preview") is not True:
                return {
                    "success": False,
                    "error": f"Step {index + 1}: "
                    + str(
                        preview.get("error")
                        or preview.get("message")
                        or "Cannot prepare this action."
                    ),
                }
            parameters = dict(preview.get("_apply_args") or parameters)
            changes.extend(
                {**change, "field": f"Step {index + 1} · {change['field']}"}
                for change in preview.get("changes", [])
            )
        else:
            # Read-only availability and campaign-start arguments are explicit.
            # Do not call start_campaign during preview.
            changes.append(
                {
                    "field": f"Step {index + 1}: {name}",
                    "before": None,
                    "after": json.dumps(parameters, ensure_ascii=False, default=str),
                }
            )
        condition = step.get("condition", "always" if index == 0 else "if_previous_success")
        if condition not in {"always", "if_previous_success", "if_previous_failed"}:
            return {"success": False, "error": f"Step {index + 1} has an invalid condition."}
        frozen.append(
            {
                "type": name,
                **parameters,
                "condition": condition,
                **({"use_result_from": reference} if reference is not None else {}),
            }
        )
    return {
        "preview": True,
        "changes": changes,
        "_apply_args": {"intent": intent, "actions": frozen, "context": {}},
        "note": "No steps executed. An uncertain result stops the plan. Permissions are rechecked on Apply.",
    }
