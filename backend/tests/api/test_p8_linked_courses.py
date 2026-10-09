"""Deterministic P8 ownership, matching, reuse, lifecycle, and citation checks."""
import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.adaptation.service import AdaptationService
from app.modules.courses.matching import build_link_matches
from app.modules.courses.models import Course, CourseStatus
from app.modules.courses.service import CourseService
from app.modules.courses.p8_models import CourseSubject, CrossCourseConceptMatch
from app.modules.curriculum.models import (
    Concept,
    ConceptPrerequisite,
    ConceptSource,
    CourseVersion,
    CourseVersionStatus,
    EdgeStrength,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.learning.models import (
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    GradingCorrection,
    GradingJudgment,
    GradingIssueReport,
    LearningActivity,
)
from app.modules.learning.service import LearningService
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    LessonContentCitation,
    PreparedActivityQuestion,
    QuestionSource,
)
from app.modules.preparation.service import ActivityPreparationService, PreparationFailure
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from tests.conftest import auth_headers


def published_course(db, owner, title, *, chunks=()):
    course = Course(owner_id=owner.id, title=title, status=CourseStatus.PUBLISHED.value)
    db.add(course)
    db.flush()
    documents = []
    for index, text in enumerate(chunks):
        checksum = hashlib.sha256(text.encode()).hexdigest()
        document = Document(
            course_id=course.id,
            owner_id=owner.id,
            filename=f"{title}-{index}.txt",
            content_type="text/plain",
            role="STUDY",
            status="EXTRACTED",
            storage_path="fixture-only",
            checksum_sha256=checksum,
            size_bytes=len(text),
        )
        db.add(document)
        db.flush()
        chunk = Chunk(
            id=uuid4(),
            document_id=document.id,
            course_id=course.id,
            owner_id=owner.id,
            position=0,
            text=text,
            heading_path="Unit 1 > Source notes",
            page_start=index + 1,
            page_end=index + 1,
            char_start=0,
            char_end=len(text),
        )
        db.add(chunk)
        documents.append((document, chunk))
    db.flush()
    fingerprint = hashlib.sha256("|".join(sorted(item.checksum_sha256 for item, _ in documents)).encode()).hexdigest()
    version = CourseVersion(
        course_id=course.id,
        owner_id=owner.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
        source_fingerprint=fingerprint,
    )
    db.add(version)
    db.flush()
    course.active_version_id = version.id
    db.flush()
    return course, version, documents


def add_concept(db, course, version, owner, name, definition, source_chunk):
    concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        canonical_key=name.casefold().replace(" ", "-"),
        name=name,
        definition=definition,
        importance=0.5,
    )
    db.add(concept)
    db.flush()
    if source_chunk is not None:
        db.add(ConceptSource(
            concept_id=concept.id,
            chunk_id=source_chunk.id,
            course_id=course.id,
            owner_id=owner.id,
        ))
    db.flush()
    return concept


class TestCourseSubjectsAndLinks:
    def test_standalone_and_same_subject_creation_do_not_add_a_link(self, client, owner):
        subject = client.post(
            "/api/v1/courses/subjects",
            json={"name": "Computer Science"},
            headers=auth_headers(owner.email),
        )
        assert subject.status_code == 201
        standalone = client.post(
            "/api/v1/courses",
            json={"title": "Unit 1", "subject_id": subject.json()["id"]},
            headers=auth_headers(owner.email),
        )
        grouped = client.post(
            "/api/v1/courses",
            json={"title": "Unit 2", "subject_id": subject.json()["id"]},
            headers=auth_headers(owner.email),
        )
        assert standalone.status_code == grouped.status_code == 201
        assert standalone.json()["subject_name"] == "Computer Science"
        assert standalone.json()["builds_on_course_id"] is None
        assert grouped.json()["builds_on_course_id"] is None

    def test_creation_pins_only_an_owned_published_ready_version(self, client, owner, other_user, db_session):
        earlier, earlier_version, _ = published_course(db_session, owner, "Unit 1")
        foreign, _foreign_version, _ = published_course(db_session, other_user, "Foreign Unit")
        draft = Course(owner_id=owner.id, title="Draft", status="DRAFT")
        db_session.add(draft)
        db_session.commit()

        created = client.post(
            "/api/v1/courses",
            json={"title": "Unit 2", "builds_on_course_id": str(earlier.id)},
            headers=auth_headers(owner.email),
        )
        assert created.status_code == 201
        assert created.json()["builds_on_course_id"] == str(earlier.id)
        assert created.json()["builds_on_version_number"] == 1
        assert created.json()["builds_on_sources_available"] is True

        for invalid in (foreign.id, draft.id, uuid4()):
            response = client.post(
                "/api/v1/courses",
                json={"title": "Invalid Unit", "builds_on_course_id": str(invalid)},
                headers=auth_headers(owner.email),
            )
            assert response.status_code == 422
        assert earlier_version.id == earlier.active_version_id

    def test_link_can_be_changed_or_removed_before_publication(self, client, owner, db_session):
        first, _first_version, _ = published_course(db_session, owner, "Earlier Unit A")
        second, _second_version, _ = published_course(db_session, owner, "Earlier Unit B")
        db_session.commit()
        current = client.post(
            "/api/v1/courses",
            json={"title": "Current Unit", "builds_on_course_id": str(first.id)},
            headers=auth_headers(owner.email),
        )
        assert current.status_code == 201
        assert current.json()["builds_on_course_id"] == str(first.id)

        changed = client.patch(
            f"/api/v1/courses/{current.json()['id']}",
            json={"builds_on_course_id": str(second.id)},
            headers=auth_headers(owner.email),
        )
        assert changed.status_code == 200
        assert changed.json()["builds_on_course_id"] == str(second.id)
        removed = client.patch(
            f"/api/v1/courses/{current.json()['id']}",
            json={"builds_on_course_id": None},
            headers=auth_headers(owner.email),
        )
        assert removed.status_code == 200
        assert removed.json()["builds_on_course_id"] is None

    def test_published_link_cannot_be_changed_and_foreign_subject_is_hidden(self, client, owner, other_user, db_session):
        subject = client.post(
            "/api/v1/courses/subjects", json={"name": "Owner subject"}, headers=auth_headers(owner.email)
        ).json()
        foreign_subject = client.post(
            "/api/v1/courses/subjects", json={"name": "Private subject"}, headers=auth_headers(other_user.email)
        ).json()
        published, _, _ = published_course(db_session, owner, "Published current")
        db_session.commit()
        update = client.patch(
            f"/api/v1/courses/{published.id}",
            json={"subject_id": subject["id"], "builds_on_course_id": None},
            headers=auth_headers(owner.email),
        )
        assert update.status_code == 409
        draft = client.post(
            "/api/v1/courses", json={"title": "Draft edit"}, headers=auth_headers(owner.email)
        ).json()
        foreign_update = client.patch(
            f"/api/v1/courses/{draft['id']}",
            json={"subject_id": foreign_subject["id"]},
            headers=auth_headers(owner.email),
        )
        assert foreign_update.status_code == 422


class TestLinkedRetrievalAndMatching:
    @pytest.mark.parametrize(
        ("earlier_definition", "current_definition"),
        [
            ("The bound is x < 5.", "The bound is x > 5."),
            ("The bound is x ≤ 5.", "The bound is x ≥ 5."),
            ("The result is -5.", "The result is +5."),
            ("The measurement is 3.5 units.", "The measurement is 35 units."),
            ("The grouping is (x + 2) * 3.", "The grouping is x + (2 * 3)."),
        ],
    )
    def test_meaning_changing_math_symbols_never_make_a_reliable_match(
        self, owner, db_session, earlier_definition, current_definition
    ):
        linked, linked_version, linked_docs = published_course(
            db_session, owner, "Unit 1", chunks=[earlier_definition]
        )
        current, current_version, current_docs = published_course(
            db_session, owner, "Unit 2", chunks=[current_definition]
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        add_concept(
            db_session, linked, linked_version, owner, "Numeric bound", earlier_definition, linked_docs[0][1]
        )
        add_concept(
            db_session, current, current_version, owner, "Numeric bound", current_definition, current_docs[0][1]
        )

        match = build_link_matches(db_session, current, current_version)[0]

        assert match.status == "UNCERTAIN"
        assert match.status != "RELIABLE"

    def test_subject_creation_recovers_when_a_concurrent_request_wins(self, owner, db_session, monkeypatch):
        winner = CourseSubject(owner_id=owner.id, name="Biology", normalized_name="biology")
        db_session.add(winner)
        db_session.commit()
        db_session.refresh(winner)
        service = CourseService(db_session)

        # Model the race window: the first lookup saw no row, while another
        # transaction committed this row before our insert hit the unique key.
        lookup_results = iter((None, winner))
        monkeypatch.setattr(service, "_find_subject", lambda *_args: next(lookup_results))

        subject = service.create_subject(owner.id, " BIOLOGY ")

        assert subject.id == winner.id
        assert db_session.query(CourseSubject).filter_by(owner_id=owner.id, normalized_name="biology").count() == 1

    def test_linked_recommendation_reason_stays_within_storage_limit(self, db_session):
        from types import SimpleNamespace

        concept_id = uuid4()
        service = object.__new__(AdaptationService)
        service._linked_readiness_sources = {concept_id: {"Earlier course " + "x" * 185}}
        reason = service._explain_linked_readiness(
            "You're ready for the next lesson, covering " + "concept " * 35 + ".",
            SimpleNamespace(concept_ids=(concept_id,)),
        )

        assert len(reason) <= 500
        assert "attributed to the earlier course" in reason

    def test_linked_retrieval_is_exact_and_source_view_keeps_original_attribution(
        self, client, owner, db_session
    ):
        shared_subject = client.post(
            "/api/v1/courses/subjects", json={"name": "Biology"}, headers=auth_headers(owner.email)
        ).json()
        linked, linked_version, linked_docs = published_course(
            db_session, owner, "Unit 1", chunks=["Mitochondria produce ATP through cellular respiration."]
        )
        current, current_version, current_docs = published_course(
            db_session, owner, "Unit 2", chunks=["Cellular respiration includes glycolysis."]
        )
        unrelated, _, unrelated_docs = published_course(
            db_session, owner, "Other same subject", chunks=["Mitochondria and ATP appear in these unrelated notes."]
        )
        current.subject_id = unrelated.subject_id = UUID(shared_subject["id"])
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.linked_course_title_snapshot = linked.title
        current.linked_version_number = linked_version.version_number
        current.link_revision = 1
        db_session.commit()

        search = client.get(
            f"/api/v1/courses/{current.id}/retrieval?q=mitochondria%20ATP",
            headers=auth_headers(owner.email),
        )
        assert search.status_code == 200
        results = search.json()
        assert {row["source_course_id"] for row in results} == {str(linked.id)}
        assert all(row["is_linked_source"] is True for row in results)
        source = client.get(
            f"/api/v1/courses/{current.id}/chunks/{linked_docs[0][1].id}",
            headers=auth_headers(owner.email),
        )
        assert source.status_code == 200
        assert source.json()["source_course_title"] == "Unit 1"
        assert source.json()["source_version_id"] == str(linked_version.id)
        assert source.json()["filename"] == "Unit 1-0.txt"
        assert source.json()["page_start"] == 1
        denied = client.get(
            f"/api/v1/courses/{current.id}/chunks/{unrelated_docs[0][1].id}",
            headers=auth_headers(owner.email),
        )
        assert denied.status_code == 404
        newer_version = CourseVersion(
            course_id=linked.id,
            owner_id=owner.id,
            version_number=2,
            status=CourseVersionStatus.READY.value,
            source_fingerprint=linked_version.source_fingerprint,
            source_revision=linked_version.source_revision,
        )
        db_session.add(newer_version)
        db_session.flush()
        linked.active_version_id = newer_version.id
        db_session.flush()
        assert client.get(
            f"/api/v1/courses/{current.id}/chunks/{linked_docs[0][1].id}",
            headers=auth_headers(owner.email),
        ).json()["source_version_id"] == str(linked_version.id)
        assert current.active_version_id == current_version.id
        assert current_docs

    def test_match_status_uses_definitions_and_support_not_names(self, owner, db_session):
        linked, linked_version, linked_docs = published_course(
            db_session,
            owner,
            "Unit 1",
            chunks=[
                "A transaction groups operations so they succeed or fail as one unit.",
                "A graph traversal explores connected vertices from a chosen starting point.",
                "A user interface machine stores rendering preferences and display values.",
            ],
        )
        current, current_version, current_docs = published_course(
            db_session,
            owner,
            "Unit 2",
            chunks=[
                "A transaction groups operations so they succeed or fail as one unit.",
                "A graph walk visits connected vertices from a starting vertex.",
                "A finite set of states and transitions models computation.",
            ],
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        db_session.flush()
        add_concept(db_session, linked, linked_version, owner, "Atomic update", "A transaction groups operations so they succeed or fail as one unit.", linked_docs[0][1])
        add_concept(db_session, current, current_version, owner, "Transaction", "A transaction groups operations so they succeed or fail as one unit.", current_docs[0][1])
        add_concept(db_session, linked, linked_version, owner, "Graph traversal", "A graph traversal explores connected vertices from a chosen starting point.", linked_docs[1][1])
        add_concept(db_session, current, current_version, owner, "Graph traversal", "A graph walk visits connected vertices from a starting vertex.", current_docs[1][1])
        add_concept(db_session, linked, linked_version, owner, "State machine", "A user interface machine stores rendering preferences and display values.", linked_docs[2][1])
        add_concept(db_session, current, current_version, owner, "State machine", "A finite set of states and transitions models computation.", current_docs[2][1])
        db_session.flush()

        newer_linked_version = CourseVersion(
            course_id=linked.id,
            owner_id=owner.id,
            version_number=2,
            status=CourseVersionStatus.READY.value,
            source_fingerprint=linked_version.source_fingerprint,
            source_revision=linked_version.source_revision,
        )
        db_session.add(newer_linked_version)
        db_session.flush()
        linked.active_version_id = newer_linked_version.id
        db_session.flush()

        matches = build_link_matches(db_session, current, current_version)
        by_pair = {
            (row.provenance["current_definition"], row.provenance["linked_definition"]): row.status
            for row in matches
        }
        assert by_pair[("A transaction groups operations so they succeed or fail as one unit.", "A transaction groups operations so they succeed or fail as one unit.")] == "RELIABLE"
        assert by_pair[("A graph walk visits connected vertices from a starting vertex.", "A graph traversal explores connected vertices from a chosen starting point.")] == "UNCERTAIN"
        assert by_pair[("A finite set of states and transitions models computation.", "A user interface machine stores rendering preferences and display values.")] == "UNSUPPORTED"
        assert all(row.current_version_id == current_version.id and row.linked_version_id == linked_version.id for row in matches)
        assert all("current_source_chunk_ids" in row.provenance for row in matches)

    def test_unsupported_match_without_passages_records_the_limitation(self, owner, db_session):
        linked, linked_version, _ = published_course(db_session, owner, "Unit 1", chunks=["Earlier notes."])
        current, current_version, _ = published_course(db_session, owner, "Unit 2", chunks=["Current notes."])
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        add_concept(db_session, linked, linked_version, owner, "Graph walk", "A graph walk visits vertices.", None)
        add_concept(db_session, current, current_version, owner, "Graph walk", "A graph walk models memory allocation.", None)
        matches = build_link_matches(db_session, current, current_version)
        assert len(matches) == 1
        assert matches[0].status == "UNSUPPORTED"
        assert matches[0].provenance["source_support"] is False
        summary = CourseService(db_session).linked_match_summaries(current, owner.id)
        assert summary[0]["has_source_support"] is False
        assert summary[0]["status"] == "UNSUPPORTED"

    def test_source_word_overlap_without_a_supported_definition_statement_is_not_reliable(
        self, owner, db_session
    ):
        definition = "A traversal explores connected vertices."
        linked, linked_version, linked_docs = published_course(
            db_session, owner, "Unit 1", chunks=["Connected vertices are explored by traversal."]
        )
        current, current_version, current_docs = published_course(
            db_session, owner, "Unit 2", chunks=["Vertices are connected when traversal explores them."]
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        add_concept(db_session, linked, linked_version, owner, "Traversal", definition, linked_docs[0][1])
        add_concept(db_session, current, current_version, owner, "Traversal", definition, current_docs[0][1])

        match = build_link_matches(db_session, current, current_version)[0]

        assert match.status == "UNSUPPORTED"
        assert match.provenance["source_support"] is False

    def test_source_statement_that_explicitly_negates_definition_is_not_reliable(
        self, owner, db_session
    ):
        definition = "A traversal explores connected vertices."
        linked, linked_version, linked_docs = published_course(
            db_session, owner, "Unit 1", chunks=[f"It is not true that {definition}"]
        )
        current, current_version, current_docs = published_course(
            db_session, owner, "Unit 2", chunks=[f"It is not true that {definition}"]
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        add_concept(db_session, linked, linked_version, owner, "Traversal", definition, linked_docs[0][1])
        add_concept(db_session, current, current_version, owner, "Traversal", definition, current_docs[0][1])

        match = build_link_matches(db_session, current, current_version)[0]

        assert match.status == "UNSUPPORTED"
        assert match.provenance["source_support"] is False

    def test_contraction_negation_and_cross_passage_conflict_are_not_reliable(
        self, owner, db_session
    ):
        definition = "A traversal explores connected vertices."
        negated = f"The claim can't be true: {definition}"
        linked, linked_version, linked_docs = published_course(
            db_session,
            owner,
            "Unit 1",
            chunks=[definition, negated],
        )
        current, current_version, current_docs = published_course(
            db_session,
            owner,
            "Unit 2",
            chunks=[definition],
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        linked_concept = add_concept(
            db_session, linked, linked_version, owner, "Traversal", definition, linked_docs[0][1]
        )
        db_session.add(ConceptSource(
            concept_id=linked_concept.id,
            chunk_id=linked_docs[1][1].id,
            course_id=linked.id,
            owner_id=owner.id,
        ))
        add_concept(
            db_session, current, current_version, owner, "Traversal", definition, current_docs[0][1]
        )

        match = build_link_matches(db_session, current, current_version)[0]

        assert match.status == "UNSUPPORTED"
        assert match.provenance["source_support"] is False

    def test_separate_qualification_passage_downgrades_a_match_to_uncertain(
        self, owner, db_session
    ):
        definition = "A traversal explores connected vertices."
        passages = [definition, "That description is not valid for disconnected graphs."]
        linked, linked_version, linked_docs = published_course(
            db_session, owner, "Unit 1", chunks=passages
        )
        current, current_version, current_docs = published_course(
            db_session, owner, "Unit 2", chunks=passages
        )
        current.linked_course_id = linked.id
        current.linked_version_id = linked_version.id
        current.link_revision = 1
        linked_concept = add_concept(
            db_session, linked, linked_version, owner, "Traversal", definition, linked_docs[0][1]
        )
        current_concept = add_concept(
            db_session, current, current_version, owner, "Traversal", definition, current_docs[0][1]
        )
        db_session.add_all([
            ConceptSource(
                concept_id=linked_concept.id,
                chunk_id=linked_docs[1][1].id,
                course_id=linked.id,
                owner_id=owner.id,
            ),
            ConceptSource(
                concept_id=current_concept.id,
                chunk_id=current_docs[1][1].id,
                course_id=current.id,
                owner_id=owner.id,
            ),
        ])
        db_session.flush()

        match = build_link_matches(db_session, current, current_version)[0]

        assert match.status == "UNCERTAIN"
        assert match.provenance["source_support"] is True

    def test_preparation_worker_rejects_changed_link_dependency(self, owner, db_session):
        earlier, _earlier_version, _ = published_course(db_session, owner, "Unit 1")
        current, current_version, _ = published_course(db_session, owner, "Unit 2")
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier.active_version_id
        current.link_revision = 1
        db_session.flush()
        service = ActivityPreparationService(db_session, FakeGenerationGateway())
        preparation = ActivityPreparation(
            preparation_key="p8-stale-link",
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_purpose="NEW_LESSON",
            target_concept_ids=[],
            link_dependency_fingerprint=service._link_dependency_fingerprint(current.id),
            presentation_format="detailed",
            include_assessment=False,
        )
        db_session.add(preparation)
        db_session.flush()
        current.linked_course_id = None
        current.linked_version_id = None
        current.link_revision += 1
        db_session.flush()
        with pytest.raises(PreparationFailure, match="STALE_LINK_DEPENDENCY"):
            service._validate_job_context(preparation)


class TestLinkedEvidenceAndOptionalCheck:
    def test_corrected_submitted_linked_evidence_changes_readiness_not_current_mastery(
        self, owner, db_session
    ):
        earlier, earlier_version, earlier_docs = published_course(db_session, owner, "Unit 1", chunks=["A graph traversal explores connected vertices."])
        current, current_version, current_docs = published_course(db_session, owner, "Unit 2", chunks=["A graph walk visits connected vertices."])
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        linked_concept = add_concept(db_session, earlier, earlier_version, owner, "Graph traversal", "A graph traversal explores connected vertices.", earlier_docs[0][1])
        prerequisite = add_concept(db_session, current, current_version, owner, "Graph walk", "A graph walk visits connected vertices.", current_docs[0][1])
        dependent = add_concept(db_session, current, current_version, owner, "Path analysis", "Path analysis applies graph walks.", current_docs[0][1])
        db_session.add(CrossCourseConceptMatch(
            owner_id=owner.id,
            current_course_id=current.id,
            current_version_id=current_version.id,
            current_concept_id=prerequisite.id,
            linked_course_id=earlier.id,
            linked_version_id=earlier_version.id,
            linked_concept_id=linked_concept.id,
            status="RELIABLE",
            provenance={"policy": "fixture", "current_source_chunk_ids": [str(current_docs[0][1].id)], "linked_source_chunk_ids": [str(earlier_docs[0][1].id)]},
        ))
        db_session.add(ConceptPrerequisite(
            course_id=current.id,
            course_version_id=current_version.id,
            prerequisite_concept_id=prerequisite.id,
            dependent_concept_id=dependent.id,
            strength=EdgeStrength.HARD.value,
            confidence=1.0,
        ))
        activity = LearningActivity(owner_id=owner.id, course_id=earlier.id, course_version_id=earlier_version.id, activity_type="TARGETED_PRACTICE", target_concept_ids=[str(linked_concept.id)], status="IN_PROGRESS")
        db_session.add(activity)
        question = Question(
            course_id=earlier.id,
            course_version_id=earlier_version.id,
            owner_id=owner.id,
            question_type="MCQ",
            prompt="What does a traversal visit?",
            options=["Connected vertices", "Rendering preferences"],
            correct_answer="Connected vertices",
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="p8-test",
        )
        db_session.add(question)
        db_session.flush()
        db_session.add(QuestionConcept(question_id=question.id, concept_id=linked_concept.id, weight=1.0))
        session = AssessmentSession(activity_id=activity.id, course_version_id=earlier_version.id, assessment_type="ACTIVITY", status="OPEN")
        db_session.add(session)
        db_session.flush()
        assessment_question = AssessmentQuestion(session_id=session.id, question_id=question.id, question_version=1, position=0)
        db_session.add(assessment_question)
        db_session.flush()
        answer = AnswerSubmission(assessment_question_id=assessment_question.id, given_answer="Connected vertices", status="GRADED")
        db_session.add(answer)
        db_session.flush()
        attempt = QuestionAttempt(
            assessment_question_id=assessment_question.id,
            question_id=question.id,
            question_version=1,
            owner_id=owner.id,
            course_id=earlier.id,
            given_answer="Connected vertices",
            correctness=0.0,
        )
        db_session.add(attempt)
        db_session.flush()
        event = MasteryEvent(
            owner_id=owner.id,
            concept_id=linked_concept.id,
            course_id=earlier.id,
            course_version_id=earlier_version.id,
            question_attempt_id=attempt.id,
            correctness=0.0,
            evidence_weight_base=1.0,
        )
        db_session.add(event)
        db_session.flush()
        report = GradingIssueReport(answer_submission_id=answer.id, owner_id=owner.id, course_id=earlier.id, report_text="Corrected by fixture")
        db_session.add(report)
        db_session.flush()
        db_session.add(GradingCorrection(
            report_id=report.id,
            question_attempt_id=attempt.id,
            reviewer_id=owner.id,
            version=1,
            criteria_met=[True],
            rubric_score=1,
            effective_correctness=1,
            reason="The saved answer was correct.",
        ))
        db_session.flush()

        service = AdaptationService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
        hidden, _ = service._build_concept_states([prerequisite, dependent], [db_session.query(ConceptPrerequisite).one()], owner.id)
        # The OPEN fixed set contributes no visible linked evidence, so the
        # prerequisite remains at its prior readiness until submission.
        assert hidden[dependent.id].readiness == pytest.approx(0.3)
        session.status = "SUBMITTED"
        db_session.flush()
        visible, _ = service._build_concept_states([prerequisite, dependent], [db_session.query(ConceptPrerequisite).one()], owner.id)
        assert visible[dependent.id].readiness > hidden[dependent.id].readiness
        # Linked evidence affects prerequisite readiness, while the current
        # course mastery report remains the unassessed local prior.
        assert visible[prerequisite.id].mastery == pytest.approx(0.3)
        assert visible[prerequisite.id].evidence_weight_total == 0.0
        effective = service.mastery.visible_mastery_events().filter(MasteryEvent.id == event.id).one()
        assert effective.effective_correctness == 1.0

        db_session.add(MasteryEvent(
            owner_id=owner.id,
            concept_id=prerequisite.id,
            course_id=current.id,
            course_version_id=current_version.id,
            question_attempt_id=None,
            correctness=0.0,
            evidence_weight_base=1.0,
        ))
        db_session.flush()
        local, _ = service._build_concept_states(
            [prerequisite, dependent], [db_session.query(ConceptPrerequisite).one()], owner.id
        )
        assert local[dependent.id].readiness == pytest.approx(local[prerequisite.id].mastery)
        assert local[dependent.id].readiness < visible[dependent.id].readiness

    def test_linked_evidence_is_not_fanned_out_across_ambiguous_matches(self, owner, db_session):
        earlier, earlier_version, _ = published_course(db_session, owner, "Unit 1")
        current, current_version, _ = published_course(db_session, owner, "Unit 2")
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        linked_concept = add_concept(
            db_session, earlier, earlier_version, owner, "Graph traversal", "A graph traversal.", None
        )
        first = add_concept(db_session, current, current_version, owner, "Graph walk", "A graph walk.", None)
        second = add_concept(db_session, current, current_version, owner, "Graph route", "A graph route.", None)
        dependent = add_concept(db_session, current, current_version, owner, "Path analysis", "Path analysis.", None)
        for concept in (first, second):
            db_session.add(CrossCourseConceptMatch(
                owner_id=owner.id,
                current_course_id=current.id,
                current_version_id=current_version.id,
                current_concept_id=concept.id,
                linked_course_id=earlier.id,
                linked_version_id=earlier_version.id,
                linked_concept_id=linked_concept.id,
                status="RELIABLE",
                provenance={"source_support": True},
            ))
        db_session.add(MasteryEvent(
            owner_id=owner.id,
            concept_id=linked_concept.id,
            course_id=earlier.id,
            course_version_id=earlier_version.id,
            question_attempt_id=None,
            correctness=1.0,
            evidence_weight_base=10.0,
        ))
        db_session.add(ConceptPrerequisite(
            course_id=current.id,
            course_version_id=current_version.id,
            prerequisite_concept_id=first.id,
            dependent_concept_id=dependent.id,
            strength=EdgeStrength.HARD.value,
            confidence=1.0,
        ))
        db_session.flush()
        service = AdaptationService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
        states, _ = service._build_concept_states(
            [first, second, dependent], [db_session.query(ConceptPrerequisite).one()], owner.id
        )
        assert states[dependent.id].readiness == pytest.approx(0.3)
        assert not service._linked_readiness_sources

    def test_uncertain_check_is_optional_and_skip_creates_no_evidence(self, owner, db_session):
        earlier, earlier_version, earlier_docs = published_course(
            db_session,
            owner,
            "Unit 1",
            chunks=[
                "A graph traversal explores connected vertices from a start.",
                "An alternate traversal begins at a designated vertex and follows each edge.",
            ],
        )
        current, current_version, current_docs = published_course(db_session, owner, "Unit 2", chunks=["A graph walk visits connected vertices from a start."])
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        linked_concept = add_concept(db_session, earlier, earlier_version, owner, "Graph traversal", "A graph traversal explores connected vertices from a start.", earlier_docs[0][1])
        current_concept = add_concept(db_session, current, current_version, owner, "Graph traversal", "A graph walk visits connected vertices from a start.", current_docs[0][1])
        match = CrossCourseConceptMatch(
            owner_id=owner.id,
            current_course_id=current.id,
            current_version_id=current_version.id,
            current_concept_id=current_concept.id,
            linked_course_id=earlier.id,
            linked_version_id=earlier_version.id,
            linked_concept_id=linked_concept.id,
            status="UNCERTAIN",
            provenance={"source_support": True, "current_source_chunk_ids": [str(current_docs[0][1].id)], "linked_source_chunk_ids": [str(earlier_docs[0][1].id)]},
        )
        db_session.add(match)
        alternate_linked_concept = add_concept(
            db_session,
            earlier,
            earlier_version,
            owner,
            "Alternate graph traversal",
            "An alternate traversal begins at a designated vertex and follows each edge.",
            earlier_docs[1][1],
        )
        alternate_match = CrossCourseConceptMatch(
            owner_id=owner.id,
            current_course_id=current.id,
            current_version_id=current_version.id,
            current_concept_id=current_concept.id,
            linked_course_id=earlier.id,
            linked_version_id=earlier_version.id,
            linked_concept_id=alternate_linked_concept.id,
            status="UNCERTAIN",
            provenance={"source_support": True, "current_source_chunk_ids": [str(current_docs[0][1].id)], "linked_source_chunk_ids": [str(earlier_docs[1][1].id)]},
        )
        db_session.add(alternate_match)
        db_session.commit()
        service = LearningService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
        activity = service.create_optional_linked_check(current.id, owner.id, match.id)
        assert activity["activity_type"] == "OPTIONAL_PREREQUISITE_CHECK"
        assert activity["is_optional_linked_check"] is True
        activity_row = db_session.get(LearningActivity, activity["id"])
        assert activity_row.linked_match_id == match.id
        preparation_service = ActivityPreparationService(db_session, FakeGenerationGateway())
        scoped_chunks, _ = preparation_service._lesson_source_chunks(
            [current_concept.id],
            current.id,
            owner.id,
            include_uncertain_link_sources=True,
            selected_link_match_id=activity_row.linked_match_id,
        )
        assert {chunk.id for chunk in scoped_chunks} == {current_docs[0][1].id, earlier_docs[0][1].id}
        preparation = ActivityPreparation(
            preparation_key="p8-selected-optional-match",
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_id=activity_row.id,
            activity_purpose=activity["activity_type"],
            target_concept_ids=[str(current_concept.id)],
            link_dependency_fingerprint=preparation_service._link_dependency_fingerprint(
                current.id, selected_link_match_id=activity_row.linked_match_id
            ),
            presentation_format="quiz_first",
            include_assessment=True,
        )
        db_session.add(preparation)
        db_session.flush()
        assert preparation_service._validate_job_context(preparation)[-1][0].id == current_concept.id
        db_session.delete(match)
        db_session.flush()
        with pytest.raises(PreparationFailure, match="STALE_LINK_MATCH"):
            preparation_service._validate_job_context(preparation)
        skipped = service.skip_optional_link_check(current.id, activity["id"], owner.id)
        assert skipped["status"] == "COMPLETED"
        assert db_session.query(QuestionAttempt).filter_by(course_id=current.id).count() == 0
        assert db_session.query(MasteryEvent).filter_by(course_id=current.id).count() == 0
        assert db_session.query(AssessmentSession).filter_by(activity_id=activity["id"]).count() == 0

    def test_deleted_earlier_course_revokes_link_and_keeps_started_fixed_set(
        self, client, owner, db_session, monkeypatch
    ):
        from app.modules.privacy.service import PrivacyService

        monkeypatch.setattr(PrivacyService, "dispatch_cleanup_tasks", lambda _service: None)
        earlier, earlier_version, earlier_docs = published_course(db_session, owner, "Unit 1", chunks=["A graph traversal explores connected vertices."])
        current, current_version, _ = published_course(db_session, owner, "Unit 2")
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.linked_course_title_snapshot = earlier.title
        current.linked_version_number = earlier_version.version_number
        current.link_revision = 1
        concept = Concept(course_id=current.id, course_version_id=current_version.id, owner_id=owner.id, canonical_key="current", name="Graph walk", definition="A graph walk.")
        db_session.add(concept)
        db_session.flush()
        activity = LearningActivity(owner_id=owner.id, course_id=current.id, course_version_id=current_version.id, activity_type="TARGETED_PRACTICE", target_concept_ids=[str(concept.id)], status="IN_PROGRESS")
        db_session.add(activity)
        question = Question(course_id=current.id, course_version_id=current_version.id, owner_id=owner.id, question_type="MCQ", prompt="Question?", options=["A", "B"], correct_answer="A", difficulty=0.5, is_diagnostic=0, version=1, model_id="fixture", prompt_version="p8-test")
        db_session.add(question)
        db_session.flush()
        db_session.add(QuestionConcept(question_id=question.id, concept_id=concept.id, weight=1.0))
        db_session.add(QuestionSource(question_id=question.id, chunk_id=earlier_docs[0][1].id))
        session = AssessmentSession(activity_id=activity.id, course_version_id=current_version.id, assessment_type="ACTIVITY", status="OPEN")
        db_session.add(session)
        db_session.flush()
        db_session.add(AssessmentQuestion(session_id=session.id, question_id=question.id, question_version=1, position=0))
        db_session.commit()

        deleted = client.delete(f"/api/v1/courses/{earlier.id}", headers=auth_headers(owner.email))
        assert deleted.status_code == 204
        db_session.refresh(current)
        assert current.linked_course_id is None
        assert current.link_revoked_at is not None
        assert current.linked_course_title_snapshot == "Unit 1"
        assert db_session.get(AssessmentSession, session.id) is not None
        fixed_question_id = db_session.query(AssessmentQuestion.question_id).filter_by(
            session_id=session.id
        ).scalar()
        assert db_session.get(AssessmentQuestion, db_session.query(AssessmentQuestion.id).filter_by(session_id=session.id).scalar()) is not None
        fixed_question = db_session.get(Question, fixed_question_id)
        assert fixed_question is not None and fixed_question.source_revoked_at is not None
        assert db_session.query(QuestionSource).filter_by(question_id=fixed_question_id).count() == 0
        resumed = client.get(
            f"/api/v1/courses/{current.id}/assessment-sessions/{session.id}",
            headers=auth_headers(owner.email),
        )
        assert resumed.status_code == 200
        assert resumed.json()["linked_sources_unavailable"] is True
        assert resumed.json()["linked_source_course_title"] == "Unit 1"

    def test_link_deletion_removes_skipped_activity_and_revokes_its_questions(
        self, client, owner, db_session, monkeypatch
    ):
        from app.modules.courses.p8_models import P8_OPTIONAL_LINK_CHECK_REASON
        from app.modules.privacy.service import PrivacyService

        monkeypatch.setattr(PrivacyService, "dispatch_cleanup_tasks", lambda _service: None)
        earlier, earlier_version, earlier_docs = published_course(
            db_session, owner, "Unit 1", chunks=["A traversal visits connected vertices."]
        )
        current, current_version, _ = published_course(db_session, owner, "Unit 2")
        current.linked_course_id = earlier.id
        current.linked_version_id = earlier_version.id
        current.link_revision = 1
        concept = add_concept(
            db_session, current, current_version, owner, "Traversal", "A traversal visits connected vertices.", None
        )
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_type="OPTIONAL_PREREQUISITE_CHECK",
            reason_text=P8_OPTIONAL_LINK_CHECK_REASON,
            target_concept_ids=[str(concept.id)],
            status="COMPLETED",
        )
        question = Question(
            course_id=current.id,
            course_version_id=current_version.id,
            owner_id=owner.id,
            question_type="MCQ",
            prompt="Which statement describes traversal?",
            options=["Visits connected vertices", "Changes graph labels"],
            correct_answer="Visits connected vertices",
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="p8-test",
        )
        db_session.add_all([activity, question])
        db_session.flush()
        db_session.add_all([
            QuestionConcept(question_id=question.id, concept_id=concept.id, weight=1.0),
            QuestionSource(question_id=question.id, chunk_id=earlier_docs[0][1].id),
        ])
        preparation_service = ActivityPreparationService(db_session, FakeGenerationGateway())
        artifact = LessonContentArtifact(
            artifact_key="p8-skipped-linked-check-artifact",
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_purpose=activity.activity_type,
            target_concept_ids=[str(concept.id)],
            source_fingerprint=current_version.source_fingerprint,
            curriculum_fingerprint="p8-skipped-linked-check-curriculum",
            link_dependency_fingerprint=preparation_service._link_dependency_fingerprint(current.id),
            presentation_format="quiz_first",
            sections=[{"text": "A traversal visits connected vertices."}],
            source_chunk_ids=[str(earlier_docs[0][1].id)],
            model_id="fixture",
            validation_model_id="fixture",
            prompt_version="p8-test",
            schema_version="p8-test",
            validation_policy_version="p8-test",
            validation_status="PASSED",
            validated_at=datetime.now(timezone.utc),
        )
        db_session.add(artifact)
        db_session.flush()
        preparation = ActivityPreparation(
            preparation_key="p8-skipped-linked-check-preparation",
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_id=activity.id,
            activity_purpose=activity.activity_type,
            target_concept_ids=[str(concept.id)],
            link_dependency_fingerprint=artifact.link_dependency_fingerprint,
            presentation_format="quiz_first",
            include_assessment=True,
            status="READY",
            content_artifact_id=artifact.id,
        )
        db_session.add(preparation)
        db_session.flush()
        db_session.add(PreparedActivityQuestion(
            preparation_id=preparation.id,
            question_id=question.id,
            question_version=question.version,
            position=0,
        ))
        db_session.add(LessonContentCitation(artifact_id=artifact.id, chunk_id=earlier_docs[0][1].id))
        db_session.commit()
        activity_id, preparation_id, artifact_id, question_id = (
            activity.id,
            preparation.id,
            artifact.id,
            question.id,
        )
        assert preparation_service._artifact_is_started(artifact_id) is False

        deleted = client.delete(f"/api/v1/courses/{earlier.id}", headers=auth_headers(owner.email))

        assert deleted.status_code == 204
        assert db_session.get(LearningActivity, activity_id) is None
        assert db_session.get(ActivityPreparation, preparation_id) is None
        assert db_session.get(LessonContentArtifact, artifact_id) is None
        assert db_session.query(PreparedActivityQuestion).filter_by(question_id=question_id).count() == 0
        revoked_question = db_session.get(Question, question_id)
        assert revoked_question is not None and revoked_question.source_revoked_at is not None
        assert db_session.query(QuestionSource).filter_by(question_id=question_id).count() == 0

        replacement_activity = LearningActivity(
            owner_id=owner.id,
            course_id=current.id,
            course_version_id=current_version.id,
            activity_type="TARGETED_PRACTICE",
            target_concept_ids=[str(concept.id)],
            status="READY",
        )
        db_session.add(replacement_activity)
        db_session.flush()
        assert LearningService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())._questions_for_activity(
            replacement_activity
        ) == []


def test_submitted_assessment_and_grading_review_keep_only_authorized_linked_sources(
    owner, db_session
):
    subject = CourseSubject(owner_id=owner.id, name="Graphs", normalized_name="graphs")
    db_session.add(subject)
    db_session.flush()
    earlier, earlier_version, earlier_docs = published_course(
        db_session, owner, "Unit 1", chunks=["A traversal visits connected vertices."]
    )
    current, current_version, current_docs = published_course(
        db_session, owner, "Unit 2", chunks=["A graph walk applies traversal rules."]
    )
    unrelated, _unrelated_version, unrelated_docs = published_course(
        db_session, owner, "Same subject, no link", chunks=["Unrelated source in the same subject."]
    )
    for course in (earlier, current, unrelated):
        course.subject_id = subject.id
    current.linked_course_id = earlier.id
    current.linked_version_id = earlier_version.id
    current.linked_course_title_snapshot = earlier.title
    current.linked_version_number = earlier_version.version_number
    current.link_revision = 1

    activity = LearningActivity(
        owner_id=owner.id,
        course_id=current.id,
        course_version_id=current_version.id,
        activity_type="TARGETED_PRACTICE",
        target_concept_ids=[],
        status="COMPLETED",
    )
    question = Question(
        course_id=current.id,
        course_version_id=current_version.id,
        owner_id=owner.id,
        question_type="SHORT_TEXT",
        prompt="Explain the traversal idea.",
        options=None,
        correct_answer=None,
        rubric=["Names the traversal rule"],
        rubric_details=[{
            "text": "Names the traversal rule",
            "expected_reasoning": "Connect traversal to visiting reachable vertices.",
            "source_chunk_ids": [str(earlier_docs[0][1].id), str(unrelated_docs[0][1].id)],
        }],
        expected_reasoning="Connect traversal to visiting reachable vertices.",
        explanation="Traversal visits reachable vertices.",
        difficulty=0.5,
        is_diagnostic=0,
        version=1,
        model_id="fixture",
        prompt_version="p8-test",
    )
    db_session.add_all([activity, question])
    db_session.flush()
    session = AssessmentSession(
        activity_id=activity.id,
        course_version_id=current_version.id,
        assessment_type="ACTIVITY",
        status="SUBMITTED",
        submitted_at=datetime.now(timezone.utc),
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
    answer = AnswerSubmission(
        assessment_question_id=assessment_question.id,
        given_answer="A traversal visits reachable vertices.",
        status="GRADED",
    )
    db_session.add(answer)
    db_session.flush()
    attempt = QuestionAttempt(
        assessment_question_id=assessment_question.id,
        question_id=question.id,
        question_version=question.version,
        owner_id=owner.id,
        course_id=current.id,
        given_answer=answer.given_answer,
        correctness=1.0,
    )
    db_session.add(attempt)
    db_session.flush()
    db_session.add(GradingJudgment(
        answer_submission_id=answer.id,
        criteria_met=[True],
        rubric_score=1,
        evidence_correctness=1,
        policy_version="fixture",
        model_id="fixture",
    ))
    report = GradingIssueReport(
        answer_submission_id=answer.id,
        owner_id=owner.id,
        course_id=current.id,
        report_text="Please review this judgment.",
    )
    db_session.add(report)
    db_session.add_all([
        QuestionSource(question_id=question.id, chunk_id=earlier_docs[0][1].id),
        QuestionSource(question_id=question.id, chunk_id=current_docs[0][1].id),
        QuestionSource(question_id=question.id, chunk_id=unrelated_docs[0][1].id),
    ])
    db_session.commit()

    service = LearningService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
    result = service._session_out(session, owner.id, current.id)["questions"][0]["result"]
    assert set(result["source_chunk_ids"]) == {current_docs[0][1].id, earlier_docs[0][1].id}
    assert unrelated_docs[0][1].id not in result["source_chunk_ids"]
    assert result["rubric_feedback"][0]["source_chunk_ids"] == [earlier_docs[0][1].id]

    review = service.get_grading_review(report.id)
    assert {source["chunk_id"] for source in review["sources"]} == {
        current_docs[0][1].id,
        earlier_docs[0][1].id,
    }
    linked_source = next(source for source in review["sources"] if source["chunk_id"] == earlier_docs[0][1].id)
    assert linked_source["source_course_id"] == earlier.id
    assert linked_source["source_course_title"] == "Unit 1"
    assert linked_source["filename"] == "Unit 1-0.txt"
    assert linked_source["page_start"] == 1
    assert linked_source["heading_path"] == "Unit 1 > Source notes"


def test_linked_course_deletion_preserves_started_snapshot_and_invalidates_unstarted_work(
    client, owner, db_session, monkeypatch
):
    from app.modules.privacy.service import PrivacyService

    monkeypatch.setattr(PrivacyService, "dispatch_cleanup_tasks", lambda _service: None)
    earlier, earlier_version, earlier_docs = published_course(
        db_session, owner, "Unit 1", chunks=["A traversal visits connected vertices."]
    )
    started_course, started_version, _ = published_course(db_session, owner, "Started Unit 2")
    unstarted_course, unstarted_version, _ = published_course(db_session, owner, "Unstarted Unit 2")
    for course in (started_course, unstarted_course):
        course.linked_course_id = earlier.id
        course.linked_version_id = earlier_version.id
        course.linked_course_title_snapshot = earlier.title
        course.linked_version_number = earlier_version.version_number
        course.link_revision = 1

    def add_course_work(course, version, *, status, key):
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="PREREQUISITE_REMEDIATION",
            target_concept_ids=[],
            status=status,
        )
        db_session.add(activity)
        db_session.flush()
        artifact = LessonContentArtifact(
            artifact_key=f"p8-{key}-artifact",
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_purpose=activity.activity_type,
            target_concept_ids=[],
            source_fingerprint=version.source_fingerprint,
            curriculum_fingerprint=f"{key}-curriculum",
            link_dependency_fingerprint=f"{key}-linked-dependency",
            presentation_format="detailed",
            sections=[{"text": "Saved explanation from the linked course."}],
            source_chunk_ids=[str(earlier_docs[0][1].id)],
            model_id="fixture",
            validation_model_id="fixture",
            prompt_version="p8-test",
            schema_version="p8-test",
            validation_policy_version="p8-test",
            validation_status="PASSED",
            validated_at=datetime.now(timezone.utc),
        )
        db_session.add(artifact)
        db_session.flush()
        citation = LessonContentCitation(artifact_id=artifact.id, chunk_id=earlier_docs[0][1].id)
        preparation = ActivityPreparation(
            preparation_key=f"p8-{key}-preparation",
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_id=activity.id,
            activity_purpose=activity.activity_type,
            target_concept_ids=[],
            link_dependency_fingerprint=f"{key}-linked-dependency",
            presentation_format="detailed",
            include_assessment=False,
            status="READY",
            content_artifact_id=artifact.id,
        )
        db_session.add_all([citation, preparation])
        db_session.flush()
        return activity, artifact, citation, preparation

    started = add_course_work(started_course, started_version, status="IN_PROGRESS", key="started")
    unstarted = add_course_work(unstarted_course, unstarted_version, status="READY", key="unstarted")
    db_session.commit()
    started_artifact_id = started[1].id
    started_citation_id = started[2].id
    started_preparation_id = started[3].id
    unstarted_artifact_id = unstarted[1].id
    unstarted_citation_id = unstarted[2].id
    unstarted_preparation_id = unstarted[3].id

    deleted = client.delete(f"/api/v1/courses/{earlier.id}", headers=auth_headers(owner.email))

    assert deleted.status_code == 204
    _started_activity, started_artifact, _started_citation, _started_preparation = started
    assert db_session.get(ActivityPreparation, started_preparation_id) is not None
    assert db_session.get(LessonContentArtifact, started_artifact_id) is not None
    assert db_session.get(LessonContentCitation, started_citation_id) is None
    assert db_session.get(ActivityPreparation, unstarted_preparation_id) is None
    assert db_session.get(LessonContentArtifact, unstarted_artifact_id) is None
    assert db_session.get(LessonContentCitation, unstarted_citation_id) is None
    assert ActivityPreparationService(db_session, FakeGenerationGateway())._artifact_sources_are_current(
        started_artifact
    ) is True


def test_course_deletion_cleans_only_the_owners_orphaned_subject(client, owner, other_user, db_session, monkeypatch):
    from app.modules.privacy.service import PrivacyService

    monkeypatch.setattr(PrivacyService, "dispatch_cleanup_tasks", lambda _service: None)
    owner_subject = client.post(
        "/api/v1/courses/subjects", json={"name": "Owner only"}, headers=auth_headers(owner.email)
    ).json()
    other_subject = client.post(
        "/api/v1/courses/subjects", json={"name": "Other learner"}, headers=auth_headers(other_user.email)
    ).json()
    course = client.post(
        "/api/v1/courses",
        json={"title": "Standalone grouped course", "subject_id": owner_subject["id"]},
        headers=auth_headers(owner.email),
    ).json()

    deleted = client.delete(f"/api/v1/courses/{course['id']}", headers=auth_headers(owner.email))

    assert deleted.status_code == 204
    assert db_session.get(CourseSubject, UUID(owner_subject["id"])) is None
    assert db_session.get(CourseSubject, UUID(other_subject["id"])) is not None
