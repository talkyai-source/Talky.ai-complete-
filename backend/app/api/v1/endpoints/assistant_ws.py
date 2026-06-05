"""
Assistant WebSocket Endpoint
Real-time chat interface for the AI assistant.
Following official FastAPI WebSocket patterns.
"""
import asyncio
import logging
import json
import os
import uuid
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.encoders import jsonable_encoder
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.config import get_settings
from app.core.jwt_security import JWTValidationError, decode_and_validate_token
from app.infrastructure.assistant.agent import assistant_graph, AgentState
from app.infrastructure.assistant.model_config import get_tenant_assistant_model

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
            await self.active_connections[connection_id].send_json(jsonable_encoder(data))
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


def _first_row(data):
    """Normalize PostgREST-style responses across list and single-object shapes."""
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


# =============================================================================
# WEBSOCKET ENDPOINT (Official FastAPI Pattern)
# =============================================================================

def _read_cookie_token(websocket: WebSocket) -> Optional[str]:
    """
    AH-Phase-F2: read the talky_at HttpOnly cookie from the WebSocket
    handshake. Browsers send cookies for the WS destination origin
    (api.talkleeai.com) on the handshake automatically, the same way
    they do for fetch. Path-scoped to /api/v1, which covers the WS
    endpoint at /api/v1/assistant/chat, so the cookie is present
    when the user has an active REST session.

    Returns the cookie value or None.
    """
    raw = websocket.cookies.get("talky_at")
    if not raw:
        return None
    stripped = raw.strip()
    return stripped or None


async def _resolve_ws_token(websocket: WebSocket, url_token: Optional[str]) -> Optional[str]:
    """
    Resolve the auth token without exposing it in the WebSocket URL.

    Priority order:
      1. `talky_at` HttpOnly cookie  — preferred. Same auth surface as
         REST endpoints, no JS-readable token.
      2. First-message {"type":"auth","token":"…"}  — frame fallback for
         clients that can't carry cookies (admin frontend, native
         shells). 5-second wait after accept().

    The `?token=` URL-query backwards-compat from Phase A was cut on
    2026-05-21 (vuln-fix sprint). 24h soak completed; the deprecation
    WARN line `assistant_ws: deprecated ?token= URL query used`
    appeared only in synthetic tests during that window. The
    `url_token` parameter is still on the signature so we can log
    attempts that still try to use the old form.
    """
    cookie_token = _read_cookie_token(websocket)
    if cookie_token:
        return cookie_token

    if url_token:
        logger.warning(
            "assistant_ws: rejected ?token= URL query — that path was "
            "cut on 2026-05-21; client must use cookie or first-message auth"
        )
        # Do NOT return the url_token. Fall through so the missing-auth
        # close path runs and the client sees a 1008 — that surfaces the
        # problem instead of silently letting old clients keep working.

    try:
        first_frame = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.info("assistant_ws: client did not send auth frame within 5s")
        return None
    except WebSocketDisconnect:
        return None
    except Exception as e:
        logger.info("assistant_ws: failed to parse first frame: %s", e)
        return None

    if not isinstance(first_frame, dict):
        return None
    if first_frame.get("type") != "auth":
        return None
    token = first_frame.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    return token


def _is_origin_allowed(websocket: WebSocket) -> bool:
    """
    Check the WebSocket Origin header against the same allowed_origins
    list the REST CORS middleware uses. Without this check, any
    cross-origin browser tab can open a WS to our backend with the
    user's cookies (the talky_at HttpOnly cookie travels on WS just
    like it does on fetch) and gain agent-tool access.

    Origin is sent by all spec-compliant browsers on WebSocket
    handshakes. Non-browser clients (curl, wscat) typically omit it;
    those are treated as allowed because they're the operational /
    debugging path and don't carry browser cookies.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True  # non-browser client; no cross-origin cookie risk
    settings = get_settings()
    allowed = settings.allowed_origins
    return origin in allowed


@router.websocket("/chat")
async def assistant_chat(
    websocket: WebSocket,
    token: Optional[str] = Query(None, description="DEPRECATED — send {type:auth,token} as first frame instead. Removed after 2026-05-21 soak."),
    conversation_id: Optional[str] = Query(None, description="Existing conversation ID")
):
    """
    WebSocket endpoint for assistant chat.

    Authentication flow (Phase A hardening, 2026-05-20):

    1. Browser handshake must carry an Origin in the configured CORS
       allow-list. Cross-origin attempts close with code 1008.
    2. Server accepts the upgrade, then waits up to 5 seconds for the
       client to send `{"type":"auth","token":"<JWT>"}`.
    3. Token is verified through `decode_and_validate_token` — the
       same path REST endpoints use. The previous Supabase
       `db_client.auth.get_user(token)` path could drift from the
       REST verification (claims, blacklist, MFA enforcement) and
       become a backdoor.

    Message Format (Client -> Server):
        {"type":"auth","token":"<JWT>"}        — once, immediately after onopen
        {"type":"user_message","content":"…"}  — subsequent chat
        {"type":"ping"}                        — keepalive

    Message Format (Server -> Client):
        {"type":"connected","conversation_id":"…","message":"…"}
        {"type":"assistant_message","content":"…"}
        {"type":"assistant_typing","content":bool}
        {"type":"error","content":"…"}
    """
    connection_id = str(uuid.uuid4())

    # STEP 0: Reject cross-origin upgrades BEFORE accepting.
    if not _is_origin_allowed(websocket):
        origin = websocket.headers.get("origin")
        logger.warning("assistant_ws: rejecting cross-origin upgrade from %r", origin)
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    # STEP 1: Accept WebSocket connection (per FastAPI docs)
    await manager.connect(websocket, connection_id)

    try:
        # STEP 2: Resolve auth token (first-message preferred, ?token= fallback for 24h soak)
        resolved_token = await _resolve_ws_token(websocket, token)
        if not resolved_token:
            await manager.send_json(connection_id, {
                "type": "error",
                "content": "Authentication required. Send {type:'auth',token:'<JWT>'} as the first message."
            })
            await websocket.close(code=1008, reason="Missing auth")
            return

        # STEP 3: Verify the token through the same path REST endpoints use.
        try:
            payload = decode_and_validate_token(resolved_token)
        except JWTValidationError as jwt_err:
            logger.info("assistant_ws: token verification failed: %s", jwt_err.detail)
            await manager.send_json(connection_id, {
                "type": "error",
                "content": "Invalid or expired token. Please sign in again."
            })
            await websocket.close(code=1008, reason="Invalid token")
            return

        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id.strip():
            await manager.send_json(connection_id, {
                "type": "error",
                "content": "Invalid token: missing subject."
            })
            await websocket.close(code=1008, reason="Invalid token")
            return
        user_id = str(user_id)

        # STEP 4: Resolve tenant via DB lookup (same flow as before).
        db_client = get_db_client()
        try:
            profile = db_client.table("user_profiles").select(
                "tenant_id"
            ).eq("id", user_id).single().execute()
            tenant_id = (
                str(profile.data.get("tenant_id"))
                if profile.data and profile.data.get("tenant_id")
                else None
            )
        except Exception as profile_err:
            logger.error("assistant_ws: profile lookup failed: %s", profile_err)
            tenant_id = None

        if not tenant_id:
            await manager.send_json(connection_id, {
                "type": "error",
                "content": "User profile not found."
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
                conv_response = db_client.table("assistant_conversations").select(
                    "messages"
                ).eq("id", conversation_id).eq("tenant_id", tenant_id).single().execute()
                
                if conv_response.data:
                    raw_messages = conv_response.data.get("messages", [])
                    if isinstance(raw_messages, str):
                        try:
                            raw_messages = json.loads(raw_messages)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Assistant conversation %s has non-JSON messages payload",
                                conversation_id,
                            )
                            raw_messages = []
                    messages_history = raw_messages if isinstance(raw_messages, list) else []
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
                        "db_client": db_client,
                        "tool_results": [],
                        "model": await get_tenant_assistant_model(db_client, tenant_id),
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
                    
                    # Persist chat state without breaking the already-sent assistant reply.
                    try:
                        if current_conversation_id:
                            db_client.table("assistant_conversations").update({
                                "messages": messages_history,
                                "message_count": len(messages_history),
                                "last_message_at": datetime.utcnow().isoformat()
                            }).eq("id", current_conversation_id).execute()
                        else:
                            new_conv = db_client.table("assistant_conversations").insert({
                                "tenant_id": tenant_id,
                                "user_id": user_id,
                                "messages": messages_history,
                                "message_count": len(messages_history),
                                "title": user_content[:50] + ("..." if len(user_content) > 50 else ""),
                                "started_at": datetime.utcnow().isoformat(),
                                "last_message_at": datetime.utcnow().isoformat()
                            }).single().execute()

                            created_conv = _first_row(new_conv.data)
                            if created_conv and created_conv.get("id"):
                                current_conversation_id = str(created_conv["id"])
                                await manager.send_json(connection_id, {
                                    "type": "conversation_created",
                                    "conversation_id": current_conversation_id
                                })
                            else:
                                logger.warning(
                                    "Assistant conversation insert returned unexpected payload shape: %r",
                                    new_conv.data,
                                )
                    except Exception:
                        logger.exception(
                            "Failed to persist assistant conversation state",
                            extra={"conversation_id": current_conversation_id},
                        )
                
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
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List user's assistant conversations.

    ``get_current_user`` is required so the per-request RLS tenant
    context is set; otherwise the SELECT returns 0 rows and the UI
    sees an empty conversation list. Same fix pattern as the campaign
    endpoints.
    """
    offset = (page - 1) * page_size

    response = db_client.table("assistant_conversations").select(
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
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Get a specific conversation with messages.

    ``get_current_user`` enforces tenant isolation via RLS — without
    it any authenticated user could read any conversation by ID.
    """
    response = db_client.table("assistant_conversations").select(
        "id, title, messages, context, message_count, started_at, last_message_at"
    ).eq("id", conversation_id).single().execute()

    if not response.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")

    return response.data


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Delete a conversation.

    ``get_current_user`` enforces tenant isolation — without it the
    DELETE would run with no RLS scope.
    """
    db_client.table("assistant_conversations").delete().eq(
        "id", conversation_id
    ).execute()

    return {"success": True, "message": "Conversation deleted"}


@router.get("/actions")
async def list_actions(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List assistant actions (audit log)."""
    offset = (page - 1) * page_size
    
    query = db_client.table("assistant_actions").select(
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
