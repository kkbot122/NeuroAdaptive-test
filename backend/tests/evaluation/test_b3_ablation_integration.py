"""
B3 ablation exercised end to end through TutorService.ask() itself (not
just a signature check) -- a fabricated citation passes through untouched
when citation_validation_enabled=False, tagged DISABLED so it can never be
confused with a real PASSED validation.
"""
import uuid

from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.tutor.service import TutorService
from app.modules.tutor.validation import ValidationStatus
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from app.services.vectorstore.fake import FakeVectorStore


def make_service(db_session, generation):
    return TutorService(db_session, generation, FakeEmbeddingGateway(), FakeVectorStore())


class TestB3DisablesRealValidation:
    def test_a_fabricated_chunk_id_passes_through_when_validation_is_disabled(self, db_session, owner):
        course = Course(owner_id=owner.id, title="OS Course")
        db_session.add(course)
        db_session.commit()
        doc = Document(
            course_id=course.id, owner_id=owner.id, filename="notes.txt",
            storage_path="/dev/null", checksum_sha256="a" * 64,
        )
        db_session.add(doc)
        db_session.commit()
        chunk = Chunk(
            id=uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id,
            text="A deadlock is a circular wait condition.",
        )
        db_session.add(chunk)
        db_session.commit()

        fabricated_chunk_id = str(uuid.uuid4())  # never a real chunk
        gen = FakeGenerationGateway().set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{fabricated_chunk_id}"}}]}}'
        )
        service = make_service(db_session, gen)

        # With validation ON (the real product path): rejected.
        on_result = service.ask(course.id, owner.id, "What is a deadlock?", citation_validation_enabled=True)
        assert on_result.citations == []

        # With validation OFF (B3): passes through, but tagged DISABLED --
        # never PASSED, so exported metrics can't mistake this for a real check.
        off_result = service.ask(course.id, owner.id, "What is a deadlock?", citation_validation_enabled=False)
        assert len(off_result.citations) == 1
        assert off_result.citations[0].chunk_id == fabricated_chunk_id
        assert off_result.citations[0].validation_status == ValidationStatus.DISABLED
