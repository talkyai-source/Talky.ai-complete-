# Day 23: Assistant Agent System - Conversational AI Chatbot

**Date:** January 5, 2026  
**Focus:** Event-driven AI assistant with query and action capabilities

---

## Overview

This document details the implementation of the **Assistant Agent System** - a conversational AI chatbot that can answer questions about tenant data and trigger actions (emails, SMS, calls, campaigns) directly from chat.

**Key Distinction:** This is NOT the voice STT/TTS pipeline. This is a separate **task executor** that uses LangGraph + Groq for reasoning and tool execution.

---

## Problem Statement

### Business Need
Users needed a way to:
1. Query their data naturally ("How many calls today?", "Show qualified leads")
2. Trigger actions without navigating complex UIs ("Send SMS to hot leads")
3. Get proactive suggestions and insights
4. All within their tenant scope (multi-tenant isolation)

### Technical Approach
- **LangGraph ReAct Agent** for tool-calling and reasoning
- **Groq** for fast LLM inference (llama-3.3-70b-versatile)
- **WebSocket** for real-time chat
- **Tenant-scoped tools** with RLS enforcement

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CHAT INTERFACE (WebSocket)                       │
│   User: "How many calls did we make today?"                          │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   LANGGRAPH ReAct AGENT (Groq)                       │
│   ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────┐   │
│   │  Router   │→ │  Reasoner │→ │   Tool    │→ │   Responder   │   │
│   │  (Intent) │  │  (Plan)   │  │  Executor │  │   (Natural)   │   │
│   └───────────┘  └───────────┘  └───────────┘  └───────────────┘   │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         ▼                            ▼                            ▼
┌─────────────────┐      ┌─────────────────────┐      ┌───────────────┐
│  QUERY TOOLS    │      │   ACTION TOOLS      │      │  CHAT TOOLS   │
│  - get_stats    │      │   - send_email      │      │  - greet      │
│  - get_leads    │      │   - send_sms        │      │  - help       │
│  - get_calls    │      │   - initiate_call   │      │  - clarify    │
│  - get_campaign │      │   - book_meeting    │      │               │
│  - get_usage    │      │   - start_campaign  │      │               │
└────────┬────────┘      └──────────┬──────────┘      └───────────────┘
         │                          │
         ▼                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    TENANT-SCOPED DATA LAYER                          │
│              (All queries filtered by tenant_id via RLS)             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### New Tables Created

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `connectors` | External integrations | type, provider, status |
| `connector_accounts` | OAuth tokens (encrypted) | access_token_encrypted, refresh_token_encrypted |
| `assistant_conversations` | Chat history | messages (JSONB), context (JSONB) |
| `assistant_actions` | Action audit log | type, status, input_data, output_data |
| `meetings` | Calendar events | start_time, end_time, join_link |
| `reminders` | Scheduled reminders | scheduled_at, type, status |

**Migration File:** `backend/database/migrations/add_assistant_agent.sql`

### RLS Policies

All tables have tenant isolation:
```sql
CREATE POLICY "Users can view in their tenant" ON assistant_actions
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );
```

---

## Files Created

### Infrastructure Layer

| File | Description |
|------|-------------|
| `app/infrastructure/assistant/__init__.py` | Package init |
| `app/infrastructure/assistant/tools.py` | 10 agent tools (6 query + 4 action) |
| `app/infrastructure/assistant/agent.py` | LangGraph ReAct agent with Groq |

### Domain Models

| File | Contents |
|------|----------|
| `app/domain/models/connector.py` | `ConnectorType`, `ConnectorProvider`, `Connector` |
| `app/domain/models/assistant_action.py` | `ActionType`, `ActionStatus`, `AssistantAction` |
| `app/domain/models/assistant_conversation.py` | `ChatMessage`, `ConversationContext` |
| `app/domain/models/meeting.py` | `Meeting`, `Reminder`, `Attendee` |

### API Layer

| File | Description |
|------|-------------|
| `app/api/v1/endpoints/assistant_ws.py` | WebSocket + REST endpoints |

---

## Files Modified

| File | Changes |
|------|---------|
| `app/api/v1/routes.py` | Added `assistant_ws` router |

---

## Agent Tools

### Query Tools (Read-Only)

| Tool | Description | Returns |
|------|-------------|---------|
| `get_dashboard_stats` | Today's stats | calls, success_rate, active_campaigns |
| `get_usage_info` | Plan usage | minutes_used, minutes_remaining, plan_name |
| `get_leads` | Query leads | leads list with filters |
| `get_campaigns` | List campaigns | campaigns with status, progress |
| `get_recent_calls` | Call history | calls with outcomes |
| `get_actions_today` | Today's actions | emails sent, SMS sent, etc. |

### Action Tools (Write)

| Tool | Description | Parameters |
|------|-------------|------------|
| `send_email` | Send email | to, subject, body |
| `send_sms` | Send SMS | to, message |
| `initiate_call` | Start call | phone_number, campaign_id |
| `start_campaign` | Start campaign | campaign_id |

---

## API Endpoints

### WebSocket

```
WS /api/v1/assistant/chat?token=JWT_TOKEN&conversation_id=UUID
```

**Client → Server:**
```json
{"type": "user_message", "content": "How many calls today?"}
```

**Server → Client:**
```json
{"type": "assistant_typing", "content": true}
{"type": "assistant_message", "content": "You made 47 calls today..."}
{"type": "action_triggered", "action": {"type": "send_sms", "success": true}}
```

### REST

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/assistant/conversations` | List conversations |
| GET | `/assistant/conversations/{id}` | Get conversation |
| DELETE | `/assistant/conversations/{id}` | Delete conversation |
| GET | `/assistant/actions` | List actions (audit) |

---

## Example Conversations

```
User: "What's today's status?"
Assistant: "Today you've made 47 calls with a 72% success rate. 
            You have 2 active campaigns running."

User: "How many minutes do I have left?"
Assistant: "You have 1,247 minutes remaining out of 1,500 allocated 
            on your Professional plan (83% used)."

User: "Send an SMS to +15551234567 saying 'Thanks for your interest'"
Assistant: "SMS queued for delivery to +15551234567."

User: "Start the Q1 Outreach campaign"
Assistant: "Campaign 'Q1 Outreach' has been started."
```

---

## Technical Details

### LangGraph State

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tenant_id: str
    user_id: Optional[str]
    conversation_id: Optional[str]
    supabase: Any
    tool_results: List[Dict[str, Any]]
```

### Agent Configuration

| Setting | Value |
|---------|-------|
| LLM Model | llama-3.3-70b-versatile |
| Temperature | 0.7 |
| Max Tokens | 500 |
| Tool Choice | auto |

### System Prompt

The agent is instructed to:
- Use tools to get real data before answering
- Confirm before destructive actions
- Stay within tenant scope
- Be conversational (handle greetings)
- Format numbers nicely

---

## Dependencies

### New Python Packages Required

```txt
langgraph>=0.0.50
```

### Existing Packages Used

- `groq` - LLM inference
- `supabase` - Database access
- `fastapi` - WebSocket handling

---

## Environment Variables

No new environment variables required. Uses existing:

| Variable | Purpose |
|----------|---------|
| `GROQ_API_KEY` | LLM inference |
| `SUPABASE_URL` | Database |
| `SUPABASE_SERVICE_ROLE_KEY` | Database admin |

---

## Next Steps

### Immediate (To Run)

1. **Run Migration:**
   ```bash
   psql $DATABASE_URL -f backend/database/migrations/add_assistant_agent.sql
   ```

2. **Install LangGraph:**
   ```bash
   pip install langgraph
   ```

3. **Add to requirements.txt:**
   ```txt
   langgraph>=0.0.50
   ```

### Future Enhancements

1. **Connector Integrations:**
   - Google Calendar (book_meeting)
   - Gmail (send_email)
   - Twilio (send_sms)

2. **Frontend Chat UI:**
   - Chat widget component
   - Message streaming display
   - Action confirmations

3. **Advanced Features:**
   - Scheduled actions
   - Proactive notifications
   - Analytics insights

---

## Security Considerations

| Concern | Implementation |
|---------|----------------|
| Tenant Isolation | All tools receive tenant_id, enforced via RLS |
| Token Security | OAuth tokens encrypted with Fernet |
| Auth Required | WebSocket requires valid JWT token |
| Action Authorization | Service role used for writes |

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| Agent Framework | LangGraph ReAct |
| LLM Provider | Groq (llama-3.3-70b-versatile) |
| Database | 6 new tables with RLS |
| API | WebSocket + REST |
| Tools | 6 query + 4 action |
| Security | Tenant-scoped, JWT auth |

---

## Update: LangGraph Message Format (January 12, 2026)

### Issue
The agent was returning tool calls in a format incompatible with LangGraph's internal message handling:
- Error: `AIMessage tool_calls.0.args Input should be a valid dictionary`
- This occurred when LangGraph tried to convert dict messages to `AIMessage` objects

### Root Cause
LangGraph expects tool calls in this format:
```python
{"id": "...", "name": "tool_name", "args": {...}}  # args must be a dict
```

The original code was using Groq's format:
```python
{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
```

### Solution
1. **agent.py**: Updated to use `langchain_core.messages` classes:
   - `AIMessage` for assistant responses
   - `ToolMessage` for tool results
2. **tool_executor**: Now handles both `AIMessage` objects and dicts
3. **should_continue**: Updated to check `AIMessage.tool_calls`
4. **Frontend**: Added:
   - Stop button to cancel pending requests
   - Reconnect button when disconnected
   - Connection error banner
   - Auto-reconnection (up to 3 attempts)

### Key Changes
```python
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

# Agent node now returns proper AIMessage
ai_message = AIMessage(
    content=message.content or "",
    tool_calls=[{
        "id": tc.id,
        "name": tc.function.name,
        "args": json.loads(tc.function.arguments) or {}  # Must be dict, never None
    } for tc in message.tool_calls]
)
return {"messages": [ai_message]}

# Tool executor returns ToolMessage
tool_message = ToolMessage(
    content=json.dumps(result),
    tool_call_id=tool_call_id
)
```

### WebSocket Endpoint Rewrite (Same Day)

The WebSocket endpoint was completely rewritten following [FastAPI official documentation](https://fastapi.tiangolo.com/advanced/websockets/).

**Issue:** "WebSocket is not connected. Need to call 'accept' first"

**Root Cause:** The original code authenticated BEFORE calling `websocket.accept()`. If authentication failed, the error handler tried to log/send messages on an unaccepted WebSocket.

**Solution:** Following FastAPI's recommended pattern:
1. Call `await websocket.accept()` FIRST before any operations
2. Wrap message loop in try/except to catch `WebSocketDisconnect`
3. Handle all errors gracefully after connection is accepted

```python
@router.websocket("/chat")
async def assistant_chat(websocket: WebSocket, token: str = Query(...)):
    connection_id = str(uuid.uuid4())
    
    # STEP 1: Accept WebSocket FIRST
    await manager.connect(websocket, connection_id)
    
    try:
        # STEP 2: Authenticate after connection
        # ... auth code ...
        
        # STEP 3: Send connected confirmation
        await manager.send_json(connection_id, {"type": "connected"})
        
        # STEP 4: Message loop
        while True:
            data = await websocket.receive_json()
            # ... process message ...
    
    except WebSocketDisconnect:
        # Normal disconnection
        pass
    
    finally:
        manager.disconnect(connection_id)
```

### WebSocket Graceful Disconnection Handling (January 12, 2026)

**Issue:** When clients closed the WebSocket panel during message processing, the server would log cascading errors:
- `RuntimeError: Cannot call "send" once a close message has been sent.`
- `starlette.websockets.WebSocketDisconnect`

**Root Cause:** The `send_json` method in `ConnectionManager` didn't handle cases where the connection was already closed. When a `WebSocketDisconnect` occurred in an exception handler, subsequent send attempts in the same try/except/finally chain would fail with `RuntimeError`.

**Solution:** Updated `ConnectionManager.send_json` to wrap send calls in try/except:

```python
async def send_json(self, connection_id: str, data: dict) -> bool:
    """
    Send JSON data to a specific connection.
    Returns True if send was successful, False if connection was closed.
    """
    if connection_id not in self.active_connections:
        return False
    
    try:
        await self.active_connections[connection_id].send_json(data)
        return True
    except WebSocketDisconnect:
        logger.debug(f"Connection {connection_id} already disconnected during send")
        self.disconnect(connection_id)
        return False
    except RuntimeError as e:
        if "close message" in str(e):
            logger.debug(f"Connection {connection_id} already closed: {e}")
            self.disconnect(connection_id)
            return False
        raise
    except Exception as e:
        logger.warning(f"Unexpected error sending to {connection_id}: {e}")
        self.disconnect(connection_id)
        return False
```

**Additional Changes:**
- Added explicit `WebSocketDisconnect` exception handler in the agent processing block to re-raise immediately
- Comments clarify that `send_json` handles closed connections gracefully

### Frontend WebSocket Reconnection Loop Fix (January 12, 2026)

**Issue:** The frontend was creating an infinite loop of WebSocket connections, logging dozens of "[accepted]" and "connection open" messages per second.

**Root Cause:** The `useCallback` for `connectWebSocket` had `messages.length`, `isOpen`, and `reconnectAttempts` in its dependency array. When the welcome message was added (changing `messages.length`), the callback was recreated, causing the `useEffect` that depended on it to re-run, which called `connectWebSocket()` again - creating a loop.

**Solution:** 
1. Added `isConnectingRef` to prevent multiple simultaneous connection attempts
2. Added `hasShownWelcomeRef` to track if the welcome message has been shown (instead of checking `messages.length`)
3. Removed `messages.length` and `isOpen` from the `useCallback` dependency array
4. Added guard conditions: checking `WebSocket.CONNECTING` state and `isConnectingRef.current`
5. Updated the `useEffect` to include `isConnected` check to prevent redundant calls

**Key Changes:**
```tsx
// Added refs for connection state tracking
const isConnectingRef = useRef(false);
const hasShownWelcomeRef = useRef(false);

// Guard against multiple connection attempts
if (wsRef.current?.readyState === WebSocket.CONNECTING) return;
if (isConnectingRef.current) return;

isConnectingRef.current = true;

// Use ref instead of messages.length for welcome message
if (!hasShownWelcomeRef.current) {
    hasShownWelcomeRef.current = true;
    setMessages([...]);
}

// Clean dependency array to avoid loops
}, [reconnectAttempts]);
```

---

