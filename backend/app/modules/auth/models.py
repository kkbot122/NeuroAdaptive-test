from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)

    hashed_password = Column(String, nullable=True)

    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Privacy: "full" records behavioral telemetry (events/*);
    # "minimal" declines it. Separate from account existence -- a user with
    # tracking_consent="minimal" still gets a fully working core loop
    # (upload -> generate -> study -> assess -> recommend), since none of
    # that path reads LearningEvent at all. Default "full" preserves
    # existing behavior for accounts created before this column existed.
    tracking_consent = Column(String(16), nullable=False, default="full", server_default="full")

    # Explicit presentation defaults apply when a new teaching activity is
    # created. Existing activities retain their saved format and position.
    default_presentation_format = Column(String(24), nullable=False, default="detailed", server_default="detailed")
    tutor_panel_open = Column(Boolean, nullable=False, default=True, server_default="true")

    profile = relationship("UserProfile", back_populates="user", uselist=False)
    chat_sessions = relationship("ChatSession", back_populates="user")
    article_readings = relationship("ArticleReading", back_populates="user")
