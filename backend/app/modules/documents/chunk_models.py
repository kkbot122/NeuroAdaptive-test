import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import DateTime

from app.db.base import Base


class Chunk(Base):
    """
    A structure-aware slice of one document, with provenance.

    heading_path preserves the section a chunk came from ("2. Parsing > 2.1
    LL(1)"), so a citation can name where in the document a claim was
    supported, not just which file.

    owner_id and course_id are denormalised so the retrieval filter can be
    applied *inside* the query, which the mandate requires, rather than
    joining through documents on every search.
    """

    __tablename__ = "chunks"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    position = Column(Integer, nullable=False, default=0)
    heading_path = Column(String(500), nullable=True)
    content_type = Column(String(32), nullable=False, default="prose")

    text = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False, default=0)

    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)

    # Set once the chunk is embedded and upserted into Qdrant. Null means the
    # chunk exists but is not yet retrievable.
    embedding_model = Column(String(64), nullable=True)
    indexed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document")
