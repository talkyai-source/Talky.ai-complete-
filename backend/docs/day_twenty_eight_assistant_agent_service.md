# Day 28: AssistantAgentService - Multi-Step Workflow Orchestration

> **Date**: January 12, 2026  
> **Focus**: Event-driven assistant agent with multi-step workflow execution  
> **Status**: Implementation Complete ✅

---

## Overview

Today we implemented the **AssistantAgentService** - an orchestration layer that transforms user intent + context into executable multi-step action plans. Unlike single-tool LangGraph calls, this enables complex workflows like:

```
Book meeting → Send confirmation email → Schedule reminder
```

### Key Features

- ✅ **Multi-Step Workflows** - Chain multiple actions with result passing
- ✅ **Hard Allowlist** - Only 10 pre-approved action types permitted
- ✅ **Result Chaining** - Pass outputs from one step to inputs of next
- ✅ **Conditional Execution** - Skip steps based on previous results
- ✅ **Time Offset Parsing** - Schedule reminders with "-1h", "-30m" syntax
- ✅ **Full Audit Trail** - All plans logged to `action_plans` table
- ✅ **Safety First** - No free-form code execution

---

## Architecture

### Directory Structure

```
backend/app/
├── domain/models/
│   └── action_plan.py              # ActionPlan, ActionStep, AllowedActionType (NEW)
│
├── services/
│   └── assistant_agent_service.py  # Core orchestration service (NEW)
│
├── infrastructure/assistant/
│   └── tools.py                    # +schedule_reminder, +execute_action_plan (UPDATED)
│
└── database/migrations/
    └── add_action_plans.sql        # action_plans table (NEW)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MULTI-STEP WORKFLOW FLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Voice Agent  │     │  Assistant   │     │  Dashboard   │
│ Call Outcome │     │  Chat Tool   │     │   REST API   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
              ┌───────────────────────────┐
              │   execute_action_plan()    │
              │   (LangGraph Tool)         │
              └─────────────┬─────────────┘
                            ▼
              ┌───────────────────────────┐
              │  AssistantAgentService    │
              │  ────────────────────────  │
              │  1. Validate against       │
              │     ALLOWLIST              │
              │  2. Create ActionPlan      │
              │  3. Execute steps          │
              │  4. Chain results          │
              │  5. Log to DB              │
              └─────────────┬─────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
 ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
 │ book_meeting│     │ send_email  │     │ schedule_   │
 │ (tool)      │────►│ (tool)      │────►│ reminder    │
 └─────────────┘     └─────────────┘     └─────────────┘
       │                   ▲                   ▲
       │                   │                   │
       └───── Result ──────┴───── Chained ─────┘
              Chaining
```

---

## Allowed Action Types

| Action Type | Description | Category |
|-------------|-------------|----------|
| `book_meeting` | Book calendar event with video link | Calendar |
| `update_meeting` | Reschedule existing meeting | Calendar |
| `cancel_meeting` | Cancel scheduled meeting | Calendar |
| `check_availability` | Check open slots | Calendar |
| `send_email` | Send email via Gmail/SMTP | Communication |
| `send_sms` | Send SMS via Vonage | Communication |
| `schedule_reminder` | Schedule a reminder | Reminders |
| `initiate_call` | Start outbound call | Calling |
| `start_campaign` | Start/resume campaign | Campaign |

> **Security**: Only these 10 action types are permitted. Any other type will raise `ActionNotAllowedError`.

---

## Usage Examples

### Example 1: Book Meeting + Send Confirmation + Schedule Reminder

```python
from app.services.assistant_agent_service import get_assistant_agent_service

service = get_assistant_agent_service(supabase)

plan = await service.create_plan(
    tenant_id="tenant-uuid",
    intent="Book demo meeting and notify lead",
    context={"lead_id": "abc123"},
    actions=[
        {
            "type": "book_meeting",
            "title": "Product Demo",
            "time": "2026-01-13T15:00:00",
            "attendees": ["john@example.com"]
        },
        {
            "type": "send_email",
            "template": "meeting_confirmation",
            "use_result_from": 0  # Uses join_link from meeting
        },
        {
            "type": "schedule_reminder",
            "offset": "-1h",  # 1 hour before meeting
            "use_result_from": 0  # Uses meeting_id, start_time
        }
    ]
)

result = await service.execute_plan(plan)
print(result.status)  # "completed"
print(result.successful_steps)  # 3
```

### Example 2: Conditional Execution

```python
plan = await service.create_plan(
    tenant_id="tenant-uuid",
    intent="Book meeting, send SMS only if successful",
    context={"lead_id": "abc123"},
    actions=[
        {"type": "book_meeting", "title": "Demo", "time": "..."},
        {
            "type": "send_sms",
            "to": ["+15551234567"],
            "message": "Your meeting is confirmed!",
            "condition": "if_previous_success"  # Only if booking succeeds
        }
    ]
)
```

### Example 3: Via LangGraph Tool

The AI assistant can trigger this via the `execute_action_plan` tool:

```
User: "Book a meeting tomorrow at 3pm, send a confirmation email, and remind me 1 hour before"

Assistant uses: execute_action_plan(
    intent="Book meeting and notify",
    actions=[
        {"type": "book_meeting", "title": "Meeting", "time": "2026-01-13T15:00:00"},
        {"type": "send_email", "template": "meeting_confirmation", "use_result_from": 0},
        {"type": "schedule_reminder", "offset": "-1h", "use_result_from": 0}
    ]
)
```

---

## API Reference

### AssistantAgentService Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `create_plan()` | Create and validate action plan | `ActionPlan` |
| `execute_plan()` | Execute all steps in order | Updated `ActionPlan` |
| `get_plan()` | Retrieve plan by ID | `ActionPlan` or `None` |
| `list_plans()` | List plans for tenant | `List[Dict]` |

### New Tools Added

| Tool | Description |
|------|-------------|
| `schedule_reminder` | Schedule a reminder with offset or absolute time |
| `execute_action_plan` | Execute multi-step workflow |

---

## Database Schema

### New Table: `action_plans`

```sql
CREATE TABLE action_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    conversation_id UUID,
    user_id UUID REFERENCES user_profiles(id),
    
    -- Intent and context
    intent TEXT NOT NULL,
    context JSONB DEFAULT '{}',
    
    -- Actions (validated against allowlist)
    actions JSONB NOT NULL DEFAULT '[]',
    
    -- Execution state
    status VARCHAR(50) DEFAULT 'pending',
    current_step INTEGER DEFAULT 0,
    step_results JSONB DEFAULT '[]',
    error TEXT,
    
    -- Timing
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Run Migration

```bash
psql $DATABASE_URL -f backend/database/migrations/add_action_plans.sql
```

---

## Files Created/Modified

### New Files

| File | Lines | Description |
|------|-------|-------------|
| `app/domain/models/action_plan.py` | 160 | ActionPlan, ActionStep, AllowedActionType |
| `app/services/assistant_agent_service.py` | 480 | Core orchestration service |
| `database/migrations/add_action_plans.sql` | 95 | Database migration |
| `tests/unit/test_assistant_agent_service.py` | 268 | Unit tests |

### Modified Files

| File | Changes |
|------|---------|
| `app/infrastructure/assistant/tools.py` | +130 lines: schedule_reminder, execute_action_plan |
| `app/domain/models/__init__.py` | +ActionPlan exports |

---

## Test Results ✅

```
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_create_valid_action_plan PASSED
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_allowlist_validation PASSED
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_result_reference_validation PASSED
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_allowed_action_types PASSED
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_conditional_execution_values PASSED
tests/unit/test_assistant_agent_service.py::TestActionPlanModel::test_action_plan_properties PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_create_plan_valid PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_create_plan_invalid_action_type PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_evaluate_condition_always PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_evaluate_condition_if_previous_success PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_apply_offset_hours PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_apply_offset_minutes PASSED
tests/unit/test_assistant_agent_service.py::TestAssistantAgentService::test_apply_offset_days PASSED
tests/unit/test_assistant_agent_service.py::TestToolIntegration::test_schedule_reminder_import PASSED
tests/unit/test_assistant_agent_service.py::TestToolIntegration::test_execute_action_plan_import PASSED
tests/unit/test_assistant_agent_service.py::TestToolIntegration::test_all_tools_includes_new_tools PASSED
tests/unit/test_assistant_agent_service.py::TestToolIntegration::test_tool_count PASSED
tests/unit/test_assistant_agent_service.py::TestSingletonPattern::test_get_assistant_agent_service_singleton PASSED

================================= 18 passed in 1.50s =================================
```

### Import Verification

```bash
cd backend

# All imports successful
python -c "from app.domain.models.action_plan import ActionPlan, AllowedActionType; print('ActionPlan model: OK')"
python -c "from app.services.assistant_agent_service import AssistantAgentService; print('AssistantAgentService: OK')"
python -c "from app.infrastructure.assistant.tools import schedule_reminder, execute_action_plan, ALL_TOOLS; print(f'New tools: OK ({len(ALL_TOOLS)} total)')"
```

---

## Security Considerations

| Concern | Implementation |
|---------|----------------|
| **Hard Allowlist** | Only 10 action types permitted, validated at Pydantic model level |
| **No Free-Form Execution** | All actions route through existing vetted tools |
| **Tenant Isolation** | All queries include tenant_id, enforced via RLS |
| **Input Validation** | Pydantic models validate all parameters |
| **Audit Trail** | Every plan and step logged to action_plans table |
| **Token Security** | Uses existing encrypted connector tokens |

---

## Next Steps

- [ ] Run database migration on Supabase
- [ ] Add REST API endpoints for action plan management
- [ ] Integrate with voice agent call outcomes
- [ ] Add rollback capability for failed steps (where possible)
- [ ] Add plan resumption for partially completed workflows
- [ ] Frontend UI for viewing action plan history

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Service** | AssistantAgentService in app/services/ |
| **Domain Model** | ActionPlan, ActionStep, AllowedActionType |
| **Database** | action_plans table with RLS |
| **New Tools** | schedule_reminder, execute_action_plan |
| **Allowed Actions** | 10 types in hard allowlist |
| **Tests** | 18 unit tests passing |
| **Result Chaining** | use_result_from index reference |
| **Conditional** | if_previous_success, if_previous_failed |
