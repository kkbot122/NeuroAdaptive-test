import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.db.base import Base


class QuizAttempt(Base):
    """
    A completed quiz, scored server-side.

    Until 2026-08-28 the quiz page made no network calls at all: it read the
    quiz from sessionStorage, scored it in the browser, and wrote the result
    back to sessionStorage, so every result was discarded when the tab closed.
    Mastery estimation needs this evidence to exist.

    `questions` holds the quiz as presented, including the correct answers, so
    an attempt stays interpretable even though the quiz itself was generated
    per-session and is not otherwise persisted. This is a deliberate
    denormalisation for a system that does not yet have a Question table.
    """

    __tablename__ = "quiz_attempts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    title = Column(String, nullable=True)
    topic = Column(String, nullable=True, index=True)

    # Scored on the server from `questions` + `answers`. Never trusted from
    # the client, which is why the client's own score is not accepted.
    score = Column(Integer, nullable=False, default=0)
    total_questions = Column(Integer, nullable=False, default=0)

    questions = Column(JSON, nullable=False, default=list)
    answers = Column(JSON, nullable=False, default=list)

    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    user = relationship("User")
