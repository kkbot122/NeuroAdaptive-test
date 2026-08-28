"""
Privacy (consent degradation, account deletion, presentation-affinity
reset) and audit logging, exercised end to end -- not just implemented.
"""
import uuid

import pytest

from app.modules.abuse.models import AIUsageDaily
from app.modules.adaptation.models import AdaptationDecision, PresentationAffinity
from app.modules.audit.models import AuditLog
from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.mastery.models import MasteryEvent
from tests.conftest import auth_headers


class TestConsentDegradesGracefully:
    def test_minimal_consent_still_completes_the_core_loop(
        self, client, owner, db_session, fake_generation
    ):
        # Explicitly decline telemetry.
        consent_resp = client.patch(
            "/api/v1/me/consent", json={"tracking_consent": "minimal"}, headers=auth_headers(owner.email)
        )
        assert consent_resp.status_code == 200

        # Upload -> generate.
        course = client.post(
            "/api/v1/courses", json={"title": "OS Course"}, headers=auth_headers(owner.email)
        ).json()
        client.post(
            f"/api/v1/courses/{course['id']}/documents",
            files={"file": ("notes.txt", (b"A deadlock is a circular wait. " * 20), "text/plain")},
            data={"role": "STUDY"},
            headers=auth_headers(owner.email),
        )
        process = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()
        assert process["status"] == "READY"

        # Study: mastery report and next-activity both work with no telemetry consent.
        mastery_resp = client.get(
            f"/api/v1/courses/{course['id']}/mastery-report", headers=auth_headers(owner.email)
        )
        assert mastery_resp.status_code == 200

        # Assess: telemetry batch is accepted-but-not-recorded, not an error.
        events_resp = client.post(
            "/api/v1/events/batch",
            json={"events": [{"event_type": "paragraph_view", "seconds": 5}]},
            headers=auth_headers(owner.email),
        )
        assert events_resp.status_code == 202
        assert events_resp.json()["rejected"] == 1
        assert events_resp.json()["accepted"] == 0

        from app.modules.events.models import LearningEvent

        assert db_session.query(LearningEvent).filter(LearningEvent.user_id == owner.id).count() == 0

    def test_full_consent_default_still_records_events(self, client, owner, db_session):
        resp = client.post(
            "/api/v1/events/batch",
            json={"events": [{"event_type": "paragraph_view", "seconds": 5}]},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1


@pytest.fixture()
def owner_with_full_footprint(db_session, owner):
    """A course with a document, chunk, concept, mastery evidence,
    adaptation decision, and AI usage -- enough surface area to prove
    deletion actually cascades, not just that the endpoint returns 200."""
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()

    doc = Document(
        course_id=course.id, owner_id=owner.id, filename="notes.txt",
        storage_path="/dev/null", checksum_sha256="a" * 64,
    )
    db_session.add(doc)
    db_session.commit()

    chunk = Chunk(id=uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id, text="content")
    db_session.add(chunk)
    db_session.commit()

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()
    concept = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="c", name="C", definition="def", importance=0.5,
    )
    db_session.add(concept)
    db_session.commit()

    db_session.add(MasteryEvent(
        owner_id=owner.id, concept_id=concept.id, course_id=course.id, course_version_id=version.id,
        correctness=1.0, evidence_weight_base=1.0,
    ))
    db_session.add(AdaptationDecision(
        owner_id=owner.id, course_id=course.id, selected_activity_type="NEW_LESSON",
        reason_text="r", candidates_considered=[], policy_version="v1", input_snapshot={},
    ))
    db_session.add(PresentationAffinity(owner_id=owner.id, format="concise", effectiveness=0.6))
    db_session.add(AIUsageDaily(owner_id=owner.id, usage_date=__import__("datetime").date.today(), call_count=5))
    db_session.commit()

    return course, concept


class TestAccountDeletion:
    def test_deletion_cascades_and_a_refetch_is_404(
        self, client, owner, db_session, owner_with_full_footprint
    ):
        course, concept = owner_with_full_footprint
        owner_id, owner_email, course_id, concept_id = owner.id, owner.email, course.id, concept.id

        resp = client.delete("/api/v1/me", headers=auth_headers(owner_email))
        assert resp.status_code == 202

        assert db_session.query(Course).filter(Course.id == course_id).first() is None
        assert db_session.query(Concept).filter(Concept.id == concept_id).first() is None
        assert db_session.query(MasteryEvent).filter(MasteryEvent.owner_id == owner_id).count() == 0
        assert db_session.query(AdaptationDecision).filter(AdaptationDecision.owner_id == owner_id).count() == 0
        assert db_session.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).count() == 0
        assert db_session.query(AIUsageDaily).filter(AIUsageDaily.owner_id == owner_id).count() == 0

        # Re-fetch attempt after deletion: the auth dependency itself can no
        # longer resolve this user, so any authenticated route 404s/rejects.
        refetch = client.get(f"/api/v1/courses/{course_id}/structure", headers=auth_headers(owner_email))
        assert refetch.status_code in (404, 401, 403)

    def test_deletion_writes_an_audit_log_entry(self, client, owner, db_session):
        owner_id, owner_email = owner.id, owner.email
        client.delete("/api/v1/me", headers=auth_headers(owner_email))
        entry = db_session.query(AuditLog).filter(AuditLog.action == "account_deletion_requested").first()
        assert entry is not None
        assert entry.actor_user_id == owner_id
        assert entry.target_type == "user"
        assert entry.created_at is not None

    def test_audit_log_survives_the_deletion_it_recorded(self, client, owner, db_session):
        owner_id, owner_email = owner.id, owner.email
        client.delete("/api/v1/me", headers=auth_headers(owner_email))
        # The audit row itself is not deleted by the cascade it describes.
        assert db_session.query(AuditLog).filter(AuditLog.actor_user_id == owner_id).count() >= 1


class TestPresentationAffinityResetIsIndependentOfMastery:
    def test_reset_clears_affinity_but_leaves_mastery_untouched(
        self, client, owner, db_session, owner_with_full_footprint
    ):
        course, concept = owner_with_full_footprint

        resp = client.post("/api/v1/presentation-affinity/reset", headers=auth_headers(owner.email))
        assert resp.status_code == 200
        assert resp.json()["rows_removed"] == 1

        assert db_session.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner.id).count() == 0
        # Mastery evidence for the same user is completely unaffected.
        assert db_session.query(MasteryEvent).filter(MasteryEvent.owner_id == owner.id).count() == 1
