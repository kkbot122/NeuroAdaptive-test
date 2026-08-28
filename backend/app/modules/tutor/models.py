"""
Persisted tutor turns. One row per question/answer pair -- there is
deliberately no separate Conversation table (architecture.md's schema list
for this phase names no such table); `conversation_id` is a client-supplied
grouping key a caller can reuse across turns, not a server-owned resource.
"""
import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.sql import func

from app.db.base import Base


class GroundingMode(str, enum.Enum):
    SOURCE_ONLY = "source_only"  # default: answer is built only from retrieved material
    SUPPLEMENTAL = "supplemental"  # explicitly-labeled general-knowledge content; not default
    INSUFFICIENT = "insufficient"  # retrieval did not support an answer


class TutorMessage(Base):
    __tablename__ = "tutor_messages"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    conversation_id = Column(Uuid, nullable=True, index=True)
    context_lesson_id = Column(Uuid, nullable=True)

    question = Column(Text, nullable=False)
    answer_markdown = Column(Text, nullable=False)

    retrieved_chunk_ids = Column(JSON, nullable=False)  # list[str] -- non-null always, even if empty
    citations = Column(JSON, nullable=False)  # list[{claim, chunk_id, validation_status}]
    grounding_mode = Column(String(16), nullable=False)

    model_id = Column(String(128), nullable=False)
    prompt_version = Column(String(32), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
