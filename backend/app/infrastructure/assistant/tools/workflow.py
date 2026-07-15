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
    offset: Optional[str] = Field(None, description="Time offset from meeting like '-1h', '-30m', '-10m'")
    scheduled_at: Optional[str] = Field(None, description="Absolute scheduled time if no offset")
    message: Optional[str] = Field(None, description="Custom reminder message")
    reminder_type: str = Field("sms", description="Reminder type: 'sms' or 'email'")


class ExecuteActionPlanInput(BaseModel):
    """Input for execute_action_plan tool"""
    intent: str = Field(..., description="Natural language description of the workflow")
    actions: List[Dict[str, Any]] = Field(
        ...,
        description="List of action steps: [{type, ...params, use_result_from?, condition?}]"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Context data like lead_id, campaign_id"
    )


async def schedule_reminder(
    tenant_id: str,
    db_client: Client,
    meeting_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    offset: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    message: Optional[str] = None,
    reminder_type: str = "sms",
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Schedule a reminder for a meeting or lead.

    Inserts a row into `reminders`, which the background reminder_worker picks up
    and delivers (SMS to the lead's number if present, else email). When a
    meeting_id is given, an `offset` like '-1h'/'-10m' is applied relative to the
    meeting's start_time; otherwise `scheduled_at` (absolute) or a 1h default.

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
                meeting_response = db_client.table("meetings").select(
                    "id, title, start_time, join_link"
                ).eq("id", meeting_id).eq("tenant_id", tenant_id).single().execute()
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
            },
            chained_result=chained_result,
            conversation_id=conversation_id,
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
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a multi-step action plan.

    Day 28: Core workflow orchestration tool.
    """
    try:
        from app.services.assistant_agent_service import get_assistant_agent_service

        service = get_assistant_agent_service(db_client)

        plan = await service.create_plan(
            tenant_id=tenant_id,
            intent=intent,
            context=context or {},
            actions=actions,
            conversation_id=conversation_id
        )

        result = await service.execute_plan(plan)

        return {
            "success": result.status in ["completed", "partially_completed"],
            "plan_id": result.id,
            "status": result.status if isinstance(result.status, str) else result.status.value,
            "steps_completed": result.successful_steps,
            "total_steps": len(result.actions),
            "results": [r.model_dump() for r in result.step_results],
            "error": result.error
        }

    except ValueError as e:
        logger.warning(f"Action plan validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error executing action plan: {e}")
        return {"success": False, "error": str(e)}
