"""
Assistant WebSocket Endpoint
Real-time chat interface for the AI assistant.
Following official FastAPI WebSocket patterns.
"""
import logging
import json
import os
import uuid
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from supabase import Client, create_client

from app.infrastructure.assistant.agent import assistant_graph, AgentState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["Assistant"])


# =============================================================================
# CONNECTION MANAGER (Official FastAPI Pattern)
# =============================================================================

class ConnectionManager:
    """
    Manages WebSocket connections following FastAPI official documentation.
    https://fastapi.tiangolo.com/advanced/websockets/#handling-disconnections-and-multiple-clients
    """
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, connection_id: str):
        """Accept WebSocket connection and store it."""
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        logger.info(f"WebSocket connected: {connection_id}")
    
    def disconnect(self, connection_id: str):
        """Remove a connection from active connections."""
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"WebSocket disconnected: {connection_id}")
    
    async def send_json(self, connection_id: str, data: dict) -> bool:
        """
        Send JSON data to a specific connection.
        
        Returns True if send was successful, False if connection was closed.
        Handles WebSocketDisconnect and RuntimeError gracefully.
        """
        if connection_id not in self.active_connections:
            return False
        
        try:
            await self.active_connections[connection_id].send_json(data)
            return True
        except WebSocketDisconnect:
            # Client disconnected - remove from active connections
            logger.debug(f"Connection {connection_id} already disconnected during send")
            self.disconnect(connection_id)
            return False
        except RuntimeError as e:
            # "Cannot call 'send' once a close message has been sent"
            if "close message" in str(e):
                logger.debug(f"Connection {connection_id} already closed: {e}")
                self.disconnect(connection_id)
                return False
            raise  # Re-raise other RuntimeErrors
        except Exception as e:
            # Handle any other unexpected send errors
            logger.warning(f"Unexpected error sending to {connection_id}: {e}")
            self.disconnect(connection_id)
            return False


manager = ConnectionManager()


# =============================================================================
# WEBSOCKET ENDPOINT (Official FastAPI Pattern)
# =============================================================================

@router.websocket("/chat")
async def assistant_chat(
    websocket: WebSocket,
    token: str = Query(..., description="JWT token for authentication"),
    conversation_id: Optional[str] = Query(None, description="Existing conversation ID")
):
    """
    WebSocket endpoint for assistant chat.
    
    Message Format (Client -> Server):
    {
        "type": "user_message",
        "content": "How many calls did we make today?"
    }
    
    Message Format (Server -> Client):
    {
        "type": "assistant_message" | "connected" | "error" | "assistant_typing",
        "content": "..."
    }
    """
    connection_id = str(uuid.uuid4())
    
    # STEP 1: Accept WebSocket connection FIRST (per FastAPI docs)
    await manager.connect(websocket, connection_id)
    
    try:
        # STEP 2: Authenticate after connection is accepted
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
        supabase = create_client(supabase_url, supabase_key)
        
        # Validate token
        try:
            user_response = supabase.auth.get_user(token)
            user = user_response.user
            if not user:
                await manager.send_json(connection_id, {
                    "type": "error",
                    "content": "Invalid token. Please log in again."
                })
                return
            
            user_id = user.id
            
            # Get tenant_id
            profile = supabase.table("user_profiles").select(
                "tenant_id"
            ).eq("id", user_id).single().execute()
            
            tenant_id = profile.data.get("tenant_id") if profile.data else None
            if not tenant_id:
                await manager.send_json(connection_id, {
                    "type": "error", 
                    "content": "User profile not found."
                })
                return
                
        except Exception as auth_error:
            logger.error(f"Auth error: {auth_error}")
            await manager.send_json(connection_id, {
                "type": "error",
                "content": "Authentication failed. Please log in again."
            })
            return
        
        # STEP 3: Send connected confirmation
        await manager.send_json(connection_id, {
            "type": "connected",
            "message": "Connected to Talky Assistant",
            "conversation_id": conversation_id or "new"
        })
        
        # Initialize conversation
        current_conversation_id = conversation_id
        messages_history = []
        
        if conversation_id:
            try:
                conv_response = supabase.table("assistant_conversations").select(
                    "messages"
                ).eq("id", conversation_id).eq("tenant_id", tenant_id).single().execute()
                
                if conv_response.data:
                    messages_history = conv_response.data.get("messages", [])
            except Exception:
                pass  # Continue with empty history
        
        # STEP 4: Main message loop (per FastAPI docs)
        while True:
            # Wait for message from client
            data = await websocket.receive_json()
            
            if data.get("type") == "user_message":
                user_content = data.get("content", "").strip()
                
                if not user_content:
                    continue
                
                logger.info(f"Received message: {user_content[:50]}...")
                
                # Add to history
                user_message = {
                    "role": "user",
                    "content": user_content,
                    "timestamp": datetime.utcnow().isoformat()
                }
                messages_history.append(user_message)
                
                # Send typing indicator
                await manager.send_json(connection_id, {
                    "type": "assistant_typing",
                    "content": True
                })
                
                # Process with agent
                try:
                    # Prepare state
                    agent_state: AgentState = {
                        "messages": [{"role": "user", "content": user_content}],
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "conversation_id": current_conversation_id,
                        "supabase": supabase,
                        "tool_results": []
                    }
                    
                    # Add history context
                    if len(messages_history) > 1:
                        history_messages = []
                        for msg in messages_history[-10:-1]:
                            history_messages.append({
                                "role": msg.get("role", "user"),
                                "content": msg.get("content", "")
                            })
                        agent_state["messages"] = history_messages + agent_state["messages"]
                    
                    # Run agent
                    logger.info("Running agent...")
                    result = await assistant_graph.ainvoke(agent_state)
                    logger.info(f"Agent returned {len(result.get('messages', []))} messages")
                    
                    # Extract response - handle AIMessage
                    from langchain_core.messages import AIMessage
                    
                    assistant_content = ""
                    for msg in result.get("messages", []):
                        if isinstance(msg, AIMessage):
                            assistant_content = msg.content or ""
                        elif isinstance(msg, dict) and msg.get("role") == "assistant":
                            assistant_content = msg.get("content", "")
                        elif hasattr(msg, 'content') and hasattr(msg, 'type'):
                            if msg.type in ("ai", "assistant"):
                                assistant_content = msg.content or ""
                    
                    if not assistant_content:
                        # Fallback
                        last_msg = result.get("messages", [])[-1] if result.get("messages") else None
                        if last_msg:
                            if isinstance(last_msg, AIMessage):
                                assistant_content = last_msg.content or "I processed your request."
                            elif isinstance(last_msg, dict):
                                assistant_content = last_msg.get("content", "I processed your request.")
                            elif hasattr(last_msg, 'content'):
                                assistant_content = last_msg.content or "I processed your request."
                        else:
                            assistant_content = "I'm not sure how to respond to that."
                    
                    # Add to history
                    messages_history.append({
                        "role": "assistant",
                        "content": assistant_content,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
                    # Send response
                    await manager.send_json(connection_id, {
                        "type": "assistant_message",
                        "content": assistant_content
                    })
                    
                    # Save conversation
                    if current_conversation_id:
                        supabase.table("assistant_conversations").update({
                            "messages": messages_history,
                            "message_count": len(messages_history),
                            "last_message_at": datetime.utcnow().isoformat()
                        }).eq("id", current_conversation_id).execute()
                    else:
                        new_conv = supabase.table("assistant_conversations").insert({
                            "tenant_id": tenant_id,
                            "user_id": user_id,
                            "messages": messages_history,
                            "message_count": len(messages_history),
                            "title": user_content[:50] + ("..." if len(user_content) > 50 else ""),
                            "started_at": datetime.utcnow().isoformat(),
                            "last_message_at": datetime.utcnow().isoformat()
                        }).execute()
                        
                        if new_conv.data:
                            current_conversation_id = new_conv.data[0]["id"]
                            await manager.send_json(connection_id, {
                                "type": "conversation_created",
                                "conversation_id": current_conversation_id
                            })
                
                except WebSocketDisconnect:
                    # Client disconnected during processing - just exit cleanly
                    logger.info(f"Client {connection_id} disconnected during agent processing")
                    raise  # Re-raise to exit the main loop
                
                except Exception as agent_error:
                    logger.error(f"Agent error: {agent_error}", exc_info=True)
                    # Only try to send if connection is still active
                    await manager.send_json(connection_id, {
                        "type": "assistant_message",
                        "content": "Sorry, I encountered an error. Please try again."
                    })
                
                finally:
                    # Stop typing indicator - send_json handles closed connections gracefully
                    await manager.send_json(connection_id, {
                        "type": "assistant_typing",
                        "content": False
                    })
            
            elif data.get("type") == "ping":
                await manager.send_json(connection_id, {"type": "pong"})
    
    except WebSocketDisconnect:
        # Normal disconnection - per FastAPI docs, catch this exception
        logger.info(f"Client {connection_id} disconnected")
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    
    finally:
        manager.disconnect(connection_id)


# =============================================================================
# REST ENDPOINTS
# =============================================================================

@router.get("/conversations")
async def list_conversations(
    supabase: Client = Depends(lambda: create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    )),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List user's assistant conversations."""
    offset = (page - 1) * page_size
    
    response = supabase.table("assistant_conversations").select(
        "id, title, message_count, last_message_at, created_at",
        count="exact"
    ).order("last_message_at", desc=True).range(offset, offset + page_size - 1).execute()
    
    return {
        "conversations": response.data,
        "total": response.count,
        "page": page,
        "page_size": page_size
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    supabase: Client = Depends(lambda: create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    ))
):
    """Get a specific conversation with messages."""
    response = supabase.table("assistant_conversations").select(
        "id, title, messages, context, message_count, started_at, last_message_at"
    ).eq("id", conversation_id).single().execute()
    
    if not response.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    return response.data


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    supabase: Client = Depends(lambda: create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    ))
):
    """Delete a conversation."""
    supabase.table("assistant_conversations").delete().eq(
        "id", conversation_id
    ).execute()
    
    return {"success": True, "message": "Conversation deleted"}


@router.get("/actions")
async def list_actions(
    supabase: Client = Depends(lambda: create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    )),
    type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List assistant actions (audit log)."""
    offset = (page - 1) * page_size
    
    query = supabase.table("assistant_actions").select(
        "id, type, status, triggered_by, input_data, output_data, created_at, completed_at",
        count="exact"
    )
    
    if type:
        query = query.eq("type", type)
    
    response = query.order("created_at", desc=True).range(
        offset, offset + page_size - 1
    ).execute()
    
    return {
        "actions": response.data,
        "total": response.count,
        "page": page,
        "page_size": page_size
    }
