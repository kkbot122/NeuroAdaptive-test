"""Durable (DB-backed, unlike core/rate_limit.py's in-memory limiters)
per-user daily AI-call budget tracking. Durable because a daily budget must
survive a backend restart -- an in-memory counter would silently reset a
user's exhausted quota on every deploy."""
import uuid
from datetime import date

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid

from app.db.base import Base


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (UniqueConstraint("owner_id", "usage_date", name="uq_ai_usage_daily_owner_date"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, default=date.today)
    call_count = Column(Integer, nullable=False, default=0)


class AIProviderCall(Base):
    """One reserved outbound attempt; never stores prompts, responses, or credentials."""

    __tablename__ = "ai_provider_calls"
    __table_args__ = (Index("ix_ai_provider_calls_owner_started", "owner_id", "started_at"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operation_id = Column(Uuid, nullable=False)
    resource_id = Column(Uuid, nullable=True)
    feature = Column(String(32), nullable=False)
    phase = Column(String(32), nullable=False)
    provider = Column(String(16), nullable=False)
    model_id = Column(String(128), nullable=False)
    operation = Column(String(16), nullable=False)
    retry_index = Column(Integer, nullable=False, default=0)
    input_items = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="STARTED")
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    elapsed_ms = Column(Integer, nullable=True)
    capacity_wait_ms = Column(Integer, nullable=False, default=0)
    error_category = Column(String(32), nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cached_input_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
