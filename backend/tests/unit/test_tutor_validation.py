import uuid
from datetime import datetime, timezone

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import CourseVersion, CourseVersionStatus
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

    def test_every_chunk_in_a_multi_source_claim_must_be_owned(self, db_session, owner, two_courses_with_chunks):
        course_a, _course_b, chunk_a, chunk_b = two_courses_with_chunks
        claim = Claim(
            text="x",
            chunk_id=str(chunk_a.id),
            source_chunk_ids=(str(chunk_a.id), str(chunk_b.id)),
        )
        assert tier1_validate(db_session, claim, course_a.id, owner.id) is False

    def test_explicit_pinned_link_authorizes_its_owned_published_source(self, db_session, owner, two_courses_with_chunks):
        from app.modules.courses.models import CourseStatus

        current, earlier, _current_chunk, earlier_chunk = two_courses_with_chunks
        earlier.status = CourseStatus.PUBLISHED.value
        earlier_version = CourseVersion(
            course_id=earlier.id,
            owner_id=owner.id,
            version_number=1,
            status=CourseVersionStatus.READY.value,
            source_fingerprint="a" * 64,
        )
        db_session.add(earlier_version)
        db_session.flush()
        earlier.active_version_id = earlier_version.id
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        db_session.commit()

        claim = Claim(text="supported from Unit 1", chunk_id=str(earlier_chunk.id))

        assert tier1_validate(db_session, claim, current.id, owner.id) is True

    def test_linked_source_requires_the_exact_live_pinned_version(self, db_session, owner, two_courses_with_chunks):
        from app.modules.courses.models import CourseStatus

        current, earlier, _current_chunk, earlier_chunk = two_courses_with_chunks
        earlier.status = CourseStatus.PUBLISHED.value
        pinned = CourseVersion(
            course_id=earlier.id, owner_id=owner.id, version_number=1,
            status=CourseVersionStatus.READY.value, source_fingerprint="a" * 64,
        )
        newer = CourseVersion(
            course_id=earlier.id, owner_id=owner.id, version_number=2,
            status=CourseVersionStatus.READY.value, source_fingerprint="b" * 64,
        )
        db_session.add_all([pinned, newer])
        db_session.flush()
        earlier.active_version_id = newer.id
        current.linked_course_id = earlier.id
        current.linked_version_id = pinned.id
        current.link_revision = 1
        db_session.commit()

        claim = Claim(text="pinned source", chunk_id=str(earlier_chunk.id))
        assert tier1_validate(db_session, claim, current.id, owner.id) is True

        current.link_revoked_at = datetime.now(timezone.utc)
        db_session.commit()
        assert tier1_validate(db_session, claim, current.id, owner.id) is False
    def test_validate_claims_keeps_linked_citations_in_the_shared_grounding_path(
        self, db_session, owner, two_courses_with_chunks
    ):
        from app.modules.courses.models import CourseStatus

        current, earlier, _current_chunk, earlier_chunk = two_courses_with_chunks
        earlier.status = CourseStatus.PUBLISHED.value
        earlier_version = CourseVersion(
            course_id=earlier.id, owner_id=owner.id, version_number=1,
            status=CourseVersionStatus.READY.value, source_fingerprint="c" * 64,
        )
        db_session.add(earlier_version)
        db_session.flush()
        earlier.active_version_id = earlier_version.id
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        db_session.commit()

        source_id = str(earlier_chunk.id)
        results = validate_claims(
            db_session, [Claim(text="linked claim", chunk_id=source_id)], current.id, owner.id,
            {source_id: earlier_chunk.text}, entailment_checker=lambda _claim, _source: True,
        )

        assert results[0].tier1_passed is True
        assert results[0].tier2_status == ValidationStatus.PASSED


class TestTier2Semantic:
    def test_default_checks_every_claim(self, db_session, owner, two_courses_with_chunks):
        course_a, _, chunk_a, _ = two_courses_with_chunks
        claims = [Claim(text=f"claim {i}", chunk_id=str(chunk_a.id)) for i in range(4)]
        calls = []

        def checker(claim, source):
            calls.append(claim)
            return claim != "claim 1"

        results = validate_claims(db_session, claims, course_a.id, owner.id,
                                  {str(chunk_a.id): "source"}, entailment_checker=checker)
        assert calls == [claim.text for claim in claims]
        assert [result.tier2_status for result in results] == ["passed", "failed", "passed", "passed"]

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
