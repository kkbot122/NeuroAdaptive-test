import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Chunk(Base):
    """
    A structure-aware slice of one document, with provenance.

    heading_path preserves the section a chunk came from ("2. Parsing > 2.1
    LL(1)"), so a citation can name where in the document a claim was
    supported, not just which file. char_start/char_end are offsets into the
    document's concatenated extracted text, so a citation can point at an
    exact span, not just a page.

    owner_id and course_id are denormalised so the retrieval filter can be
    applied *inside* the query, which the mandate requires, rather than
    joining through documents on every search.

    id is NOT a random default here -- the job runner assigns a deterministic
    uuid5 derived from (document_id, extraction_version, position), so a
    retried job overwrites identically instead of duplicating, and a citation
    stored elsewhere keeps pointing at the same chunk across a reprocess with
    the same extraction_version.
    """

    __tablename__ = "chunks"

    id = Column(Uuid, primary_key=True)
    document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    position = Column(Integer, nullable=False, default=0)
    heading_path = Column(String(500), nullable=True)
    content_type = Column(String(32), nullable=False, default="prose")

    text = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False, default=0)
    token_count = Column(Integer, nullable=False, default=0)

    # Offsets into the document's concatenated extracted text (all pages
    # joined). Together with page_start/page_end this is what makes a chunk
    # citeable: exactly where a claim came from, not just which document.
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)

    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)

    # The extraction/chunking algorithm version that produced this chunk.
    # Lets a reprocess under a new algorithm coexist logically with (and then
    # replace) chunks from an old one, and is one of the three inputs to the
    # deterministic id.
    extraction_version = Column(Integer, nullable=False, default=1)

    # Set once the chunk is embedded and upserted into Qdrant. Null means the
    # chunk exists but is not yet retrievable.
    embedding_model = Column(String(64), nullable=True)
    indexed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document")
