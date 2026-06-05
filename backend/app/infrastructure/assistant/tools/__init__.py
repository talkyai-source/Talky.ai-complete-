"""
Assistant Agent Tools package.

All public names that were importable from the original tools.py module remain
importable from this package without any change to consumer code.
"""

from app.infrastructure.assistant.tools.dashboard import (
    GetDashboardStatsInput,
    get_dashboard_stats,
    get_usage_info,
    get_actions_today,
)
from app.infrastructure.assistant.tools.leads import (
    GetLeadsInput,
    get_leads,
)
from app.infrastructure.assistant.tools.campaigns import (
    StartCampaignInput,
    get_campaigns,
    start_campaign,
)
from app.infrastructure.assistant.tools.calls import (
    InitiateCallInput,
    get_recent_calls,
    initiate_call,
)
from app.infrastructure.assistant.tools.comms import (
    SendEmailInput,
    SendSMSInput,
    send_email,
    send_sms,
)
from app.infrastructure.assistant.tools.meetings import (
    CheckAvailabilityInput,
    BookMeetingInput,
    UpdateMeetingInput,
    CancelMeetingInput,
    check_availability,
    book_meeting,
    update_meeting_tool,
    cancel_meeting_tool,
)
from app.infrastructure.assistant.tools.workflow import (
    ScheduleReminderInput,
    ExecuteActionPlanInput,
    schedule_reminder,
    execute_action_plan,
)

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

__all__ = [
    # Input models
    "GetDashboardStatsInput",
    "GetLeadsInput",
    "StartCampaignInput",
    "InitiateCallInput",
    "SendEmailInput",
    "SendSMSInput",
    "CheckAvailabilityInput",
    "BookMeetingInput",
    "UpdateMeetingInput",
    "CancelMeetingInput",
    "ScheduleReminderInput",
    "ExecuteActionPlanInput",
    # Query tool functions
    "get_dashboard_stats",
    "get_usage_info",
    "get_leads",
    "get_campaigns",
    "get_recent_calls",
    "get_actions_today",
    # Action tool functions
    "send_email",
    "send_sms",
    "initiate_call",
    "start_campaign",
    "check_availability",
    "book_meeting",
    "update_meeting_tool",
    "cancel_meeting_tool",
    "schedule_reminder",
    "execute_action_plan",
    # Registries
    "QUERY_TOOLS",
    "ACTION_TOOLS",
    "ALL_TOOLS",
]
