# Day 27: Timed Communication System

> **Date**: January 9, 2026  
> **Focus**: SMS-based meeting reminders with email fallback  
> **Status**: Implementation Complete ✅

---

## Overview

Today we implemented an automated reminder system that sends SMS reminders to leads before their scheduled meetings. The system uses Vonage SMS as the primary channel with email fallback when phone number is unavailable.

### Key Features

- ✅ **SMS Provider (Vonage)** - Primary SMS delivery via Vonage SMS API v4.x
- ✅ **ReminderWorker** - Background worker for processing scheduled reminders
- ✅ **Auto-Reminder Creation** - T-24h, T-1h, T-10m reminders created when meeting is booked
- ✅ **Fallback Logic** - SMS if phone available, Email otherwise
- ✅ **Idempotency** - Prevents duplicate sends on retries
- ✅ **Retry with Backoff** - Exponential backoff for failed sends (max 3 retries)
- ✅ **Audit Trail** - All sends logged in `assistant_actions` table
- ✅ **Multi-Tenant** - Strict tenant isolation

---

## Test Results ✅

### Import Verification

All core modules load without errors:

```
✅ SMS Connectors: OK
✅ SMS Service: OK
✅ SMS Templates: OK
✅ Reminder Worker: OK
✅ Meeting Service: OK
```

### Unit Tests Passed

```
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_get_template_returns_template PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_get_template_raises_for_unknown PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_render_meeting_reminder_24h PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_render_meeting_reminder_1h PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_render_meeting_reminder_10m PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_render_meeting_reminder_10m_without_link PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_list_templates PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_get_template_info PASSED
tests/unit/test_sms_service.py::TestSMSTemplateManager::test_singleton_returns_same_instance PASSED
```

---

## Vonage SMS Setup

### Step 1: Create Vonage Account

1. Go to [Vonage Dashboard](https://dashboard.nexmo.com/)
2. Sign up or log in
3. Note your **API Key** and **API Secret** from the dashboard

### Step 2: Get an SMS-Capable Number

1. Navigate to **Numbers > Buy Numbers**
2. Select a number with SMS capability
3. Purchase and note the number (E.164 format: +1234567890)

### Step 3: Configure Environment Variables

Add to your `.env` file:

```bash
# Vonage SMS (Required for SMS reminders)
VONAGE_API_KEY=your-api-key
VONAGE_API_SECRET=your-api-secret
VONAGE_FROM_NUMBER=your-sms-capable-number  # E.164 format

# Already existing (for voice calls)
# VONAGE_APP_ID=your-app-id
# VONAGE_PRIVATE_KEY_PATH=./config/private.key
```

---

## Architecture

### Directory Structure

```
backend/app/
├── services/
│   ├── sms_service.py             # SMS orchestration (NEW)
│   ├── email_service.py           # Email fallback (existing)
│   └── meeting_service.py         # Creates reminders on booking (UPDATED)
│
├── domain/
│   ├── models/meeting.py          # Reminder model (UPDATED)
│   └── services/
│       └── sms_template_manager.py  # SMS templates (NEW)
│
├── infrastructure/connectors/sms/
│   ├── __init__.py                # Package exports (NEW)
│   ├── base.py                    # SMSProvider ABC (NEW)
│   └── vonage_sms.py              # Vonage implementation (NEW)
│
├── workers/
│   ├── __init__.py                # Worker exports (UPDATED)
│   ├── reminder_worker.py         # Background processor (NEW)
│   └── dialer_worker.py           # (existing - pattern reference)
│
└── database/migrations/
    └── add_reminder_idempotency.sql  # DB migration (NEW)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REMINDER SENDING FLOW                                  │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Voice Agent  │     │  Assistant   │     │  Dashboard   │
│ Books Meeting│     │  Chat Tool   │     │   REST API   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                 ┌─────────────────────┐
                 │   MeetingService    │
                 │  ─────────────────  │
                 │ create_meeting()    │
                 │ _create_meeting_    │
                 │ reminders()         │
                 └──────────┬──────────┘
                            │
                Creates 3 reminders:
                • T-24h before meeting
                • T-1h before meeting
                • T-10m before meeting
                            │
                            ▼
                 ┌─────────────────────┐
                 │  reminders table    │
                 │  (status: pending)  │
                 └──────────┬──────────┘
                            │
                   ┌────────┴────────┐
                   ▼                 │
         ┌─────────────────┐         │
         │  ReminderWorker │◄────────┘
         │  (polls every   │    Scans DB
         │   30 seconds)   │
         └────────┬────────┘
                  │
    ┌─────────────┴─────────────┐
    ▼                           ▼
Lead has phone?            Lead has email?
    │                           │
    ▼                           ▼
┌────────────┐          ┌────────────┐
│ SMSService │          │EmailService│
│ (Vonage)   │          │ (Fallback) │
└─────┬──────┘          └─────┬──────┘
      │                       │
      └───────────┬───────────┘
                  ▼
         ┌───────────────┐
         │ Update status │
         │ → 'sent'      │
         └───────────────┘
```

---

## SMS Templates

| Template | Use Case |
|----------|----------|
| `meeting_reminder_24h` | 24 hours before meeting |
| `meeting_reminder_1h` | 1 hour before meeting |
| `meeting_reminder_10m` | 10 minutes before meeting |

### Template Content

```
24h: "Hi {name}, reminder: You have "{title}" scheduled for tomorrow at {time}. Reply CONFIRM to confirm."

1h:  "Hi {name}, your meeting "{title}" starts in 1 hour at {time}."

10m: "Hi {name}, "{title}" starts in 10 min! Join: {join_link}"
```

### Usage Example

```python
from app.domain.services.sms_template_manager import get_sms_template_manager

mgr = get_sms_template_manager()

# Render a reminder message
message = mgr.render_meeting_reminder(
    reminder_type="1h",
    name="John",
    title="Product Demo",
    time="2:00 PM",
    join_link="https://meet.google.com/abc-xyz"
)

print(message)
# "Hi John, your meeting "Product Demo" starts in 1 hour at 2:00 PM."
```

---

## Running the Reminder Worker

### As Standalone Process

```bash
cd c:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\backend

# Run the worker
python -m app.workers.reminder_worker
```

### Expected Output

```
2026-01-09 18:00:00 - ReminderWorker - INFO - Initializing Reminder Worker...
2026-01-09 18:00:00 - ReminderWorker - INFO - Reminder Worker initialized successfully
2026-01-09 18:00:00 - ReminderWorker - INFO - Reminder Worker started - scanning for due reminders
2026-01-09 18:00:30 - ReminderWorker - INFO - Found 2 due reminders
2026-01-09 18:00:30 - ReminderWorker - INFO - Processing reminder abc123: 1h for Product Demo
2026-01-09 18:00:31 - SMSService - INFO - SMS sent successfully: msg-789
2026-01-09 18:00:31 - ReminderWorker - INFO - Reminder abc123 sent successfully via sms
```

### Worker Configuration

| Setting | Value | Description |
|---------|-------|-------------|
| `POLL_INTERVAL` | 30s | Time between database scans |
| `BATCH_SIZE` | 50 | Max reminders per scan |
| `MAX_RETRIES` | 3 | Retry attempts before marking failed |
| `INITIAL_RETRY_DELAY` | 60s | First retry delay |
| `RETRY_BACKOFF_MULTIPLIER` | 2 | Exponential backoff |

---

## Database Migration

Run this SQL in your Supabase SQL editor:

```sql
-- Add idempotency_key column
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_idempotency_key 
ON reminders(idempotency_key) 
WHERE idempotency_key IS NOT NULL;

-- Add retry tracking columns
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3,
ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS last_error TEXT,
ADD COLUMN IF NOT EXISTS channel VARCHAR(20),
ADD COLUMN IF NOT EXISTS external_message_id VARCHAR(255);

-- Add performance indexes
CREATE INDEX IF NOT EXISTS idx_reminders_pending_due
ON reminders(scheduled_at) WHERE status = 'pending';
```

### New Columns Added

| Column | Type | Description |
|--------|------|-------------|
| `idempotency_key` | VARCHAR(255) | Prevents duplicate sends |
| `channel` | VARCHAR(20) | `sms` or `email` |
| `external_message_id` | VARCHAR(255) | Provider message ID |
| `next_retry_at` | TIMESTAMPTZ | Scheduled retry time |
| `max_retries` | INTEGER | Max retry attempts (default 3) |
| `last_error` | TEXT | Error from last attempt |

---

## Testing

### Verify Imports

```bash
# All imports should print "OK"
python -c "from app.infrastructure.connectors.sms import VonageSMSProvider; print('SMS Connectors: OK')"
python -c "from app.services.sms_service import SMSService; print('SMS Service: OK')"
python -c "from app.domain.services.sms_template_manager import SMSTemplateManager; print('SMS Templates: OK')"
python -c "from app.workers.reminder_worker import ReminderWorker; print('Reminder Worker: OK')"
python -c "from app.services.meeting_service import MeetingService; print('Meeting Service: OK')"
```

### Run Unit Tests

```bash
cd backend
python -m pytest tests/unit/test_sms_service.py -v
```

### Manual Test Flow

1. **Book a meeting** via AI Assistant or API
2. **Check database** for 3 new reminder records
3. **Start reminder worker**: `python -m app.workers.reminder_worker`
4. **Wait for reminder to become due** (or update `scheduled_at` in DB)
5. **Verify SMS sent** (check worker logs)

---

## Troubleshooting

### SMS Not Sending

| Issue | Solution |
|-------|----------|
| "VONAGE_FROM_NUMBER not configured" | Set `VONAGE_FROM_NUMBER` in `.env` |
| "Vonage SMS client not initialized" | Check `VONAGE_API_KEY` and `VONAGE_API_SECRET` |
| "No phone number or email available" | Ensure lead has `phone_number` in database |

### Worker Issues

| Issue | Solution |
|-------|----------|
| Worker crashes immediately | Check Supabase connection (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`) |
| Reminders not processing | Verify `status='pending'` and `scheduled_at <= NOW()` |
| Duplicate sends | Check `idempotency_key` is being set |

---

## Next Steps

- [ ] Configure Vonage SMS credentials in production
- [ ] Run database migration on Supabase
- [ ] Test meeting booking → reminders creation
- [ ] Deploy reminder worker as background service (systemd/Docker)
- [ ] Add SMS delivery status webhooks (optional)
- [ ] Add Twilio as fallback provider (future enhancement)
