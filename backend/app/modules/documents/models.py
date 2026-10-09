import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class DocumentRole(str, enum.Enum):
    SYLLABUS = "SYLLABUS"
    STUDY = "STUDY"


class DocumentSourceKind(str, enum.Enum):
    """How the bytes arrived. PASTED_TEXT skips the upload step entirely --
    the text is written to disk exactly like an uploaded .txt so the rest of
    the pipeline (extraction, chunking) needs no separate code path."""

    UPLOAD = "UPLOAD"
    PASTED_TEXT = "PASTED_TEXT"


class DocumentStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    NEEDS_INPUT = "NEEDS_INPUT"  # e.g. no extractable native text
    FAILED = "FAILED"


class Document(Base):
    """
    One uploaded source file belonging to a course.

    storage_key identifies the private object-store original. storage_path is
    retained only to read existing local-development rows created before the
    private-storage migration.
    """

    __tablename__ = "documents"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)

    # Denormalised from courses.owner_id so ownership can be enforced in a
    # single query without a join on every retrieval path.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    role = Column(String(16), nullable=False, default=DocumentRole.STUDY.value)
    status = Column(String(32), nullable=False, default=DocumentStatus.UPLOADED.value)

    source_kind = Column(String(16), nullable=False, default=DocumentSourceKind.UPLOAD.value)

    storage_path = Column(String(512), nullable=False)
    storage_key = Column(String(512), nullable=True, unique=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    page_count = Column(Integer, nullable=True)

    # SHA-256 of the file's bytes. Re-uploading a file already present in the
    # same course reuses the existing document and its processed artifacts
    # instead of storing a duplicate and reprocessing it.
    checksum_sha256 = Column(String(64), nullable=False, index=True)

    # Plain-language reason shown to the learner when status is NEEDS_INPUT.
    needs_input_reason = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    course = relationship("Course", back_populates="documents")


class StorageUploadIntent(Base):
    """A one-use, owner-scoped authorization to finalize a private upload."""

    __tablename__ = "storage_upload_intents"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    object_key = Column(String(512), nullable=False, unique=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    role = Column(String(16), nullable=False)
    replaces_document_id = Column(Uuid, nullable=True, index=True)
    replaced_filename = Column(String(255), nullable=True)
    retired_cleanup_task_id = Column(Uuid, nullable=True)
    upload_cleanup_task_id = Column(Uuid, nullable=True)
    expected_checksum_sha256 = Column(String(64), nullable=False)
    expected_size_bytes = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    finalized = Column(Boolean, nullable=False, default=False)
    source_change_applied = Column(Boolean, nullable=False, default=False, server_default="false")
    finalized_document_id = Column(Uuid, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
