import json
import uuid
from datetime import datetime, timezone

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
from app.modules.learning.models import ActivityStatus, LearningActivity
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


def set_mastery(db_session, owner, concept_id, mastery, evidence_weight=1000.0):
    """A single very-high-weight event pins mastery near `mastery` with low
    uncertainty -- a convenient fixture, not a claim about realistic evidence."""
    db_session.add(
        MasteryEvent(
            owner_id=owner.id, concept_id=concept_id, course_id=uuid.uuid4(), course_version_id=uuid.uuid4(),
            correctness=mastery, evidence_weight_base=evidence_weight,
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


def mark_taught(db_session, owner, course, version, lesson, concepts):
    activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id) for concept in concepts],
        lesson_id=lesson.id,
        status=ActivityStatus.COMPLETED.value,
        reading_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(activity)
    db_session.commit()
    return activity


class TestRemediationTrigger:
    def test_demonstrated_weak_concept_triggers_focused_remediation(
        self, db_session, owner, course_setup
    ):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_y.id, 0.2)
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        remediation = [item for item in [result.recommended, *result.alternatives]
                       if item["activity_type"] == "PREREQUISITE_REMEDIATION"]
        assert len(remediation) == 1
        assert remediation[0]["concept_ids"] == [str(concept_y.id)]
        assert "Mutual Exclusion" in remediation[0]["reason"]
        decision = db_session.query(AdaptationDecision).filter_by(id=result.decision_id).one()
        snapshot = decision.input_snapshot
        assert snapshot["concept_understanding_band"][str(concept_y.id)] == "Needs attention"
        assert snapshot["concept_evidence_strength"][str(concept_y.id)] == "More supporting evidence"
        assert snapshot["concept_evidence_weight_total"][str(concept_y.id)] >= 1.0

    def test_unassessed_prerequisite_is_not_labeled_weak_from_dependent_evidence(
        self, db_session, owner, course_setup
    ):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.2)
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        remediation = [item for item in [result.recommended, *result.alternatives]
                       if item["activity_type"] == "PREREQUISITE_REMEDIATION"]
        assert all(str(concept_y.id) not in item["concept_ids"] for item in remediation)

    def test_low_weight_wrong_evidence_is_practice_until_support_is_sufficient(
        self, db_session, owner, course_setup
    ):
        course, version, _concept_x, concept_y, _concept_z = course_setup
        set_mastery(db_session, owner, concept_y.id, 0.0, evidence_weight=0.2)
        module = db_session.query(Module).filter_by(course_version_id=version.id).one()
        lesson = Lesson(module_id=module.id, position=1, title="Mutual Exclusion")
        db_session.add(lesson)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept_y.id))
        db_session.commit()
        mark_taught(db_session, owner, course, version, lesson, [concept_y])
        service = make_service(db_session)

        result = service.recommend_next(course.id, owner.id)
        items = [result.recommended, *result.alternatives]
        assert any(
            item["activity_type"] == "TARGETED_PRACTICE"
            and item["concept_ids"] == [str(concept_y.id)]
            for item in items
        )
        assert not any(
            item["activity_type"] == "PREREQUISITE_REMEDIATION"
            and item["concept_ids"] == [str(concept_y.id)]
            for item in items
        )

    @pytest.mark.parametrize(
        ("mastery", "evidence_weight"),
        [(0.0, 0.2), (0.5, 1000.0)],
        ids=["limited-diagnostic-evidence", "developing-evidence"],
    )
    def test_practice_requires_completed_teaching_even_with_existing_evidence(
        self, db_session, owner, course_setup, mastery, evidence_weight
    ):
        course, version, _concept_x, concept_y, _concept_z = course_setup
        set_mastery(db_session, owner, concept_y.id, mastery, evidence_weight=evidence_weight)

        result = make_service(db_session).recommend_next(course.id, owner.id)
        items = [result.recommended, *result.alternatives]
        assert not any(
            item["activity_type"] == "TARGETED_PRACTICE"
            and item["concept_ids"] == [str(concept_y.id)]
            for item in items
        )

        module = db_session.query(Module).filter_by(course_version_id=version.id).one()
        lesson = Lesson(module_id=module.id, position=1, title="Mutual Exclusion")
        db_session.add(lesson)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept_y.id))
        db_session.commit()
        mark_taught(db_session, owner, course, version, lesson, [concept_y])

        taught_result = make_service(db_session).recommend_next(course.id, owner.id)
        taught_items = [taught_result.recommended, *taught_result.alternatives]
        assert any(
            item["activity_type"] == "TARGETED_PRACTICE"
            and item["concept_ids"] == [str(concept_y.id)]
            for item in taught_items
        )

    def test_not_assessed_concept_is_practice_only_after_its_lesson_was_taught(
        self, db_session, owner, course_setup
    ):
        course, version, _concept_x, _concept_y, concept_z = course_setup
        lesson = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        mark_taught(db_session, owner, course, version, lesson, [concept_z])

        result = make_service(db_session).recommend_next(course.id, owner.id)
        items = [result.recommended, *result.alternatives]
        practice = next(
            item for item in items
            if item["activity_type"] == "TARGETED_PRACTICE" and item["concept_ids"] == [str(concept_z.id)]
        )
        assert "not assessed" in practice["reason"].lower()

    def test_unassessed_first_course_concept_does_not_replace_new_lesson(
        self, db_session, owner, course_setup
    ):
        course, *_ = course_setup
        result = make_service(db_session).recommend_next(course.id, owner.id)
        assert result.recommended["activity_type"] == "NEW_LESSON"


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
        set_mastery(db_session, owner, concept_x.id, 0.2)
        set_mastery(db_session, owner, concept_y.id, 0.2)
        # A taught, proficient concept supplies a distinct challenge candidate.
        set_mastery(db_session, owner, concept_z.id, 0.95)
        lesson_z = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        mark_taught(db_session, owner, course, version, lesson_z, [concept_z])
        service = make_service(db_session)

        service.recommend_next(course.id, owner.id)
        decision = db_session.query(AdaptationDecision).order_by(AdaptationDecision.created_at.desc()).first()
        assert len(decision.candidates_considered) >= 3
        selected_count = sum(1 for c in decision.candidates_considered if c["selected"])
        assert selected_count == 1


class TestChallengeEligibility:
    def test_challenge_requires_completed_teaching_and_proficient_evidence(
        self, db_session, owner, course_setup
    ):
        course, version, _concept_x, _concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_z.id, 0.9)
        lesson = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        mark_taught(db_session, owner, course, version, lesson, [concept_z])

        result = make_service(db_session).recommend_next(course.id, owner.id)
        challenge = next(
            item for item in [result.recommended, *result.alternatives]
            if item["activity_type"] == "CHALLENGE"
        )
        assert challenge["concept_ids"] == [str(concept_z.id)]
        assert "single-concept" in challenge["reason"].lower()
        decision = db_session.query(AdaptationDecision).filter_by(id=result.decision_id).one()
        assert str(concept_z.id) in decision.input_snapshot["completed_teaching_concept_ids"]

    def test_eligible_concepts_across_modules_form_a_multiconcept_challenge(
        self, db_session, owner, course_setup
    ):
        course, version, _concept_x, concept_y, concept_z = course_setup
        module = db_session.query(Module).filter_by(course_version_id=version.id).one()
        lesson_y = Lesson(module_id=module.id, position=1, title="Mutual Exclusion")
        db_session.add(lesson_y)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=lesson_y.id, concept_id=concept_y.id))
        module_z = Module(course_version_id=version.id, position=1, title="Another module")
        db_session.add(module_z)
        db_session.flush()
        lesson_z = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        lesson_z.module_id = module_z.id
        db_session.commit()
        set_mastery(db_session, owner, concept_y.id, 0.9)
        set_mastery(db_session, owner, concept_z.id, 0.9)
        mark_taught(db_session, owner, course, version, lesson_y, [concept_y])
        mark_taught(db_session, owner, course, version, lesson_z, [concept_z])

        result = make_service(db_session).recommend_next(course.id, owner.id)
        challenges = [item for item in [result.recommended, *result.alternatives]
                      if item["activity_type"] == "CHALLENGE"]
        assert any(set(item["concept_ids"]) == {str(concept_y.id), str(concept_z.id)} for item in challenges)


class TestNewLessonEligibility:
    def test_completed_teaching_is_skipped_and_later_ready_lessons_remain_candidates(
        self, db_session, owner, course_setup
    ):
        course, version, concept_x, concept_y, concept_z = course_setup
        module = db_session.query(Module).filter_by(course_version_id=version.id).one()
        lesson_y = Lesson(module_id=module.id, position=1, title="Mutual Exclusion")
        lesson_x = Lesson(module_id=module.id, position=2, title="Deadlock Detection")
        db_session.add_all([lesson_y, lesson_x])
        db_session.flush()
        db_session.add_all([
            LessonConcept(lesson_id=lesson_y.id, concept_id=concept_y.id),
            LessonConcept(lesson_id=lesson_x.id, concept_id=concept_x.id),
        ])
        lesson_z = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        db_session.commit()
        set_mastery(db_session, owner, concept_x.id, 0.75)
        set_mastery(db_session, owner, concept_y.id, 0.75)
        set_mastery(db_session, owner, concept_z.id, 0.75)
        mark_taught(db_session, owner, course, version, lesson_z, [concept_z])

        result = make_service(db_session).recommend_next(course.id, owner.id)
        decision = db_session.query(AdaptationDecision).filter_by(id=result.decision_id).one()
        new_lesson_ids = {
            candidate["lesson_id"]
            for candidate in decision.candidates_considered
            if candidate["activity_type"] == "NEW_LESSON"
        }

        assert str(lesson_z.id) not in new_lesson_ids
        assert {str(lesson_y.id), str(lesson_x.id)} <= new_lesson_ids


class TestGuardrailNoFixedLabels:
    def test_no_banned_vocabulary_anywhere_in_the_response_or_decision(self, db_session, owner, course_setup):
        course, version, concept_x, concept_y, concept_z = course_setup
        set_mastery(db_session, owner, concept_x.id, 0.45)
        set_mastery(db_session, owner, concept_y.id, 0.5)
        set_mastery(db_session, owner, concept_z.id, 0.95)
        lesson_z = db_session.query(Lesson).filter_by(title="Scheduling Basics").one()
        mark_taught(db_session, owner, course, version, lesson_z, [concept_z])
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
