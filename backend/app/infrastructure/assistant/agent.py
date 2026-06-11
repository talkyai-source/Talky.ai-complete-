"""
Assistant Agent - LangGraph ReAct Agent
Conversational AI assistant using Groq for reasoning and tools for data/actions.
"""
import logging
import json
from typing import Optional, List, Dict, Any, Annotated, TypedDict, Literal
from datetime import datetime

from fastapi.encoders import jsonable_encoder
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# LangChain message classes for proper LangGraph compatibility
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from groq import AsyncGroq

import asyncpg  # migrated from db_client

from app.infrastructure.assistant.tools import (
    get_dashboard_stats,
    get_usage_info,
    get_leads,
    get_campaigns,
    get_recent_calls,
    get_actions_today,
    send_email,
    send_sms,
    initiate_call,
    start_campaign,
    ALL_TOOLS
)
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS
from app.infrastructure.assistant.tools.dispatch import dispatch_tool
from app.infrastructure.assistant.model_config import normalize_model

logger = logging.getLogger(__name__)


def _dump_json(data: Any) -> str:
    """Encode assistant payloads using FastAPI's JSON-safe conversion rules."""
    return json.dumps(jsonable_encoder(data))


# =============================================================================
# STATE DEFINITION
# =============================================================================

class AgentState(TypedDict):
    """State for the assistant agent"""
    messages: Annotated[list, add_messages]
    tenant_id: str
    user_id: Optional[str]
    conversation_id: Optional[str]
    db_client: Any  # PostgreSQL client
    tool_results: List[Dict[str, Any]]
    model: Optional[str]


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """You are Talky Assistant, an AI assistant for the Talky.ai voice calling platform.

You help users with:
1. **Querying Data**: Answer questions about calls, campaigns, leads, usage, and statistics
2. **Taking Actions**: Send emails, SMS, initiate calls, start campaigns when asked
3. **Providing Suggestions**: Give recommendations based on data patterns

**Your Capabilities:**
- get_dashboard_stats: Get today's call stats (calls made, success rate, etc.)
- get_usage_info: Get plan usage (minutes used, remaining)
- get_leads: List leads/contacts (filters: campaign, status, only_leads=true for just qualified leads). Each includes is_lead + follow_up_note.
- get_lead_followup: Get the follow-up for ONE lead by lead_id / phone_number / name — the follow-up note/tips and that lead's qualified call summary. Use when asked "how should I follow up with <person>" or "what's the follow-up for <number>".
- get_campaigns: List all campaigns
- get_recent_calls: Get recent call history
- get_actions_today: See what actions have been taken today
- send_email: Email someone. To email a lead/contact, omit "to" and pass lead_id or phone_number — their email is resolved automatically. Call with confirm=false to PREVIEW; the user gets Apply/Reject buttons that send it (don't set confirm=true yourself). Supports templates: meeting_confirmation, follow_up, reminder.
- send_sms: Send SMS messages
- report_issue: File a technical problem to the support team. When the user reports something broken or is clearly stuck on a TECHNICAL issue (calls not going through, voice/provider errors, can't log in, billing/dashboard glitches), help them: ask one or two quick questions to pin down what failed, then call report_issue with a clear `description` (+ category/severity if obvious). It auto-adds the tenant id, account email and timestamp and emails support immediately — so confirm to the user it's been sent. Don't use it for how-to questions you can answer yourself.
- initiate_call: Start an outbound call
- start_campaign: Start or resume a campaign

- get_campaign_detail / get_knowledge_tree / retrieve_knowledge: inspect a campaign's config + knowledge, and test what the knowledge tree returns for a question
- update_campaign_config / update_knowledge_node: edit campaign config and knowledge nodes
- manage_lead: add a new lead, remove (soft-delete) an existing lead, or update an existing lead's phone number, name, or email
- list_voices: list a provider's available TTS voices (name, id, and gender/accent where known). When the user asks what voices are available, present them by NAME (with gender/accent if useful) — never dump raw ids.
- apply_campaign_voice: change a campaign's TTS voice/provider (AI options) for one or more campaigns. Pass the voice NAME the user said (e.g. "Orus", "Sarah") as voice_id — it is resolved to the id automatically. If the tool returns ambiguous=true with candidates, list those candidate names and ask the user which one; if it returns available_voices, the name didn't match — show a few options by name. Users never need to know voice ids.

**Creating campaigns:** There is NO create_campaign tool. If the user asks to create a new campaign, do NOT call any tool (manage_lead adds a CONTACT, not a campaign — never use it for this). Instead, explain that new campaigns are created from the dashboard: Campaigns → New Campaign, a 3-step wizard (basics + persona, knowledge upload, voice). Offer to help right after — once it exists you CAN configure it: update its config, upload knowledge answers, change the voice, and add contacts.

**Tool discipline:** Only call tools from the list above — never invent a tool name. If no tool fits the request, say what you can and can't do in plain text instead of guessing with the wrong tool.

**Editing & sending:** For ANY editing tool (update_campaign_config, update_knowledge_node, manage_lead, apply_campaign_voice) AND for send_email, call it with confirm=false to PREVIEW the change. The user is then shown the exact before→after diff with **Apply** and **Reject** buttons in the UI — those buttons perform the apply for you. So: (1) call the tool with confirm=false, (2) in one short sentence tell the user what you've proposed and that they can Apply or Reject it, then STOP. Do NOT ask them to "type yes", and do NOT call the tool again with confirm=true yourself — the Apply button does that. Only fall back to calling with confirm=true directly if the user explicitly insists on applying without the buttons.

**AI model:** The assistant cannot change the global LLM model — it is a shared, process-level setting that must be configured from the AI Options page in the dashboard.

**Guidelines:**
- Give complete, thorough answers — explain fully and do NOT truncate. When a question has multiple parts or you're summarizing data, use short paragraphs and bullet lists so nothing is left out. Prefer a detailed, well-structured reply over a terse one (but stay on-topic; don't pad with filler).
- Use tools to get real data before answering data questions
- Confirm before executing destructive actions
- Stay within the user's tenant scope (they can only access their own data)
- Be conversational when appropriate (greetings, small talk)
- Format numbers nicely (e.g., "47 calls" not "47")
- If you don't have a tool to answer something, say so

Current date/time: {current_time}
"""


# =============================================================================
# AGENT NODE
# =============================================================================

async def agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Main agent node that processes messages and decides on tool calls.
    Uses Groq for fast inference.
    """
    import os
    
    groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    
    # Build messages for Groq
    system_message = {
        "role": "system",
        "content": SYSTEM_PROMPT.format(current_time=datetime.utcnow().isoformat())
    }
    
    # Convert state messages to Groq format
    # Convert state messages to Groq format
    messages = [system_message]
    for msg in state["messages"]:
        if hasattr(msg, 'content'):
            # LangGraph message object - map type to role
            msg_type = getattr(msg, 'type', 'user')
            
            # Map LangGraph types to Groq roles
            if msg_type == "human":
                role = "user"
            elif msg_type == "ai":
                role = "assistant"
            elif msg_type == "tool":
                role = "tool"
            else:
                role = msg_type if msg_type in ("user", "assistant", "tool", "system") else "user"
            
            # Base message
            formatted_msg = {
                "role": role,
                "content": msg.content or ""
            }
            
            # Add tool_calls if present (for assistant messages)
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                formatted_msg["tool_calls"] = [
                    {
                        "id": tc.get("id"),
                        "type": "function",
                        "function": {
                            "name": tc.get("name"),
                            "arguments": _dump_json(tc.get("args")) if isinstance(tc.get("args"), dict) else tc.get("args")
                        }
                    }
                    for tc in msg.tool_calls
                ]
            
            # Add tool_call_id if present (for tool messages)
            if hasattr(msg, 'tool_call_id') and msg.tool_call_id:
                formatted_msg["tool_call_id"] = msg.tool_call_id
                
            messages.append(formatted_msg)
            
        elif isinstance(msg, dict):
            # Already a dict - ensure role is valid
            role = msg.get("role", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            
            # Build formatted message
            formatted_msg = {"role": role, "content": msg.get("content", "")}
            
            # Convert tool_calls from LangGraph format to Groq format if present
            if "tool_calls" in msg and msg["tool_calls"]:
                groq_tool_calls = []
                for tc in msg["tool_calls"]:
                    # Handle both formats
                    if "function" in tc:
                        # Already in Groq format
                        groq_tool_calls.append(tc)
                    else:
                        # Convert from LangGraph format
                        args = tc.get("args", {})
                        groq_tool_calls.append({
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": _dump_json(args) if isinstance(args, dict) else (args or "{}")
                            }
                        })
                formatted_msg["tool_calls"] = groq_tool_calls
            
            # Preserve tool_call_id for tool responses
            if "tool_call_id" in msg:
                formatted_msg["tool_call_id"] = msg["tool_call_id"]
            
            messages.append(formatted_msg)
    
    # Define tools for Groq (schemas live in tools/llm_schemas.py)
    tools = GROQ_TOOL_SCHEMAS
    
    try:
        response = await groq_client.chat.completions.create(
            model=normalize_model(state.get("model")),
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=2000
        )
        
        message = response.choices[0].message
        
        # Check if there are tool calls
        if message.tool_calls:
            # Build tool calls in LangGraph format
            # LangGraph expects: {"name": str, "args": dict, "id": str}
            formatted_tool_calls = []
            for tc in message.tool_calls:
                # Parse arguments - ensure it's a dict, not None or string
                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                
                # Ensure args is never None - must be empty dict at minimum
                if args is None:
                    args = {}
                
                formatted_tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": args  # Must be a dict, never None
                })
            
            # Return as AIMessage for proper LangGraph handling
            ai_message = AIMessage(
                content=message.content or "",
                tool_calls=formatted_tool_calls
            )
            return {"messages": [ai_message]}
        else:
            # Return as AIMessage without tool calls
            ai_message = AIMessage(content=message.content or "")
            return {"messages": [ai_message]}
    
    except Exception as e:
        logger.error(f"Agent error: {e}")
        # Return error as AIMessage
        error_message = AIMessage(content=f"I encountered an error: {str(e)}. Please try again.")
        return {"messages": [error_message]}


# =============================================================================
# TOOL EXECUTION NODE
# =============================================================================

async def tool_executor(state: AgentState) -> Dict[str, Any]:
    """
    Execute tools called by the agent.
    """
    last_message = state["messages"][-1]
    
    # Handle both AIMessage objects and dicts
    if isinstance(last_message, AIMessage):
        if not last_message.tool_calls:
            return {"messages": []}
        tool_calls = last_message.tool_calls
    elif isinstance(last_message, dict) and "tool_calls" in last_message:
        tool_calls = last_message["tool_calls"]
    else:
        return {"messages": []}
    
    results = []
    
    tenant_id = state["tenant_id"]
    db_client = state["db_client"]
    conversation_id = state.get("conversation_id")
    
    for tc in tool_calls:
        # Handle both dict format and ToolCall-like objects
        if isinstance(tc, dict):
            if "function" in tc:
                func_name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments", "{}")
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            else:
                func_name = tc.get("name", "")
                args = tc.get("args", {}) or {}
            tool_call_id = tc.get("id", "")
        else:
            # Object with attributes
            func_name = getattr(tc, "name", "")
            args = getattr(tc, "args", {}) or {}
            tool_call_id = getattr(tc, "id", "")
        
        # Route through the shared dispatcher (single source of truth, also
        # used by the streaming loop). dispatch_tool never raises — failures
        # come back as {"error": ...}.
        result = await dispatch_tool(func_name, tenant_id, db_client, conversation_id, args)
        tool_message = ToolMessage(
            content=_dump_json(result),
            tool_call_id=tool_call_id,
        )
        results.append(tool_message)
    
    # Also store raw results for websocket response
    raw_results = [{"role": "tool", "tool_call_id": getattr(m, "tool_call_id", ""), "content": m.content} for m in results]
    return {"messages": results, "tool_results": raw_results}


# =============================================================================
# ROUTING
# =============================================================================

def should_continue(state: AgentState) -> Literal["tools", "end"]:
    """
    Decide whether to continue to tools or end.
    """
    last_message = state["messages"][-1]
    
    # Handle AIMessage objects
    if isinstance(last_message, AIMessage):
        if last_message.tool_calls:
            return "tools"
        return "end"
    
    # Handle dict messages (legacy)
    if isinstance(last_message, dict) and "tool_calls" in last_message:
        return "tools"
    
    return "end"


# =============================================================================
# GRAPH BUILDER
# =============================================================================

def create_assistant_graph():
    """
    Create the LangGraph for the assistant agent.
    """
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_executor)
    
    # Set entry point
    workflow.set_entry_point("agent")
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "end": END
        }
    )
    
    # Tools always go back to agent
    workflow.add_edge("tools", "agent")
    
    return workflow.compile()


# Create the graph instance
assistant_graph = create_assistant_graph()
