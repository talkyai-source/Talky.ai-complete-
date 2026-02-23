# Day 29: Voice Intent Detection & Assistant Actions

> **Date**: January 13, 2026  
> **Focus**: Post-call transcript analysis to detect actionable intents and trigger assistant workflows  
> **Status**: Implementation Complete ✅

---

## Overview

Today we implemented the **PostCallAnalyzer** - a service that analyzes call transcripts **after the call ends** to detect actionable intents and automatically trigger assistant workflows via the AssistantAgentService (Day 28).

### Key Design Decision: Zero Latency

Instead of real-time intent detection (which would add latency to the voice pipeline), we analyze the **saved transcript** in the existing `_save_call_data()` background task. This means:

- ✅ **Zero impact** on call latency
- ✅ **Reliable analysis** on complete transcript
- ✅ **Seamless integration** with existing code path

### Key Features

- ✅ **Post-Call Analysis** - Runs after call ends, no latency impact
- ✅ **Pattern-Based Detection** - Regex patterns with confidence scoring
- ✅ **API Availability Check** - Verifies tenant has required connectors
- ✅ **Permission Check** - Only executes if `auto_actions_enabled = true`
- ✅ **Recommendation System** - Stores suggestions when actions can't execute
- ✅ **4 Actionable Intents** - Booking, follow-up, reminder, callback
- ✅ **AssistantAgentService Integration** - Triggers multi-step workflows

---

## Architecture

### Directory Structure

```
backend/app/
├── domain/models/
│   └── voice_intent.py              # VoiceActionableIntent, DetectedIntent (NEW)
│
├── domain/services/
│   └── post_call_analyzer.py        # Core analysis service (NEW)
│
├── api/v1/endpoints/
│   └── websockets.py                # +Intent analysis in _save_call_data (MODIFIED)
│
└── database/migrations/
    └── add_voice_intent_actions.sql # New columns for calls table (NEW)

tests/unit/
└── test_post_call_analyzer.py       # 36 unit tests (NEW)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     POST-CALL INTENT ANALYSIS FLOW                          │
└─────────────────────────────────────────────────────────────────────────────┘

DURING CALL (unchanged, zero latency):
┌─────────────────────────────────────────────────────────────────────────────┐
│  Audio → DeepgramFlux → GroqLLM → Cartesia TTS → Audio                      │
│                      (normal voice pipeline)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ call ends, WebSocket closes
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      _save_call_data() Background Task                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. Save recording to storage (existing)                                    │
│  2. Save transcript to database (existing)                                  │
│  3. Update call record with status/duration (existing)                      │
│  4. ┌────────────────────────────────────────┐                              │
│     │  PostCallAnalyzer.analyze_call() NEW   │                              │
│     │  ────────────────────────────────────  │                              │
│     │  • Detect intent from transcript       │                              │
│     │  • Check API availability              │                              │
│     │  • Check auto_actions_enabled          │                              │
│     │  • Execute or store recommendation     │                              │
│     └───────────────────┬────────────────────┘                              │
└─────────────────────────┼────────────────────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
  ┌───────────────────┐        ┌───────────────────┐
  │ READY             │        │ NOT READY         │
  │ APIs + Permission │        │ Missing API or    │
  │                   │        │ No Permission     │
  └─────────┬─────────┘        └─────────┬─────────┘
            ▼                            ▼
  ┌───────────────────┐        ┌───────────────────┐
  │ Execute via       │        │ Store             │
  │ AssistantAgent    │        │ Recommendation    │
  │ Service (Day 28)  │        │ for next visit    │
  └───────────────────┘        └───────────────────┘
```

---

## Actionable Intent Types

| Intent | Example Phrases | Required API | Triggered Actions |
|--------|-----------------|--------------|-------------------|
| `booking_request` | "schedule a meeting", "book a call", "tomorrow at 2pm" | Calendar | book_meeting → send_email → schedule_reminder |
| `follow_up_request` | "send me information", "email the details", "pricing docs" | Email | send_email |
| `reminder_request` | "remind me", "don't let me forget", "notify me" | None | schedule_reminder |
| `callback_later` | "call me back", "busy now", "try tomorrow" | None | schedule_reminder |

> **Detection Threshold**: Only intents with confidence ≥ 0.7 are considered actionable.

---

## Usage Examples

### Example 1: Full Flow - APIs Ready + Permission Enabled

```python
# This happens automatically in _save_call_data() after call ends

# Transcript from call:
transcript = """
User: Yes, let's schedule a demo for tomorrow at 3pm
Assistant: Great! I'll get that set up for you.
"""

# PostCallAnalyzer detects:
# - Intent: BOOKING_REQUEST
# - Confidence: 0.9
# - Extracted: {"time_reference": "3pm"}

# Checks pass:
# - Calendar connector: ✓ Active
# - auto_actions_enabled: ✓ True

# Triggers AssistantAgentService with:
actions = [
    {"type": "book_meeting", "parameters": {"title": "Follow-up Call", ...}},
    {"type": "send_email", "parameters": {"template_name": "meeting_confirmation"}, "use_result_from": 0},
    {"type": "schedule_reminder", "parameters": {"offset": "-1h"}, "use_result_from": 0}
]

# Results stored in calls.action_results
```

### Example 2: Missing Calendar Connector

```python
# Transcript from call:
transcript = """
User: I'd like to book an appointment next week
Assistant: That sounds great!
"""

# PostCallAnalyzer detects:
# - Intent: BOOKING_REQUEST
# - Confidence: 0.85

# Check fails:
# - Calendar connector: ✗ Not connected

# Stores recommendation in calls.pending_recommendations:
"The caller wanted to book a meeting. Connect your calendar in Settings > Integrations 
to enable automatic booking."

# Next time user logs in, they see this recommendation
```

### Example 3: Manual Integration

```python
from app.domain.services.post_call_analyzer import get_post_call_analyzer

# Get analyzer instance
analyzer = get_post_call_analyzer(supabase)

# Analyze a transcript
result = await analyzer.analyze_call(
    call_id="call-uuid-123",
    tenant_id="tenant-uuid-456",
    transcript_text="User: Can you send me the pricing information?",
    lead_id="lead-uuid-789"
)

if result:
    print(f"Intent: {result.intent}")           # follow_up_request
    print(f"Confidence: {result.confidence}")   # 0.9
    print(f"Readiness: {result.readiness}")     # ready | missing_api | needs_permission
    print(f"Recommendation: {result.recommendation_message}")
```

---

## API Reference

### PostCallAnalyzer Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `analyze_call()` | Analyze transcript for actionable intents | `DetectedIntent` or `None` |
| `_detect_intent()` | Pattern-based intent detection | `(intent, confidence, extracted_data)` |
| `_check_api_available()` | Check if required connector is active | `bool` |
| `_check_auto_action_permission()` | Check if tenant enabled auto-actions | `bool` |
| `_build_recommendation_message()` | Build user-facing recommendation | `str` |
| `_build_action_plan()` | Build action steps for intent | `List[Dict]` |
| `_execute_action()` | Trigger AssistantAgentService | `None` |

### Domain Models

| Model | Description |
|-------|-------------|
| `VoiceActionableIntent` | Enum of 5 intent types (booking, follow-up, reminder, callback, none) |
| `ActionReadiness` | Enum of 4 readiness states (ready, missing_api, needs_permission, not_applicable) |
| `DetectedIntent` | Full intent detection result with confidence, extracted data, and recommendation |
| `CallRecommendation` | Structured recommendation for storage |

---

## Database Schema

### New Columns on `calls` Table

```sql
ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS detected_intents JSONB DEFAULT '[]',
ADD COLUMN IF NOT EXISTS action_plan_id UUID REFERENCES action_plans(id),
ADD COLUMN IF NOT EXISTS action_results JSONB DEFAULT '{}',
ADD COLUMN IF NOT EXISTS pending_recommendations TEXT;
```

| Column | Type | Description |
|--------|------|-------------|
| `detected_intents` | JSONB | Array of DetectedIntent objects |
| `action_plan_id` | UUID | Reference to executed action_plans record |
| `action_results` | JSONB | Execution results (status, steps, timestamps) |
| `pending_recommendations` | TEXT | User-facing recommendation message |

### New Column on `tenant_settings` Table

```sql
ALTER TABLE tenant_settings 
ADD COLUMN IF NOT EXISTS auto_actions_enabled BOOLEAN DEFAULT FALSE;
```

| Column | Type | Description |
|--------|------|-------------|
| `auto_actions_enabled` | BOOLEAN | Allow automatic execution of detected intents |

### Run Migration

```bash
psql $DATABASE_URL -f backend/database/migrations/add_voice_intent_actions.sql
```

---

## Intent Detection Patterns

### Booking Patterns

```python
BOOKING_PATTERNS = [
    # Explicit booking language (0.9 confidence)
    r'\b(schedule|book|set up|arrange)\b.*\b(meeting|call|appointment|demo)\b',
    
    # Time-based confirmation (0.85 confidence)
    r'\b(tomorrow|next week|today|monday|tuesday|...)\b.*\b(at|around)\s+\d',
    
    # General confirmation (0.75 confidence)
    r'\b(sounds good|yes.*that time|confirm|works for me|let\'?s do it)\b',
    
    # Demo/meeting interest (0.7 confidence)
    r'\b(let\'?s|can we|could we)\b.*\b(meet|talk|call|demo)\b',
]
```

### Follow-up Patterns

```python
FOLLOW_UP_PATTERNS = [
    # Explicit follow-up request (0.9 confidence)
    r'\b(send|email)\b.*\b(information|details|follow.?up|summary)\b',
    
    # Request for sending (0.85 confidence)
    r'\b(can you|could you)\b.*\b(email|send)\b',
    
    # Information request (0.7 confidence)
    r'\b(more info|brochure|pricing|documentation|details)\b',
]
```

### Data Extraction

When booking intent is detected, time references are extracted:

```python
# Extract time references
time_patterns = [
    r'(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))',  # "2pm", "10:30 AM"
    r'(tomorrow|today|next\s+\w+day|monday|...)',
    r'(morning|afternoon|evening)',
    r'(in\s+\d+\s+(?:hour|minute|day)s?)',
]
```

---

## Files Created/Modified

### New Files

| File | Lines | Description |
|------|-------|-------------|
| `app/domain/models/voice_intent.py` | 89 | VoiceActionableIntent, ActionReadiness, DetectedIntent |
| `app/domain/services/post_call_analyzer.py` | 475 | Core post-call analysis service |
| `database/migrations/add_voice_intent_actions.sql` | 58 | Migration for calls and tenant_settings |
| `tests/unit/test_post_call_analyzer.py` | 410 | 36 unit tests |
| `docs/day_twenty_nine_voice_intent_actions.md` | - | This documentation |

### Modified Files

| File | Changes |
|------|---------|
| `app/api/v1/endpoints/websockets.py` | +24 lines: PostCallAnalyzer call in _save_call_data() |

---

## Test Results ✅

```
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_booking_explicit PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_booking_with_time PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_booking_confirmation PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_booking_demo_request PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_booking_appointment PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_followup_explicit PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_followup_email PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_followup_pricing PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_reminder_explicit PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_reminder_implicit PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_callback_explicit PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_callback_busy PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_callback_not_good_time PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_none_generic PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_none_question PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_none_empty PASSED
tests/unit/test_post_call_analyzer.py::TestIntentDetection::test_detect_none_objection PASSED
tests/unit/test_post_call_analyzer.py::TestDataExtraction::test_extract_time_specific PASSED
tests/unit/test_post_call_analyzer.py::TestDataExtraction::test_extract_time_relative PASSED
tests/unit/test_post_call_analyzer.py::TestDataExtraction::test_extract_callback_time PASSED
tests/unit/test_post_call_analyzer.py::TestRecommendationMessages::test_recommendation_missing_calendar PASSED
tests/unit/test_post_call_analyzer.py::TestRecommendationMessages::test_recommendation_missing_email PASSED
tests/unit/test_post_call_analyzer.py::TestRecommendationMessages::test_recommendation_needs_permission PASSED
tests/unit/test_post_call_analyzer.py::TestActionPlanBuilding::test_booking_plan_has_three_steps PASSED
tests/unit/test_post_call_analyzer.py::TestActionPlanBuilding::test_followup_plan_has_email PASSED
tests/unit/test_post_call_analyzer.py::TestActionPlanBuilding::test_callback_plan_has_reminder PASSED
tests/unit/test_post_call_analyzer.py::TestAPIAvailability::test_check_api_no_requirement PASSED
tests/unit/test_post_call_analyzer.py::TestAPIAvailability::test_check_api_connector_active PASSED
tests/unit/test_post_call_analyzer.py::TestAPIAvailability::test_check_api_connector_missing PASSED
tests/unit/test_post_call_analyzer.py::TestPermissionChecking::test_permission_enabled PASSED
tests/unit/test_post_call_analyzer.py::TestPermissionChecking::test_permission_disabled PASSED
tests/unit/test_post_call_analyzer.py::TestPermissionChecking::test_permission_no_settings PASSED
tests/unit/test_post_call_analyzer.py::TestFullAnalysisFlow::test_analyze_booking_ready PASSED
tests/unit/test_post_call_analyzer.py::TestFullAnalysisFlow::test_analyze_booking_missing_api PASSED
tests/unit/test_post_call_analyzer.py::TestFullAnalysisFlow::test_analyze_no_intent PASSED
tests/unit/test_post_call_analyzer.py::TestFullAnalysisFlow::test_analyze_empty_transcript PASSED

================================= 36 passed in 2.1s =================================
```

### Syntax Verification

```bash
cd backend

# All files pass Python syntax check
python -m py_compile app/domain/models/voice_intent.py          # ✓
python -m py_compile app/domain/services/post_call_analyzer.py  # ✓
python -m py_compile tests/unit/test_post_call_analyzer.py      # ✓
echo "Syntax OK"
```

---

## Security Considerations

| Concern | Implementation |
|---------|----------------|
| **Permission Control** | Actions only execute if `auto_actions_enabled = true` |
| **API Availability** | Verifies connector exists and is active before action |
| **Uses Existing Security** | Leverages Day 28 `AssistantAgentService` allowlist (10 action types only) |
| **Tenant Isolation** | All queries include tenant_id |
| **Non-Critical Path** | Analysis failures are logged but don't affect call data saving |
| **Audit Trail** | All detected intents and results stored in calls table |

---

## Integration Points

### 1. websockets.py - _save_call_data()

```python
# Location: app/api/v1/endpoints/websockets.py :: _save_call_data()
# Added after: "Call data saved successfully" log

if full_transcript:
    try:
        from app.domain.services.post_call_analyzer import get_post_call_analyzer
        
        analyzer = get_post_call_analyzer(supabase)
        detected_intent = await analyzer.analyze_call(
            call_id=call_id,
            tenant_id=tenant_id,
            transcript_text=full_transcript,
            lead_id=session.lead_id
        )
        
        if detected_intent:
            logger.info(f"Post-call analysis complete: {detected_intent.intent}")
    except Exception as e:
        logger.warning(f"Post-call analysis failed: {e}")
```

### 2. AssistantAgentService (Day 28)

When readiness is `READY`, the analyzer calls:

```python
from app.services.assistant_agent_service import get_assistant_agent_service

agent_service = get_assistant_agent_service(supabase)
plan = await agent_service.create_plan(
    tenant_id=tenant_id,
    intent=f"Post-call action: {detected.intent}",
    context={"call_id": call_id, "source": "post_call_analysis"},
    actions=detected.action_plan
)
result = await agent_service.execute_plan(plan)
```

---

## Next Steps

- [ ] Run database migration on Supabase
- [ ] Enable `auto_actions_enabled` for test tenant
- [ ] Test with live calls
- [ ] Add REST API endpoint for viewing call recommendations
- [ ] Frontend UI to display pending recommendations
- [ ] Add LLM-enhanced detection for edge cases

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Service** | PostCallAnalyzer in app/domain/services/ |
| **Domain Models** | VoiceActionableIntent, ActionReadiness, DetectedIntent |
| **Database** | detected_intents, action_plan_id, action_results, pending_recommendations |
| **Integration** | _save_call_data() in websockets.py |
| **Latency Impact** | Zero - runs as background task after call ends |
| **Execution Condition** | APIs available + auto_actions_enabled |
| **Fallback** | Store recommendation for next user visit |
| **Tests** | 36 unit tests passing |
