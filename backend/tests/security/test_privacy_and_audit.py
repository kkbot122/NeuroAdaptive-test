"""
Privacy (consent degradation, account deletion, presentation-affinity
reset) and audit logging, exercised end to end -- not just implemented.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.modules.abuse.models import AIUsageDaily
from app.modules.adaptation.models import AdaptationDecision, PresentationAffinity
from app.modules.audit.models import AuditLog
from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus, Lesson, Module
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.learning.models import (
    ActivityStatus,
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    GradingCorrection,
    GradingIssueReport,
    GradingJudgment,
    GradingReviewEvent,
    LearningActivity,
)
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    LessonContentCitation,
    PreparedActivityQuestion,
    QuestionSource,
)
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
        started = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()
        # /process returns 202 with the job PENDING and runs the pipeline via
        # BackgroundTasks (jobs/router.py) rather than inline -- TestClient
        # still runs it to completion before this call returns, so the
        # immediate follow-up GET already sees the terminal status.
        process = client.get(
            f"/api/v1/jobs/{started['id']}", headers=auth_headers(owner.email)
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
    def test_deleting_reviewer_account_anonymizes_audit_actor_and_keeps_effective_correction(
        self, client, owner, db_session
    ):
        from app.modules.auth.models import User

        reviewer = User(email="reviewer-to-delete@example.com", full_name="Reviewer", is_active=True)
        db_session.add(reviewer)
        db_session.flush()
        reviewer_id = reviewer.id
        course = Course(owner_id=owner.id, title="Reviewer retention course")
        db_session.add(course)
        db_session.flush()
        version = CourseVersion(
            course_id=course.id,
            owner_id=owner.id,
            version_number=1,
            status=CourseVersionStatus.READY.value,
        )
        db_session.add(version)
        db_session.flush()
        concept = Concept(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            canonical_key="reviewer-retention",
            name="Reviewer retention",
            definition="A reviewer account deletion test concept.",
            importance=0.5,
        )
        db_session.add(concept)
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="TARGETED_PRACTICE",
            target_concept_ids=[str(concept.id)],
            status=ActivityStatus.COMPLETED.value,
            presentation_format="concise",
        )
        db_session.add(activity)
        db_session.flush()
        question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="SHORT_TEXT",
            prompt="Explain the supported idea.",
            options=None,
            correct_answer=None,
            rubric=["Names the idea", "Explains its feature", "Connects it to the source"],
            rubric_passing_criteria=2,
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="p5-reviewer-deletion-v1",
        )
        db_session.add(question)
        db_session.flush()
        session = AssessmentSession(
            activity_id=activity.id,
            course_version_id=version.id,
            assessment_type="ACTIVITY",
            status="SUBMITTED",
        )
        db_session.add(session)
        db_session.flush()
        assessment_question = AssessmentQuestion(
            session_id=session.id,
            question_id=question.id,
            question_version=question.version,
            position=0,
        )
        db_session.add(assessment_question)
        db_session.flush()
        answer = AnswerSubmission(assessment_question_id=assessment_question.id, given_answer="Saved answer", status="GRADED")
        db_session.add(answer)
        db_session.flush()
        attempt = QuestionAttempt(
            assessment_question_id=assessment_question.id,
            question_id=question.id,
            question_version=question.version,
            owner_id=owner.id,
            course_id=course.id,
            given_answer=answer.given_answer,
            correctness=0.0,
        )
        db_session.add(attempt)
        db_session.flush()
        db_session.add(MasteryEvent(
            owner_id=owner.id,
            concept_id=concept.id,
            course_id=course.id,
            course_version_id=version.id,
            question_attempt_id=attempt.id,
            correctness=0.0,
            evidence_weight_base=1.0,
        ))
        report = GradingIssueReport(
            answer_submission_id=answer.id,
            owner_id=owner.id,
            course_id=course.id,
            report_text="Please review this judgment.",
            status="CORRECTED",
        )
        db_session.add(report)
        db_session.flush()
        db_session.add_all([
            GradingReviewEvent(
                report_id=report.id,
                reviewer_id=reviewer.id,
                event_type="CORRECTED",
                reason="Approved correction.",
                correction_version=1,
            ),
            GradingCorrection(
                report_id=report.id,
                question_attempt_id=attempt.id,
                reviewer_id=reviewer.id,
                version=1,
                criteria_met=[True, True, False],
                rubric_score=2,
                effective_correctness=1,
                reason="Approved correction.",
            ),
        ])
        db_session.commit()
        report_id = report.id
        attempt_id = attempt.id

        response = client.delete("/api/v1/me", headers=auth_headers(reviewer.email))
        assert response.status_code == 202
        assert db_session.get(User, reviewer_id) is None
        assert db_session.query(GradingIssueReport).filter_by(id=report_id).count() == 1
        assert db_session.query(GradingReviewEvent).filter_by(report_id=report_id).one().reviewer_id is None
        correction = db_session.query(GradingCorrection).filter_by(report_id=report_id).one()
        assert correction.reviewer_id is None
        assert correction.effective_correctness == 1
        assert db_session.query(QuestionAttempt).filter_by(id=attempt_id).one().correctness == 0.0

    def test_account_deletion_removes_short_answer_review_and_correction_history(
        self, client, owner, db_session
    ):
        course = Course(owner_id=owner.id, title="Review retention course")
        db_session.add(course)
        db_session.flush()
        version = CourseVersion(
            course_id=course.id,
            owner_id=owner.id,
            version_number=1,
            status=CourseVersionStatus.READY.value,
        )
        db_session.add(version)
        db_session.flush()
        concept = Concept(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            canonical_key="review-retention",
            name="Review retention",
            definition="A deletion test concept.",
            importance=0.5,
        )
        db_session.add(concept)
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="TARGETED_PRACTICE",
            target_concept_ids=[],
            status=ActivityStatus.COMPLETED.value,
            presentation_format="concise",
        )
        db_session.add(activity)
        db_session.flush()
        question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="SHORT_TEXT",
            prompt="Explain the concept.",
            options=None,
            correct_answer=None,
            rubric=["Explains the supported idea."],
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="p5-retention-test-v1",
        )
        db_session.add(question)
        db_session.flush()
        session = AssessmentSession(
            activity_id=activity.id,
            course_version_id=version.id,
            assessment_type="ACTIVITY",
            status="SUBMITTED",
        )
        db_session.add(session)
        db_session.flush()
        item = AssessmentQuestion(
            session_id=session.id,
            question_id=question.id,
            question_version=question.version,
            position=0,
        )
        db_session.add(item)
        db_session.flush()
        answer = AnswerSubmission(
            assessment_question_id=item.id,
            given_answer="A saved learner answer.",
            status="GRADED",
        )
        db_session.add(answer)
        db_session.flush()
        attempt = QuestionAttempt(
            assessment_question_id=item.id,
            question_id=question.id,
            question_version=question.version,
            owner_id=owner.id,
            course_id=course.id,
            given_answer=answer.given_answer,
            correctness=1.0,
        )
        db_session.add(attempt)
        db_session.flush()
        db_session.add(MasteryEvent(
            owner_id=owner.id,
            concept_id=concept.id,
            course_id=course.id,
            course_version_id=version.id,
            question_attempt_id=attempt.id,
            correctness=1.0,
            evidence_weight_base=1.0,
        ))
        report = GradingIssueReport(
            answer_submission_id=answer.id,
            owner_id=owner.id,
            course_id=course.id,
            report_text="Please review this answer.",
            status="CORRECTED",
        )
        db_session.add_all([
            report,
            GradingJudgment(
                answer_submission_id=answer.id,
                criteria_met=[True],
                rubric_score=1,
                evidence_correctness=1,
                policy_version="p5-rubric-binary-evidence-v1",
                model_id="fixture",
            ),
        ])
        db_session.flush()
        db_session.add_all([
            GradingReviewEvent(
                report_id=report.id,
                reviewer_id=owner.id,
                event_type="CORRECTED",
                reason="Saved correction for retention test.",
                correction_version=1,
            ),
            GradingCorrection(
                report_id=report.id,
                question_attempt_id=attempt.id,
                reviewer_id=owner.id,
                version=1,
                criteria_met=[False],
                rubric_score=0,
                effective_correctness=0,
                reason="Saved correction for retention test.",
            ),
        ])
        db_session.commit()

        response = client.delete("/api/v1/me", headers=auth_headers(owner.email))
        assert response.status_code == 202
        assert db_session.query(GradingReviewEvent).count() == 0
        assert db_session.query(GradingCorrection).count() == 0
        assert db_session.query(GradingJudgment).count() == 0
        assert db_session.query(GradingIssueReport).count() == 0
        assert db_session.query(AnswerSubmission).count() == 0
        assert db_session.query(QuestionAttempt).count() == 0
        assert db_session.query(MasteryEvent).count() == 0

    def test_deletion_removes_p2_preparation_content_and_question_provenance(
        self, client, owner, db_session, owner_with_full_footprint
    ):
        course, concept = owner_with_full_footprint
        version = db_session.query(CourseVersion).filter_by(course_id=course.id).one()
        chunk = db_session.query(Chunk).filter_by(course_id=course.id).one()
        module = Module(course_version_id=version.id, position=0, title="Module")
        db_session.add(module)
        db_session.flush()
        lesson = Lesson(module_id=module.id, position=0, title="Lesson", objective="Teach the concept")
        db_session.add(lesson)
        db_session.flush()
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept.id)],
            lesson_id=lesson.id,
            status=ActivityStatus.READY.value,
            presentation_format="detailed",
        )
        db_session.add(activity)
        db_session.flush()
        artifact = LessonContentArtifact(
            artifact_key="a" * 64,
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            lesson_id=lesson.id,
            source_fingerprint="b" * 64,
            curriculum_fingerprint="c" * 64,
            presentation_format="detailed",
            sections={"objective": [], "explanation": [], "example": [], "recap": []},
            source_chunk_ids=[str(chunk.id)],
            model_id="fixture-model",
            validation_model_id="fixture-model",
            prompt_version="p2-lesson-content-v1",
            schema_version="p2-lesson-content-schema-v1",
            validation_policy_version="p2-grounding-validation-v1",
            validated_at=datetime.now(timezone.utc),
        )
        db_session.add(artifact)
        db_session.flush()
        preparation = ActivityPreparation(
            preparation_key=f"activity:{activity.id}:default",
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_id=activity.id,
            lesson_id=lesson.id,
            presentation_format="detailed",
            include_assessment=True,
            status="READY",
            stage="COMPLETE",
            progress=100,
            artifact_keys={"lesson_content": str(artifact.id)},
            content_artifact_id=artifact.id,
        )
        p4_activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="PREREQUISITE_REMEDIATION",
            target_concept_ids=[str(concept.id)],
            lesson_id=None,
            reason_text="The selected concept has demonstrated weak evidence.",
            status=ActivityStatus.COMPLETED.value,
            presentation_format="worked_example",
        )
        db_session.add(p4_activity)
        db_session.flush()
        p4_artifact = LessonContentArtifact(
            artifact_key="e" * 64,
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            lesson_id=None,
            activity_purpose="PREREQUISITE_REMEDIATION",
            target_concept_ids=[str(concept.id)],
            source_fingerprint="b" * 64,
            curriculum_fingerprint="f" * 64,
            presentation_format="worked_example",
            sections={"objective": [], "explanation": [], "example": [], "recap": []},
            source_chunk_ids=[str(chunk.id)],
            model_id="fixture-model",
            validation_model_id="fixture-model",
            prompt_version="p4-remediation-content-v1",
            schema_version="p4-remediation-content-schema-v1",
            validation_policy_version="p4-grounding-freshness-v1",
            validated_at=datetime.now(timezone.utc),
        )
        db_session.add(p4_artifact)
        db_session.flush()
        p4_preparation = ActivityPreparation(
            preparation_key=f"activity:{p4_activity.id}:PREREQUISITE_REMEDIATION:targeted:default",
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_id=p4_activity.id,
            lesson_id=None,
            activity_purpose="PREREQUISITE_REMEDIATION",
            target_concept_ids=[str(concept.id)],
            presentation_format="worked_example",
            include_assessment=True,
            status="READY",
            stage="COMPLETE",
            progress=100,
            artifact_keys={"lesson_content": str(p4_artifact.id)},
            content_artifact_id=p4_artifact.id,
        )
        question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="MCQ",
            prompt="Which answer is supported by the source passage?",
            options=["Supported", "Other A", "Other B", "Other C"],
            correct_answer="Supported",
            explanation="The source passage supports this answer.",
            content_hash="d" * 64,
            schema_version="p2-lesson-mcq-schema-v1",
            validation_policy_version="p2-grounding-validation-v1",
            difficulty=0.5,
            is_diagnostic=0,
            model_id="fixture-model",
            prompt_version="p2-lesson-mcq-v1",
        )
        db_session.add_all([preparation, question])
        db_session.flush()
        p4_question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="MCQ",
            prompt="Which source-supported idea needs attention?",
            options=["Supported idea", "Other A", "Other B", "Other C"],
            correct_answer="Supported idea",
            explanation="The source passage supports this idea.",
            content_hash="f" * 64,
            schema_version="p4-activity-mcq-schema-v1",
            validation_policy_version="p4-grounding-freshness-v1",
            difficulty=0.5,
            is_diagnostic=0,
            model_id="fixture-model",
            prompt_version="p4-activity-mcq-v1",
        )
        db_session.add_all([p4_preparation, p4_question])
        db_session.flush()
        db_session.add_all(
            [
                LessonContentCitation(artifact_id=artifact.id, chunk_id=chunk.id),
                PreparedActivityQuestion(
                    preparation_id=preparation.id,
                    question_id=question.id,
                    question_version=question.version,
                    position=0,
                ),
                QuestionSource(question_id=question.id, chunk_id=chunk.id),
                LessonContentCitation(artifact_id=p4_artifact.id, chunk_id=chunk.id),
                PreparedActivityQuestion(
                    preparation_id=p4_preparation.id,
                    question_id=p4_question.id,
                    question_version=p4_question.version,
                    position=0,
                ),
                QuestionConcept(question_id=p4_question.id, concept_id=concept.id, weight=1.0),
                QuestionSource(question_id=p4_question.id, chunk_id=chunk.id),
            ]
        )
        db_session.commit()
        preparation_id = preparation.id
        artifact_id = artifact.id
        question_id = question.id
        p4_preparation_id = p4_preparation.id
        p4_artifact_id = p4_artifact.id
        p4_question_id = p4_question.id

        response = client.delete("/api/v1/me", headers=auth_headers(owner.email))
        assert response.status_code == 202
        assert db_session.query(ActivityPreparation).filter_by(id=preparation_id).count() == 0
        assert db_session.query(LessonContentArtifact).filter_by(id=artifact_id).count() == 0
        assert db_session.query(LessonContentCitation).filter_by(artifact_id=artifact_id).count() == 0
        assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation_id).count() == 0
        assert db_session.query(QuestionSource).filter_by(question_id=question_id).count() == 0
        assert db_session.query(ActivityPreparation).filter_by(id=p4_preparation_id).count() == 0
        assert db_session.query(LessonContentArtifact).filter_by(id=p4_artifact_id).count() == 0
        assert db_session.query(LessonContentCitation).filter_by(artifact_id=p4_artifact_id).count() == 0
        assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=p4_preparation_id).count() == 0
        assert db_session.query(QuestionSource).filter_by(question_id=p4_question_id).count() == 0

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
