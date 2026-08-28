import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.tutor.validation import Claim, ValidationStatus, tier1_validate, validate_claims


@pytest.fixture()
def two_courses_with_chunks(db_session, owner):
    course_a = Course(owner_id=owner.id, title="Course A")
    course_b = Course(owner_id=owner.id, title="Course B")
    db_session.add_all([course_a, course_b])
    db_session.commit()

    doc_a = Document(course_id=course_a.id, owner_id=owner.id, filename="a.txt", storage_path="/dev/null", checksum_sha256="a" * 64)
    doc_b = Document(course_id=course_b.id, owner_id=owner.id, filename="b.txt", storage_path="/dev/null", checksum_sha256="b" * 64)
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    chunk_a = Chunk(id=uuid.uuid4(), document_id=doc_a.id, course_id=course_a.id, owner_id=owner.id, text="Chunk A text")
    chunk_b = Chunk(id=uuid.uuid4(), document_id=doc_b.id, course_id=course_b.id, owner_id=owner.id, text="Chunk B text")
    db_session.add_all([chunk_a, chunk_b])
    db_session.commit()
    return course_a, course_b, chunk_a, chunk_b


class TestTier1Structural:
    def test_fabricated_chunk_id_fails(self, db_session, owner, two_courses_with_chunks):
        course_a, course_b, chunk_a, chunk_b = two_courses_with_chunks
        claim = Claim(text="x", chunk_id=str(uuid.uuid4()))
        assert tier1_validate(db_session, claim, course_a.id, owner.id) is False

    def test_real_chunk_from_a_different_course_fails(self, db_session, owner, two_courses_with_chunks):
        course_a, course_b, chunk_a, chunk_b = two_courses_with_chunks
        claim = Claim(text="x", chunk_id=str(chunk_b.id))
        assert tier1_validate(db_session, claim, course_a.id, owner.id) is False

    def test_correctly_scoped_chunk_passes(self, db_session, owner, two_courses_with_chunks):
        course_a, course_b, chunk_a, chunk_b = two_courses_with_chunks
        claim = Claim(text="x", chunk_id=str(chunk_a.id))
        assert tier1_validate(db_session, claim, course_a.id, owner.id) is True

    def test_non_uuid_chunk_id_fails_safely(self, db_session, owner, two_courses_with_chunks):
        course_a, *_ = two_courses_with_chunks
        claim = Claim(text="x", chunk_id="not-a-uuid")
        assert tier1_validate(db_session, claim, course_a.id, owner.id) is False

    def test_a_chunk_owned_by_someone_else_fails(self, db_session, owner, other_user, two_courses_with_chunks):
        course_a, course_b, chunk_a, chunk_b = two_courses_with_chunks
        claim = Claim(text="x", chunk_id=str(chunk_a.id))
        assert tier1_validate(db_session, claim, course_a.id, other_user.id) is False


class TestTier2Semantic:
    def test_supported_claim_passes(self, db_session, owner, two_courses_with_chunks):
        course_a, _, chunk_a, _ = two_courses_with_chunks
        claims = [Claim(text="claim", chunk_id=str(chunk_a.id))]
        results = validate_claims(
            db_session, claims, course_a.id, owner.id, {str(chunk_a.id): "source"},
            entailment_checker=lambda c, s: True,
        )
        assert results[0].tier2_status == ValidationStatus.PASSED

    def test_unsupported_claim_fails(self, db_session, owner, two_courses_with_chunks):
        course_a, _, chunk_a, _ = two_courses_with_chunks
        claims = [Claim(text="claim", chunk_id=str(chunk_a.id))]
        results = validate_claims(
            db_session, claims, course_a.id, owner.id, {str(chunk_a.id): "source"},
            entailment_checker=lambda c, s: False,
        )
        assert results[0].tier2_status == ValidationStatus.FAILED

    def test_tier1_failure_short_circuits_tier2(self, db_session, owner, two_courses_with_chunks):
        course_a, *_ = two_courses_with_chunks

        def boom(claim, source):
            raise AssertionError("tier2 must not run when tier1 already failed")

        claims = [Claim(text="claim", chunk_id=str(uuid.uuid4()))]
        results = validate_claims(db_session, claims, course_a.id, owner.id, {}, entailment_checker=boom)
        assert results[0].tier1_passed is False
        assert results[0].tier2_status == ValidationStatus.UNSAMPLED

    def test_sampling_skips_non_sampled_claims(self, db_session, owner, two_courses_with_chunks):
        course_a, _, chunk_a, _ = two_courses_with_chunks
        claims = [Claim(text=f"claim {i}", chunk_id=str(chunk_a.id)) for i in range(4)]
        calls = []

        def checker(c, s):
            calls.append(c)
            return True

        results = validate_claims(
            db_session, claims, course_a.id, owner.id, {str(chunk_a.id): "source"}, entailment_checker=checker,
            sample_every=2,
        )
        sampled_statuses = [r.tier2_status for r in results]
        assert sampled_statuses == [
            ValidationStatus.PASSED, ValidationStatus.UNSAMPLED, ValidationStatus.PASSED, ValidationStatus.UNSAMPLED,
        ]
        assert len(calls) == 2
