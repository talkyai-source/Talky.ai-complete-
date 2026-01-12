"""
Assistant Agent Tools
LangGraph tools for querying tenant data and triggering actions.

All tools receive tenant_id as context and are scoped to that tenant's data.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta
from pydantic import BaseModel, Field
from supabase import Client

logger = logging.getLogger(__name__)


# =============================================================================
# QUERY TOOLS (Read-only)
# =============================================================================

class GetDashboardStatsInput(BaseModel):
    """Input for get_dashboard_stats tool"""
    date: Optional[str] = Field(None, description="Date in YYYY-MM-DD format, defaults to today")


async def get_dashboard_stats(
    tenant_id: str,
    supabase: Client,
    date_str: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get dashboard statistics for the tenant.
    
    Returns:
        - Total calls today
        - Calls completed
        - Calls failed
        - Success rate
        - Active campaigns
    """
    try:
        # Default to today
        target_date = date_str or date.today().isoformat()
        
        # Get calls for the day
        calls_response = supabase.table("calls").select(
            "id, status, outcome, goal_achieved",
            count="exact"
        ).eq("tenant_id", tenant_id).gte(
            "created_at", f"{target_date}T00:00:00"
        ).lte(
            "created_at", f"{target_date}T23:59:59"
        ).execute()
        
        total_calls = calls_response.count or 0
        completed = len([c for c in calls_response.data if c.get("status") == "completed"])
        failed = len([c for c in calls_response.data if c.get("status") == "failed"])
        goal_achieved = len([c for c in calls_response.data if c.get("goal_achieved")])
        
        # Get active campaigns
        campaigns_response = supabase.table("campaigns").select(
            "id",
            count="exact"
        ).eq("tenant_id", tenant_id).eq("status", "running").execute()
        
        active_campaigns = campaigns_response.count or 0
        
        success_rate = (completed / total_calls * 100) if total_calls > 0 else 0
        
        return {
            "date": target_date,
            "total_calls": total_calls,
            "completed": completed,
            "failed": failed,
            "goal_achieved": goal_achieved,
            "success_rate": round(success_rate, 1),
            "active_campaigns": active_campaigns
        }
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return {"error": str(e)}


async def get_usage_info(
    tenant_id: str,
    supabase: Client
) -> Dict[str, Any]:
    """
    Get plan usage information.
    
    Returns:
        - Plan name
        - Minutes allocated
        - Minutes used
        - Minutes remaining
        - Plan expiry (if applicable)
    """
    try:
        # Get tenant with plan info
        tenant_response = supabase.table("tenants").select(
            "id, plan_id, minutes_allocated, minutes_used, subscription_status"
        ).eq("id", tenant_id).single().execute()
        
        tenant = tenant_response.data
        if not tenant:
            return {"error": "Tenant not found"}
        
        # Get plan details
        plan_response = supabase.table("plans").select(
            "name, price, minutes"
        ).eq("id", tenant.get("plan_id")).single().execute()
        
        plan = plan_response.data or {}
        
        minutes_allocated = tenant.get("minutes_allocated", 0)
        minutes_used = tenant.get("minutes_used", 0)
        
        return {
            "plan_name": plan.get("name", "Free"),
            "plan_price": plan.get("price", 0),
            "minutes_allocated": minutes_allocated,
            "minutes_used": minutes_used,
            "minutes_remaining": max(0, minutes_allocated - minutes_used),
            "usage_percentage": round((minutes_used / minutes_allocated * 100), 1) if minutes_allocated > 0 else 0,
            "subscription_status": tenant.get("subscription_status", "inactive")
        }
    except Exception as e:
        logger.error(f"Error getting usage info: {e}")
        return {"error": str(e)}


class GetLeadsInput(BaseModel):
    """Input for get_leads tool"""
    campaign_id: Optional[str] = Field(None, description="Filter by campaign ID")
    status: Optional[str] = Field(None, description="Filter by status (pending, completed, failed)")
    limit: int = Field(10, description="Maximum number of leads to return")


async def get_leads(
    tenant_id: str,
    supabase: Client,
    campaign_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Get leads for the tenant with optional filters.
    """
    try:
        query = supabase.table("leads").select(
            "id, phone_number, first_name, last_name, email, status, priority, call_attempts, last_call_result",
            count="exact"
        ).eq("tenant_id", tenant_id)
        
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        if status:
            query = query.eq("status", status)
        
        response = query.order("created_at", desc=True).limit(limit).execute()
        
        return {
            "total_count": response.count,
            "returned_count": len(response.data),
            "leads": response.data
        }
    except Exception as e:
        logger.error(f"Error getting leads: {e}")
        return {"error": str(e)}


async def get_campaigns(
    tenant_id: str,
    supabase: Client,
    status: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get campaigns for the tenant.
    """
    try:
        query = supabase.table("campaigns").select(
            "id, name, status, goal, total_leads, calls_completed, calls_failed, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id)
        
        if status:
            query = query.eq("status", status)
        
        response = query.order("created_at", desc=True).limit(20).execute()
        
        return {
            "total_count": response.count,
            "campaigns": response.data
        }
    except Exception as e:
        logger.error(f"Error getting campaigns: {e}")
        return {"error": str(e)}


async def get_recent_calls(
    tenant_id: str,
    supabase: Client,
    today_only: bool = True,
    outcome: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Get recent calls for the tenant.
    """
    try:
        query = supabase.table("calls").select(
            "id, phone_number, status, outcome, goal_achieved, duration_seconds, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id)
        
        if today_only:
            today = date.today().isoformat()
            query = query.gte("created_at", f"{today}T00:00:00")
        
        if outcome:
            query = query.eq("outcome", outcome)
        
        response = query.order("created_at", desc=True).limit(limit).execute()
        
        return {
            "total_count": response.count,
            "calls": response.data
        }
    except Exception as e:
        logger.error(f"Error getting calls: {e}")
        return {"error": str(e)}


async def get_actions_today(
    tenant_id: str,
    supabase: Client
) -> Dict[str, Any]:
    """
    Get assistant actions performed today.
    """
    try:
        today = date.today().isoformat()
        
        response = supabase.table("assistant_actions").select(
            "id, type, status, triggered_by, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id).gte(
            "created_at", f"{today}T00:00:00"
        ).execute()
        
        # Group by type
        by_type = {}
        for action in response.data:
            action_type = action.get("type")
            by_type[action_type] = by_type.get(action_type, 0) + 1
        
        return {
            "total_actions": response.count,
            "by_type": by_type,
            "recent_actions": response.data[:5]
        }
    except Exception as e:
        logger.error(f"Error getting actions: {e}")
        return {"error": str(e)}


# =============================================================================
# ACTION TOOLS (Write operations)
# =============================================================================

class SendEmailInput(BaseModel):
    """Input for send_email tool"""
    to: List[str] = Field(..., description="List of email addresses")
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body content (plain text)")
    body_html: Optional[str] = Field(None, description="Optional HTML body")
    template_name: Optional[str] = Field(None, description="Template to use: meeting_confirmation, follow_up, reminder")
    template_context: Optional[Dict[str, Any]] = Field(None, description="Variables for template rendering")
    lead_ids: Optional[List[str]] = Field(None, description="Optional lead IDs if sending to leads")


async def send_email(
    tenant_id: str,
    supabase: Client,
    to: List[str],
    subject: str,
    body: str,
    body_html: Optional[str] = None,
    template_name: Optional[str] = None,
    template_context: Optional[Dict[str, Any]] = None,
    lead_ids: Optional[List[str]] = None,
    connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send email via connected email provider (Gmail) or SMTP fallback.
    
    Supports:
    - Direct email with subject/body
    - Templated emails (meeting_confirmation, follow_up, reminder)
    - HTML and plain text
    - Audit trail via assistant_actions table
    """
    try:
        from app.services.email_service import get_email_service, EmailNotConnectedError
        from app.infrastructure.connectors.email.smtp import SMTPConnector
        
        service = get_email_service(supabase)
        
        try:
            # Try sending via connected email provider (Gmail)
            result = await service.send_email(
                tenant_id=tenant_id,
                to=to,
                subject=subject,
                body=body,
                body_html=body_html,
                template_name=template_name,
                template_context=template_context,
                lead_ids=lead_ids,
                conversation_id=conversation_id,
                triggered_by="assistant"
            )
            return result
            
        except EmailNotConnectedError:
            # Fallback to SMTP if configured
            if SMTPConnector.is_configured():
                logger.info("Using SMTP fallback for email sending")
                smtp = SMTPConnector()
                
                # Render template if specified
                if template_name and template_context:
                    from app.domain.services.email_template_manager import get_email_template_manager
                    mgr = get_email_template_manager()
                    rendered = mgr.render_email(template_name, **template_context)
                    subject = rendered.subject
                    body = rendered.body
                    body_html = rendered.body_html or body_html
                
                result = await smtp.send_email(
                    to=to,
                    subject=subject,
                    body=body,
                    body_html=body_html
                )
                
                return {
                    "success": True,
                    "message_id": result.id,
                    "provider": "smtp",
                    "recipients": to,
                    "message": f"Email sent to {len(to)} recipient(s)"
                }
            else:
                return {
                    "success": False,
                    "error": "No email provider connected. Please connect Gmail from Settings > Integrations.",
                    "email_required": True
                }
                
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return {"success": False, "error": str(e)}


class SendSMSInput(BaseModel):
    """Input for send_sms tool"""
    to: List[str] = Field(..., description="List of phone numbers")
    message: str


async def send_sms(
    tenant_id: str,
    supabase: Client,
    to: List[str],
    message: str,
    lead_ids: Optional[List[str]] = None,
    connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send SMS via connected SMS provider.
    """
    try:
        action_data = {
            "tenant_id": tenant_id,
            "type": "send_sms",
            "status": "pending",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "connector_id": connector_id,
            "input_data": {
                "to": to,
                "message": message,
                "lead_ids": lead_ids
            }
        }
        
        action_response = supabase.table("assistant_actions").insert(action_data).execute()
        action_id = action_response.data[0]["id"] if action_response.data else None
        
        # TODO: Actually send SMS via connector
        
        if action_id:
            supabase.table("assistant_actions").update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "output_data": {"message": "SMS queued for delivery"}
            }).eq("id", action_id).execute()
        
        return {
            "success": True,
            "action_id": action_id,
            "message": f"SMS to {len(to)} recipient(s) queued",
            "recipients": to
        }
    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        return {"success": False, "error": str(e)}


class InitiateCallInput(BaseModel):
    """Input for initiate_call tool"""
    phone_number: str
    campaign_id: Optional[str] = None


async def initiate_call(
    tenant_id: str,
    supabase: Client,
    phone_number: str,
    campaign_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Initiate an outbound call.
    """
    try:
        action_data = {
            "tenant_id": tenant_id,
            "type": "initiate_call",
            "status": "pending",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "input_data": {
                "phone_number": phone_number,
                "campaign_id": campaign_id,
                "lead_id": lead_id
            }
        }
        
        action_response = supabase.table("assistant_actions").insert(action_data).execute()
        action_id = action_response.data[0]["id"] if action_response.data else None
        
        # TODO: Queue call via dialer worker
        
        return {
            "success": True,
            "action_id": action_id,
            "message": f"Call to {phone_number} has been queued",
            "phone_number": phone_number
        }
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        return {"success": False, "error": str(e)}


class StartCampaignInput(BaseModel):
    """Input for start_campaign tool"""
    campaign_id: str


async def start_campaign(
    tenant_id: str,
    supabase: Client,
    campaign_id: str,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Start or resume a campaign.
    """
    try:
        # Verify campaign belongs to tenant
        campaign = supabase.table("campaigns").select(
            "id, name, status"
        ).eq("id", campaign_id).eq("tenant_id", tenant_id).single().execute()
        
        if not campaign.data:
            return {"success": False, "error": "Campaign not found"}
        
        current_status = campaign.data.get("status")
        if current_status == "running":
            return {"success": False, "error": "Campaign is already running"}
        
        # Update campaign status
        supabase.table("campaigns").update({
            "status": "running",
            "started_at": datetime.utcnow().isoformat() if current_status == "draft" else None
        }).eq("id", campaign_id).execute()
        
        # Log action
        supabase.table("assistant_actions").insert({
            "tenant_id": tenant_id,
            "type": "start_campaign",
            "status": "completed",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "input_data": {"campaign_id": campaign_id},
            "output_data": {"previous_status": current_status},
            "completed_at": datetime.utcnow().isoformat()
        }).execute()
        
        return {
            "success": True,
            "message": f"Campaign '{campaign.data.get('name')}' has been started",
            "campaign_id": campaign_id
        }
    except Exception as e:
        logger.error(f"Error starting campaign: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# MEETING TOOLS
# =============================================================================

class CheckAvailabilityInput(BaseModel):
    """Input for check_availability tool"""
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    duration_minutes: int = Field(30, description="Meeting duration in minutes")


async def check_availability(
    tenant_id: str,
    supabase: Client,
    date_str: str,
    duration_minutes: int = 30
) -> Dict[str, Any]:
    """
    Check available meeting slots for a given date.
    
    Requires connected Google Calendar or Microsoft Outlook.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError
        
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_time = datetime.combine(target_date, datetime.min.time().replace(hour=9))  # 9 AM
        end_time = datetime.combine(target_date, datetime.min.time().replace(hour=18))   # 6 PM
        
        service = get_meeting_service(supabase)
        
        slots = await service.get_availability(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes
        )
        
        return {
            "success": True,
            "date": date_str,
            "duration_minutes": duration_minutes,
            "available_slots": slots,
            "slot_count": len(slots)
        }
    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return {"success": False, "error": str(e)}


class BookMeetingInput(BaseModel):
    """Input for book_meeting tool"""
    title: str = Field(..., description="Meeting title")
    start_time: str = Field(..., description="Start time in ISO format (e.g., 2026-01-08T10:00:00)")
    duration_minutes: int = Field(30, description="Duration in minutes")
    attendees: List[str] = Field(default_factory=list, description="Attendee email addresses")
    lead_id: Optional[str] = Field(None, description="Lead ID if meeting is with a lead")
    description: Optional[str] = Field(None, description="Meeting description")
    add_video_conference: bool = Field(True, description="Add Google Meet or Teams link")


async def book_meeting(
    tenant_id: str,
    supabase: Client,
    title: str,
    start_time: str,
    duration_minutes: int = 30,
    attendees: Optional[List[str]] = None,
    lead_id: Optional[str] = None,
    description: Optional[str] = None,
    add_video_conference: bool = True,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Book a meeting via connected calendar.
    
    Creates calendar event and saves meeting record to database.
    Returns join link for video conference if enabled.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError
        
        # Parse start time
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        
        service = get_meeting_service(supabase)
        
        result = await service.create_meeting(
            tenant_id=tenant_id,
            title=title,
            start_time=start_dt,
            duration_minutes=duration_minutes,
            attendees=attendees or [],
            lead_id=lead_id,
            description=description,
            add_video_conference=add_video_conference,
            triggered_by="assistant"
        )
        
        return result
        
    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error booking meeting: {e}")
        return {"success": False, "error": str(e)}


class UpdateMeetingInput(BaseModel):
    """Input for update_meeting tool"""
    meeting_id: str = Field(..., description="Meeting ID to update")
    new_time: Optional[str] = Field(None, description="New start time in ISO format")
    new_title: Optional[str] = Field(None, description="New meeting title")


async def update_meeting_tool(
    tenant_id: str,
    supabase: Client,
    meeting_id: str,
    new_time: Optional[str] = None,
    new_title: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update/reschedule an existing meeting.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError
        
        service = get_meeting_service(supabase)
        
        new_start_time = None
        if new_time:
            new_start_time = datetime.fromisoformat(new_time.replace("Z", "+00:00"))
        
        result = await service.update_meeting(
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            new_start_time=new_start_time,
            new_title=new_title
        )
        
        return result
        
    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error updating meeting: {e}")
        return {"success": False, "error": str(e)}


class CancelMeetingInput(BaseModel):
    """Input for cancel_meeting tool"""
    meeting_id: str = Field(..., description="Meeting ID to cancel")
    reason: Optional[str] = Field(None, description="Cancellation reason")


async def cancel_meeting_tool(
    tenant_id: str,
    supabase: Client,
    meeting_id: str,
    reason: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Cancel a scheduled meeting.
    """
    try:
        from app.services.meeting_service import get_meeting_service
        
        service = get_meeting_service(supabase)
        
        result = await service.cancel_meeting(
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            reason=reason
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error cancelling meeting: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# DAY 28: WORKFLOW ORCHESTRATION TOOLS
# =============================================================================

class ScheduleReminderInput(BaseModel):
    """Input for schedule_reminder tool"""
    meeting_id: Optional[str] = Field(None, description="Meeting ID to attach reminder to")
    lead_id: Optional[str] = Field(None, description="Lead ID for reminder")
    offset: Optional[str] = Field(None, description="Time offset from meeting like '-1h', '-30m', '-10m'")
    scheduled_at: Optional[str] = Field(None, description="Absolute scheduled time if no offset")
    message: Optional[str] = Field(None, description="Custom reminder message")
    reminder_type: str = Field("sms", description="Reminder type: 'sms' or 'email'")


async def schedule_reminder(
    tenant_id: str,
    supabase: Client,
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
    
    Day 28: Integrates with AssistantAgentService for workflow orchestration.
    """
    try:
        from app.services.assistant_agent_service import get_assistant_agent_service
        
        service = get_assistant_agent_service(supabase)
        
        # If meeting_id provided, get meeting details for chaining
        chained_result = {}
        if meeting_id:
            meeting_response = supabase.table("meetings").select(
                "id, title, start_time, join_link"
            ).eq("id", meeting_id).eq("tenant_id", tenant_id).single().execute()
            
            if meeting_response.data:
                chained_result = {
                    "meeting_id": meeting_response.data["id"],
                    "title": meeting_response.data.get("title"),
                    "start_time": meeting_response.data.get("start_time"),
                    "join_link": meeting_response.data.get("join_link")
                }
        
        result = await service._schedule_reminder(
            tenant_id=tenant_id,
            params={
                "meeting_id": meeting_id,
                "lead_id": lead_id,
                "offset": offset,
                "scheduled_at": scheduled_at,
                "message": message,
                "reminder_type": reminder_type
            },
            chained_result=chained_result,
            conversation_id=conversation_id
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error scheduling reminder: {e}")
        return {"success": False, "error": str(e)}


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


async def execute_action_plan(
    tenant_id: str,
    supabase: Client,
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
        
        service = get_assistant_agent_service(supabase)
        
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


# =============================================================================
# TOOL REGISTRY
# =============================================================================


QUERY_TOOLS = {
    "get_dashboard_stats": {
        "function": get_dashboard_stats,
        "description": "Get today's call statistics, success rate, and active campaigns",
        "input_schema": GetDashboardStatsInput
    },
    "get_usage_info": {
        "function": get_usage_info,
        "description": "Get plan usage - minutes used, remaining, subscription status",
        "input_schema": None
    },
    "get_leads": {
        "function": get_leads,
        "description": "Get leads list with optional filters by campaign or status",
        "input_schema": GetLeadsInput
    },
    "get_campaigns": {
        "function": get_campaigns,
        "description": "Get all campaigns with their status and progress",
        "input_schema": None
    },
    "get_recent_calls": {
        "function": get_recent_calls,
        "description": "Get recent calls with outcomes and durations",
        "input_schema": None
    },
    "get_actions_today": {
        "function": get_actions_today,
        "description": "Get assistant actions performed today (emails sent, SMS sent, etc.)",
        "input_schema": None
    }
}

ACTION_TOOLS = {
    "send_email": {
        "function": send_email,
        "description": "Send an email to recipients. Supports templates (meeting_confirmation, follow_up, reminder) and HTML. Uses Gmail if connected, SMTP fallback otherwise.",
        "input_schema": SendEmailInput
    },
    "send_sms": {
        "function": send_sms,
        "description": "Send an SMS to specified phone numbers",
        "input_schema": SendSMSInput
    },
    "initiate_call": {
        "function": initiate_call,
        "description": "Start an outbound call to a phone number",
        "input_schema": InitiateCallInput
    },
    "start_campaign": {
        "function": start_campaign,
        "description": "Start or resume a campaign",
        "input_schema": StartCampaignInput
    },
    # Meeting tools
    "check_availability": {
        "function": check_availability,
        "description": "Check available meeting slots for a date. Requires connected calendar.",
        "input_schema": CheckAvailabilityInput
    },
    "book_meeting": {
        "function": book_meeting,
        "description": "Book a meeting with optional video conference (Google Meet/Teams). Requires connected calendar.",
        "input_schema": BookMeetingInput
    },
    "update_meeting": {
        "function": update_meeting_tool,
        "description": "Update/reschedule an existing meeting",
        "input_schema": UpdateMeetingInput
    },
    "cancel_meeting": {
        "function": cancel_meeting_tool,
        "description": "Cancel a scheduled meeting",
        "input_schema": CancelMeetingInput
    },
    # Day 28: Workflow orchestration tools
    "schedule_reminder": {
        "function": schedule_reminder,
        "description": "Schedule a reminder for a meeting or lead. Use offset like '-1h' for relative time or scheduled_at for absolute time.",
        "input_schema": ScheduleReminderInput
    },
    "execute_action_plan": {
        "function": execute_action_plan,
        "description": "Execute a multi-step action plan for complex workflows like 'book meeting + send confirmation + schedule reminder'.",
        "input_schema": ExecuteActionPlanInput
    }
}

ALL_TOOLS = {**QUERY_TOOLS, **ACTION_TOOLS}


