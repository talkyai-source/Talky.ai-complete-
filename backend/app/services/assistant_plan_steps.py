"""
assistant_plan_steps — per-step execution helpers for AssistantAgentService.

Extracted from assistant_agent_service.py to keep that file under 600 lines.
All logic is identical to the original private methods; only self.db_client
is replaced by an explicit db_client parameter, and self-calls to sibling
helpers are replaced by direct function calls within this module.
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from app.core.postgres_adapter import Client
from app.domain.models.action_plan import (
    ActionPlan,
    ActionStepCondition,
    AllowedActionType,
)

logger = logging.getLogger(__name__)

def evaluate_condition(
    condition: ActionStepCondition,
    plan: ActionPlan,
    current_index: int,
) -> bool:
    """Evaluate whether a step should execute based on its condition."""
    if condition == ActionStepCondition.ALWAYS:
        return True

    if current_index == 0:
        # First step has no previous result
        return condition == ActionStepCondition.ALWAYS

    prev_result = plan.get_step_result(current_index - 1)
    if prev_result is None:
        return True

    if condition == ActionStepCondition.IF_PREVIOUS_SUCCESS:
        return prev_result.success and not prev_result.skipped

    if condition == ActionStepCondition.IF_PREVIOUS_FAILED:
        return not prev_result.success and not prev_result.skipped

    return True


def apply_offset(base_time: datetime, offset: str) -> datetime:
    """
    Apply time offset like '-1h', '+30m', '-10m' to a base time.

    Args:
        base_time: Base datetime
        offset: Offset string like '-1h', '+30m', '-10m'

    Returns:
        Adjusted datetime
    """
    if not offset:
        return base_time

    # Parse sign
    sign = -1 if offset.startswith("-") else 1
    value = offset.lstrip("+-")

    # Parse unit and amount
    if value.endswith("h"):
        delta = timedelta(hours=int(value[:-1]) * sign)
    elif value.endswith("m"):
        delta = timedelta(minutes=int(value[:-1]) * sign)
    elif value.endswith("d"):
        delta = timedelta(days=int(value[:-1]) * sign)
    else:
        # Default to minutes
        delta = timedelta(minutes=int(value) * sign)

    return base_time + delta


async def schedule_reminder(
    db_client: Client,
    tenant_id: str,
    params: Dict[str, Any],
    chained_result: Dict[str, Any],
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Schedule a reminder based on offset from meeting or absolute time.

    Args:
        db_client: Database client
        tenant_id: Tenant ID
        params: Reminder parameters (offset, scheduled_at, message, etc.)
        chained_result: Result from previous action (may contain meeting info)
        conversation_id: Optional conversation ID
    """
    # Calculate scheduled_at
    offset = params.get("offset", "")

    if offset and chained_result.get("start_time"):
        # Relative to meeting start time
        start_time_str = chained_result["start_time"]
        if isinstance(start_time_str, str):
            base_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        else:
            base_time = start_time_str
        scheduled_at = apply_offset(base_time, offset)
    elif params.get("scheduled_at"):
        # Absolute time
        scheduled_at_str = params["scheduled_at"]
        if isinstance(scheduled_at_str, str):
            scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
        else:
            scheduled_at = scheduled_at_str
    else:
        # Default to 1 hour from now
        scheduled_at = datetime.utcnow() + timedelta(hours=1)

    # Don't create reminders in the past
    if scheduled_at <= datetime.utcnow():
        return {
            "success": False,
            "error": "Cannot schedule reminder in the past",
        }

    # Build reminder data
    reminder_data = {
        "tenant_id": tenant_id,
        "meeting_id": chained_result.get("meeting_id") or params.get("meeting_id"),
        "lead_id": params.get("lead_id"),
        "type": params.get("reminder_type", "sms"),
        "scheduled_at": scheduled_at.isoformat(),
        "status": "pending",
        "content": {
            "message": params.get("message", "Reminder for your upcoming meeting"),
            "template": params.get("template", "meeting_reminder_1h"),
            "title": chained_result.get("title") or params.get("title"),
            "join_link": chained_result.get("join_link"),
        },
    }

    try:
        response = db_client.table("reminders").insert(reminder_data).execute()

        if response.data:
            return {
                "success": True,
                "reminder_id": response.data[0]["id"],
                "scheduled_at": scheduled_at.isoformat(),
                "message": f"Reminder scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M')}",
            }

        return {"success": False, "error": "Failed to create reminder"}

    except Exception as e:
        logger.error(f"Error scheduling reminder: {e}")
        return {"success": False, "error": str(e)}


async def execute_action(
    db_client: Client,
    action_type: str,
    tenant_id: str,
    params: Dict[str, Any],
    context: Dict[str, Any],
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a single action by routing to the appropriate tool.

    Uses existing tools from app.infrastructure.assistant.tools
    """
    # Import tools here to avoid circular imports
    from app.infrastructure.assistant.tools import (
        book_meeting,
        send_email,
        send_sms,
        initiate_call,
        start_campaign,
        check_availability,
        update_meeting_tool,
        cancel_meeting_tool,
    )

    # Merge context into params
    merged_params = {**context, **params}
    chained = merged_params.pop("_chained_result", {})

    try:
        if action_type == AllowedActionType.BOOK_MEETING.value:
            return await book_meeting(
                tenant_id=tenant_id,
                db_client=db_client,
                title=merged_params.get("title", "Meeting"),
                start_time=merged_params.get("time") or merged_params.get("start_time", ""),
                duration_minutes=merged_params.get("duration_minutes", 30),
                attendees=merged_params.get("attendees", []),
                lead_id=merged_params.get("lead_id"),
                description=merged_params.get("description"),
                add_video_conference=merged_params.get("add_video_conference", True),
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.SEND_EMAIL.value:
            # Use chained result for meeting info if available
            template_context = merged_params.get("template_context", {})

            if chained.get("join_link"):
                template_context["join_link"] = chained["join_link"]
            if chained.get("meeting_id"):
                template_context["meeting_id"] = chained["meeting_id"]
            if chained.get("title"):
                template_context["title"] = chained["title"]
            if chained.get("start_time"):
                template_context["start_time"] = chained["start_time"]

            return await send_email(
                tenant_id=tenant_id,
                db_client=db_client,
                to=merged_params.get("to", []),
                subject=merged_params.get("subject", ""),
                body=merged_params.get("body", ""),
                template_name=merged_params.get("template"),
                template_context=template_context,
                conversation_id=conversation_id,
                # Plan execution is an already-approved action — send immediately,
                # not the interactive preview-first path.
                confirm=True,
            )

        elif action_type == AllowedActionType.SEND_SMS.value:
            return await send_sms(
                tenant_id=tenant_id,
                db_client=db_client,
                to=merged_params.get("to", []),
                message=merged_params.get("message", ""),
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.SCHEDULE_REMINDER.value:
            return await schedule_reminder(
                db_client=db_client,
                tenant_id=tenant_id,
                params=merged_params,
                chained_result=chained,
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.INITIATE_CALL.value:
            return await initiate_call(
                tenant_id=tenant_id,
                db_client=db_client,
                phone_number=merged_params.get("phone_number", ""),
                campaign_id=merged_params.get("campaign_id"),
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.START_CAMPAIGN.value:
            return await start_campaign(
                tenant_id=tenant_id,
                db_client=db_client,
                campaign_id=merged_params.get("campaign_id", ""),
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.CHECK_AVAILABILITY.value:
            return await check_availability(
                tenant_id=tenant_id,
                db_client=db_client,
                date_str=merged_params.get("date", ""),
                duration_minutes=merged_params.get("duration_minutes", 30),
            )

        elif action_type == AllowedActionType.UPDATE_MEETING.value:
            return await update_meeting_tool(
                tenant_id=tenant_id,
                db_client=db_client,
                meeting_id=merged_params.get("meeting_id", ""),
                new_time=merged_params.get("new_time"),
                new_title=merged_params.get("new_title"),
                conversation_id=conversation_id,
            )

        elif action_type == AllowedActionType.CANCEL_MEETING.value:
            return await cancel_meeting_tool(
                tenant_id=tenant_id,
                db_client=db_client,
                meeting_id=merged_params.get("meeting_id", ""),
                reason=merged_params.get("reason"),
                conversation_id=conversation_id,
            )

        else:
            return {"success": False, "error": f"Unknown action type: {action_type}"}

    except Exception as e:
        logger.error(f"Action execution error ({action_type}): {e}")
        return {"success": False, "error": str(e)}
