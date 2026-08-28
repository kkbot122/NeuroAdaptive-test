"""
Audit log for security-sensitive actions (Phase 6): auth events, deletion,
admin actions, quota overrides. No admin role or quota-override mechanism
exists yet in this codebase, so only account-deletion requests write here
this phase -- the table is general-purpose (actor/action/target/metadata),
not scoped to one action type, so adding a new audited action later is a
new call site, not a schema change.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.sql import func

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)  # e.g. "account_deletion_requested"
    target_type = Column(String(64), nullable=False)  # e.g. "user"
    target_id = Column(String(64), nullable=False)  # stringified -- targets vary in id type (int, UUID)
    extra = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
