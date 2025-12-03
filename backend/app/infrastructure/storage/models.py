"""
SQLAlchemy Database Models
Maps to Supabase PostgreSQL tables
"""
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, DECIMAL, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class Campaign(Base):
    """Campaign model - maps to campaigns table"""
    __tablename__ = "campaigns"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # MULTI-TENANT: Uncomment when enabling multi-tenancy
    # tenant_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(String(50), nullable=False, default="draft")
    system_prompt = Column(Text, nullable=False)
    voice_id = Column(String(100), nullable=False)
    max_concurrent_calls = Column(Integer, default=10)
    retry_failed = Column(Boolean, default=True)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    total_leads = Column(Integer, default=0)
    calls_completed = Column(Integer, default=0)
    calls_failed = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    leads = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")
    calls = relationship("Call", back_populates="campaign", cascade="all, delete-orphan")


class Lead(Base):
    """Lead model - maps to leads table"""
    __tablename__ = "leads"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # MULTI-TENANT: Uncomment when enabling multi-tenancy
    # tenant_id = Column(String(255), nullable=False, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    phone_number = Column(String(20), nullable=False)
    first_name = Column(String(100))
    last_name = Column(String(100))
    email = Column(String(255))
    custom_fields = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_called_at = Column(DateTime(timezone=True))
    call_attempts = Column(Integer, default=0)
    status = Column(String(50), default="pending")
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    campaign = relationship("Campaign", back_populates="leads")
    calls = relationship("Call", back_populates="lead", cascade="all, delete-orphan")


class Call(Base):
    """Call model - maps to calls table"""
    __tablename__ = "calls"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # MULTI-TENANT: Uncomment when enabling multi-tenancy
    # tenant_id = Column(String(255), nullable=False, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    phone_number = Column(String(20), nullable=False)
    status = Column(String(50), nullable=False, default="initiated")
    started_at = Column(DateTime(timezone=True))
    answered_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    recording_url = Column(Text)
    transcript = Column(Text)
    summary = Column(Text)
    cost = Column(DECIMAL(10, 4))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    campaign = relationship("Campaign", back_populates="calls")
    lead = relationship("Lead", back_populates="calls")
    conversations = relationship("Conversation", back_populates="call", cascade="all, delete-orphan")


class Conversation(Base):
    """Conversation model - maps to conversations table"""
    __tablename__ = "conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # MULTI-TENANT: Uncomment when enabling multi-tenancy
    # tenant_id = Column(String(255), nullable=False, index=True)
    call_id = Column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    messages = Column(JSONB, default=[])
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    ended_at = Column(DateTime(timezone=True))
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    call = relationship("Call", back_populates="conversations")
