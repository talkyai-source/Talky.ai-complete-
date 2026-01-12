"""
Assistant Conversation Domain Models
Defines conversation structure for multi-turn chat with context
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """Role of message sender"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationStatus(str, Enum):
    """Status of a conversation"""
    ACTIVE = "active"
    ARCHIVED = "archived"


class ChatMessage(BaseModel):
    """Single message in a conversation"""
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # For tool calls/results
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    
    # Metadata
    tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    
    class Config:
        use_enum_values = True


class ConversationContext(BaseModel):
    """
    Accumulated context for multi-turn conversations.
    Stores important extracted information.
    """
    # Entities mentioned
    leads_mentioned: List[str] = Field(default_factory=list)
    campaigns_mentioned: List[str] = Field(default_factory=list)
    dates_mentioned: List[str] = Field(default_factory=list)
    
    # User preferences
    preferred_action: Optional[str] = None
    
    # Session state
    current_topic: Optional[str] = None
    pending_confirmation: Optional[Dict[str, Any]] = None
    
    # Custom context
    custom: Dict[str, Any] = Field(default_factory=dict)


class AssistantConversation(BaseModel):
    """
    Full conversation session with the assistant.
    Contains message history and accumulated context.
    """
    id: str
    tenant_id: str
    user_id: Optional[str] = None
    title: Optional[str] = None
    
    messages: List[ChatMessage] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    
    status: ConversationStatus = ConversationStatus.ACTIVE
    message_count: int = 0
    
    started_at: datetime
    last_message_at: datetime
    created_at: datetime
    
    class Config:
        use_enum_values = True
    
    def add_message(
        self, 
        role: MessageRole, 
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None
    ) -> ChatMessage:
        """Add a message to the conversation"""
        message = ChatMessage(
            role=role,
            content=content,
            timestamp=datetime.utcnow(),
            tool_calls=tool_calls,
            tool_call_id=tool_call_id
        )
        self.messages.append(message)
        self.message_count = len(self.messages)
        self.last_message_at = message.timestamp
        return message
    
    def get_messages_for_llm(self, max_messages: int = 20) -> List[Dict[str, Any]]:
        """
        Get messages formatted for LLM context.
        Limits to recent messages to fit context window.
        """
        recent_messages = self.messages[-max_messages:]
        return [
            {
                "role": msg.role,
                "content": msg.content,
                **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
                **({"tool_call_id": msg.tool_call_id} if msg.tool_call_id else {})
            }
            for msg in recent_messages
        ]
    
    def generate_title(self) -> str:
        """Generate a title from the first user message"""
        for msg in self.messages:
            if msg.role == MessageRole.USER:
                # Take first 50 chars of first user message
                title = msg.content[:50]
                if len(msg.content) > 50:
                    title += "..."
                return title
        return "New Conversation"
