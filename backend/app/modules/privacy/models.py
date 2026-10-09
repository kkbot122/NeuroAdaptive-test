"""Durable cleanup work for private originals and abandoned upload objects."""
import uuid

from sqlalchemy import Column, DateTime, Integer, String, Uuid, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base import Base


class StorageCleanupTask(Base):
    __tablename__ = "storage_cleanup_tasks"
    __table_args__ = (UniqueConstraint("storage_target", name="uq_storage_cleanup_target"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    # Deliberately not a foreign key: cleanup must survive account deletion.
    owner_id = Column(Integer, nullable=True, index=True)
    storage_target = Column(String(512), nullable=False)
    storage_kind = Column(String(16), nullable=False)  # private or local
    reason = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    error_category = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
