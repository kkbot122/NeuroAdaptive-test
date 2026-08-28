"""Durable (DB-backed, unlike core/rate_limit.py's in-memory limiters)
per-user daily AI-call budget tracking. Durable because a daily budget must
survive a backend restart -- an in-memory counter would silently reset a
user's exhausted quota on every deploy."""
import uuid
from datetime import date

from sqlalchemy import Column, Date, ForeignKey, Integer, UniqueConstraint, Uuid

from app.db.base import Base


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (UniqueConstraint("owner_id", "usage_date", name="uq_ai_usage_daily_owner_date"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, default=date.today)
    call_count = Column(Integer, nullable=False, default=0)
