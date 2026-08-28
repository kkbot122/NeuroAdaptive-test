import json
import uuid

import pytest

from app.modules.adaptation.models import AdaptationDecision
from app.modules.adaptation.service import AdaptationNotFound, AdaptationPersistenceError, AdaptationService
from app.modules.courses.models import Course
from app.modules.curriculum.models import (
    Concept,
    ConceptPrerequisite,
    CourseVersion,
    CourseVersionStatus,
    EdgeStrength,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.mastery.models import MasteryEvent
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway

# Known archetype/style-label vocabulary this phase must never emit anywhere
# in a recommendation response, a reason string, or a persisted decision.
BANNED_LABEL_TERMS = [
    "THE_PIONEER", "THE_VISUAL_ARCHITECT", "THE_DEEP_SCHOLAR", "THE_STRATEGIC_SKIMMER",
    "THE_LOGICAL_TINKERER", "THE_ADAPTIVE_GENERALIST", "THE_VISUALIZER", "THE_ARCHITECT",
    "THE_SPRINTER", "THE_DEBUGGER", "visual learner", "auditory learner", "kinesthetic learner",
]


def make_service(db_session):
    return AdaptationService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())


def set_mastery(db_session, owner, concept_id, mastery):
    """A single very-high-weight event pins mastery near `mastery` with low
    uncertainty -- a convenient fixture, not a claim about realistic evidence."""
    db_session.add(
        MasteryEvent(
            owner_id=owner.id, concept_id=concept_id, course_id=uuid.uuid4(), course_version_id=uuid.uuid4(),
            correctness=mastery, evidence_weight_base=1000.0,
        )
    )
    db_session.commit()


@pytest.fixture()
def course_setup(db_session, owner):
    """course -> version -> concept_x (dependent) --HARD--> concept_y
    (prerequisite), plus a third concept in its own ready lesson so there's
    always a NEW_LESSON/TARGETED_PRACTICE candidate alongside remediation."""
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()

    concept_x = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="x", name="Deadlock Detection", definition="def x", importance=0.8,
    )
    concept_y = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="y", name="Mutual Exclusion", definition="def y", importance=0.8,
    )
    concept_z = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="z", name="Scheduling", definition="def z", importance=0.5,
    )
    db_session.add_all([concept_x, concept_y, concept_z])
    db_session.flush()

    db_session.add(
        ConceptPrerequisite(
            course_id=course.id, course_version_id=version.id,
            prerequisite_concept_id=concept_y.id, dependent_concept_id=concept_x.id,
            strength=EdgeStrength.HARD.value,
        )
    )

    module = Module(course_version_id=version.id, position=0, title="Module 1")
    db_session.add(module)
    db_session.flush()
    lesson_z = Lesson(module_id=module.id, position=0, title="Scheduling Basics")
    db_session.add(lesson_z)
    db_session.flush()
    db_session.add(LessonConcept(lesson_id=lesson_z.id, concept_id=concept_z.id))
    db_session.commit()

    course.active_version_id = version.id
    db_session.commit()
    db_session.refresh(course)
    return course, version, concept_x, concept_y, concept_z


class TestRemediationTrigger:
    def test_weak_hard_prerequisite_triggers_remediation_that_outranks_a_peer(
        self, db_session, owner, course_setup
    ):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.45)  # dependent drops below 0.5
        set_mastery(db_session, owner, concept_y.id, 0.5)  # hard prerequisite below 0.6
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        types = [result.recommended["activity_type"]] + [a["activity_type"] for a in result.alternatives]
        assert "PREREQUISITE_REMEDIATION" in types
        # The remediation bonus is designed to make it outrank ordinary candidates.
        assert result.recommended["activity_type"] == "PREREQUISITE_REMEDIATION"

    def test_strong_prerequisite_does_not_trigger_remediation(self, db_session, owner, course_setup):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.45)  # still drops
        set_mastery(db_session, owner, concept_y.id, 0.8)  # but prerequisite is solid
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        types = [result.recommended["activity_type"]] + [a["activity_type"] for a in result.alternatives]
        assert "PREREQUISITE_REMEDIATION" not in types


class TestDecisionLogging:
    def test_every_call_persists_a_decision_before_returning(self, db_session, owner, course_setup):
        course, *_ = course_setup
        service = make_service(db_session)
        before = db_session.query(AdaptationDecision).count()
        service.recommend_next(course.id, owner.id)
        after = db_session.query(AdaptationDecision).count()
        assert after == before + 1

    def test_persistence_failure_prevents_any_recommendation(self, db_session, owner, course_setup, monkeypatch):
        course, *_ = course_setup
        service = make_service(db_session)

        def failing_commit():
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(db_session, "commit", failing_commit)
        with pytest.raises(AdaptationPersistenceError):
            service.recommend_next(course.id, owner.id)

    def test_candidates_considered_includes_every_scored_candidate_not_only_the_winner(
        self, db_session, owner, course_setup
    ):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.45)
        set_mastery(db_session, owner, concept_y.id, 0.5)
        # concept_z fully mastered -> a CHALLENGE candidate too, for a third distinct candidate.
        set_mastery(db_session, owner, concept_z.id, 0.95)
        service = make_service(db_session)

        service.recommend_next(course.id, owner.id)
        decision = db_session.query(AdaptationDecision).order_by(AdaptationDecision.created_at.desc()).first()
        assert len(decision.candidates_considered) >= 3
        selected_count = sum(1 for c in decision.candidates_considered if c["selected"])
        assert selected_count == 1


class TestGuardrailNoFixedLabels:
    def test_no_banned_vocabulary_anywhere_in_the_response_or_decision(self, db_session, owner, course_setup):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.45)
        set_mastery(db_session, owner, concept_y.id, 0.5)
        set_mastery(db_session, owner, concept_z.id, 0.95)
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        decision = db_session.query(AdaptationDecision).order_by(AdaptationDecision.created_at.desc()).first()

        haystack = json.dumps(
            {
                "recommended": result.recommended,
                "alternatives": result.alternatives,
                "reason_text": decision.reason_text,
                "candidates_considered": decision.candidates_considered,
            }
        )
        for term in BANNED_LABEL_TERMS:
            assert term.lower() not in haystack.lower()


class TestOwnership:
    def test_another_users_course_is_not_found(self, db_session, other_user, course_setup):
        course, *_ = course_setup
        service = make_service(db_session)
        with pytest.raises(AdaptationNotFound):
            service.recommend_next(course.id, other_user.id)

    def test_ungenerated_course_is_not_found(self, db_session, owner):
        course = Course(owner_id=owner.id, title="Empty")
        db_session.add(course)
        db_session.commit()
        db_session.refresh(course)
        service = make_service(db_session)
        with pytest.raises(AdaptationNotFound):
            service.recommend_next(course.id, owner.id)
