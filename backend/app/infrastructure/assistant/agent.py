"""
Assistant Agent - LangGraph ReAct Agent
Conversational AI assistant using Groq for reasoning and tools for data/actions.
"""
import logging
import json
from typing import Optional, List, Dict, Any, Annotated, TypedDict, Literal
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# LangChain message classes for proper LangGraph compatibility
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from groq import AsyncGroq

from supabase import Client

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

logger = logging.getLogger(__name__)


# =============================================================================
# STATE DEFINITION
# =============================================================================

class AgentState(TypedDict):
    """State for the assistant agent"""
    messages: Annotated[list, add_messages]
    tenant_id: str
    user_id: Optional[str]
    conversation_id: Optional[str]
    supabase: Any  # Supabase client
    tool_results: List[Dict[str, Any]]


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
- get_leads: Get lead information with filters
- get_campaigns: List all campaigns
- get_recent_calls: Get recent call history
- get_actions_today: See what actions have been taken today
- send_email: Send emails to recipients (supports templates: meeting_confirmation, follow_up, reminder)
- send_sms: Send SMS messages
- initiate_call: Start an outbound call
- start_campaign: Start or resume a campaign

**Guidelines:**
- Be concise and helpful
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
                            "arguments": json.dumps(tc.get("args")) if isinstance(tc.get("args"), dict) else tc.get("args")
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
                                "arguments": json.dumps(args) if isinstance(args, dict) else (args or "{}")
                            }
                        })
                formatted_msg["tool_calls"] = groq_tool_calls
            
            # Preserve tool_call_id for tool responses
            if "tool_call_id" in msg:
                formatted_msg["tool_call_id"] = msg["tool_call_id"]
            
            messages.append(formatted_msg)
    
    # Define tools for Groq
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_dashboard_stats",
                "description": "Get today's call statistics - total calls, success rate, active campaigns",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date in YYYY-MM-DD format, defaults to today"}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_usage_info",
                "description": "Get plan usage - minutes allocated, used, remaining, subscription status",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_leads",
                "description": "Get leads list with optional filters",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "campaign_id": {"type": "string", "description": "Filter by campaign ID"},
                        "status": {"type": "string", "description": "Filter by status"},
                        "limit": {"type": "integer", "description": "Max leads to return", "default": 10}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_campaigns",
                "description": "Get all campaigns with status and progress",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by status (draft, running, completed)"}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_calls",
                "description": "Get recent calls with outcomes",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "today_only": {"type": "boolean", "description": "Only show today's calls", "default": True},
                        "limit": {"type": "integer", "default": 10}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_actions_today",
                "description": "Get assistant actions performed today (emails, SMS, calls triggered)",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Send an email to recipients. Supports templates: meeting_confirmation, follow_up, reminder. Uses Gmail if connected, SMTP fallback otherwise.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "array", "items": {"type": "string"}, "description": "Email addresses"},
                        "subject": {"type": "string", "description": "Email subject (ignored if using template)"},
                        "body": {"type": "string", "description": "Email body (ignored if using template)"},
                        "template_name": {"type": "string", "description": "Template to use: meeting_confirmation, follow_up, or reminder"},
                        "template_context": {"type": "object", "description": "Variables for template (e.g., attendee_name, title, date, time)"}
                    },
                    "required": ["to", "subject", "body"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "send_sms",
                "description": "Send SMS to phone numbers",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "array", "items": {"type": "string"}, "description": "Phone numbers"},
                        "message": {"type": "string"}
                    },
                    "required": ["to", "message"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "initiate_call",
                "description": "Start an outbound call to a phone number",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone_number": {"type": "string"},
                        "campaign_id": {"type": "string", "description": "Optional campaign context"}
                    },
                    "required": ["phone_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "start_campaign",
                "description": "Start or resume a campaign",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "campaign_id": {"type": "string"}
                    },
                    "required": ["campaign_id"]
                }
            }
        }
    ]
    
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=500
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
    supabase = state["supabase"]
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
        
        try:
            # Route to appropriate tool
            if func_name == "get_dashboard_stats":
                result = await get_dashboard_stats(tenant_id, supabase, **args)
            elif func_name == "get_usage_info":
                result = await get_usage_info(tenant_id, supabase)
            elif func_name == "get_leads":
                result = await get_leads(tenant_id, supabase, **args)
            elif func_name == "get_campaigns":
                result = await get_campaigns(tenant_id, supabase, **args)
            elif func_name == "get_recent_calls":
                result = await get_recent_calls(tenant_id, supabase, **args)
            elif func_name == "get_actions_today":
                result = await get_actions_today(tenant_id, supabase)
            elif func_name == "send_email":
                result = await send_email(tenant_id, supabase, conversation_id=conversation_id, **args)
            elif func_name == "send_sms":
                result = await send_sms(tenant_id, supabase, conversation_id=conversation_id, **args)
            elif func_name == "initiate_call":
                result = await initiate_call(tenant_id, supabase, conversation_id=conversation_id, **args)
            elif func_name == "start_campaign":
                result = await start_campaign(tenant_id, supabase, conversation_id=conversation_id, **args)
            else:
                result = {"error": f"Unknown tool: {func_name}"}
            
            # Return as ToolMessage for proper LangGraph handling
            tool_message = ToolMessage(
                content=json.dumps(result),
                tool_call_id=tool_call_id
            )
            results.append(tool_message)
            
        except Exception as e:
            logger.error(f"Tool execution error for {func_name}: {e}")
            error_message = ToolMessage(
                content=json.dumps({"error": str(e)}),
                tool_call_id=tool_call_id
            )
            results.append(error_message)
    
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
