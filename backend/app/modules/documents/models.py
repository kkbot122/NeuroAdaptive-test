import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Uuid
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

    storage_path points at backend-local disk for this sprint rather than
    object storage: boto3/minio are declared dependencies with no running
    service, and the substitution is recorded in SPRINT_LOG.md. The file is
    only ever served through an authenticated, owner-checked endpoint.
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
