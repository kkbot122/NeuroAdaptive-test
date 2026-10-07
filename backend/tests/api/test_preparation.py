import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.modules.courses.models import Course, CourseStatus
from app.modules.curriculum.models import (
    Concept,
    ConceptSource,
    CourseVersion,
    CourseVersionStatus,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.learning.models import ActivityStatus, LearningActivity
from app.modules.learning.router import _preparation_service as preparation_dependency
from app.modules.mastery.models import MasteryEvent, Question, QuestionConcept
from app.modules.abuse.models import AIUsageDaily
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    PreparedActivityQuestion,
    PreparationStatus,
)
from app.modules.preparation.service import ActivityPreparationService, PreparationFailure
from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError
from tests.conftest import auth_headers


class RecordingDispatcher:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, preparation_id, owner_id, *, speculative=False):
        self.enqueued.append((preparation_id, owner_id, speculative))


def _lesson_draft(concept_ids, chunk_ids, *, unsupported=False):
    def statement(text, concept_index=0):
        return {
            "text": text,
            "concept_ids": [str(concept_ids[concept_index % len(concept_ids)])],
            "citation_chunk_ids": [str(chunk_ids[concept_index % len(chunk_ids)])],
        }

    explanation = [statement("The source explains the first taught idea.")]
    if unsupported:
        explanation.append(statement("This unsupported claim is not in the course source."))
    explanation.append(statement("The second taught idea also appears in the source.", min(1, len(concept_ids) - 1)))
    return json.dumps(
        {
            "insufficient_evidence": False,
            "objective": [statement("The objective covers a source-supported taught idea.")],
            "explanation": explanation,
            "example": [statement("This example follows the supplied course passage.", min(1, len(concept_ids) - 1))],
            "recap": [statement("The recap restates a supported idea.")],
        }
    )


def _question_draft(concept_ids, chunk_ids, *, duplicate_prompt=False, prompt_offset=0):
    questions = []
    for index in range(5):
        concept_index = index % len(concept_ids)
        prompt_index = 0 if duplicate_prompt else index + prompt_offset
        correct = f"Supported answer {index}"
        questions.append(
            {
                "concept_id": str(concept_ids[concept_index]),
                "prompt": f"From the course source, which choice describes idea {prompt_index}?",
                "options": [correct, f"Distractor {index} B", f"Distractor {index} C", f"Distractor {index} D"],
                "correct_answer": correct,
                "explanation": f"The source supports this answer for idea {index}.",
                "source_chunk_ids": [str(chunk_ids[concept_index])],
            }
        )
    return json.dumps({"insufficient_evidence": False, "questions": questions})


def _configure_generation(
    gateway, concept_ids, chunk_ids, *, content_unsupported=False, duplicate_prompt=False, prompt_offset=0
):
    gateway.when_prompt_contains(
        "Prepare the first lesson",
        _lesson_draft(concept_ids, chunk_ids, unsupported=content_unsupported),
    ).when_prompt_contains(
        "Write exactly 5 single-answer",
        _question_draft(
            concept_ids, chunk_ids, duplicate_prompt=duplicate_prompt, prompt_offset=prompt_offset
        ),
    ).when_prompt_contains(
        "is also a supported correct answer.", '{"supported": false}'
    ).set_default('{"supported": true}')
    return gateway


def seed_published_activity(db, owner, *, with_next_lesson=False):
    source_texts = [
        "The first course concept has a source passage for teaching and assessment.",
        "The second course concept has another source passage for teaching and assessment.",
    ]
    checksums = [hashlib.sha256(text.encode()).hexdigest() for text in source_texts]
    source_fingerprint = hashlib.sha256("|".join(sorted(checksums)).encode()).hexdigest()
    course = Course(owner_id=owner.id, title="Grounded preparation", status=CourseStatus.PUBLISHED.value)
    db.add(course)
    db.flush()
    version = CourseVersion(
        course_id=course.id,
        owner_id=owner.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
        source_fingerprint=source_fingerprint,
    )
    db.add(version)
    db.flush()
    course.active_version_id = version.id
    concepts = [
        Concept(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            canonical_key=f"p2-{index}-{uuid4().hex[:8]}",
            name=f"Grounded concept {index + 1}",
            definition=f"Definition supported by source passage {index + 1}.",
            importance=0.8,
        )
        for index in range(2)
    ]
    db.add_all(concepts)
    db.flush()
    docs, chunks = [], []
    for index, (text, checksum) in enumerate(zip(source_texts, checksums)):
        doc = Document(
            course_id=course.id,
            owner_id=owner.id,
            filename=f"source-{index}.txt",
            role="STUDY",
            status="EXTRACTED",
            storage_path=f"/tmp/p2-{uuid4().hex}.txt",
            size_bytes=len(text),
            checksum_sha256=checksum,
        )
        db.add(doc)
        db.flush()
        chunk = Chunk(
            id=uuid4(),
            document_id=doc.id,
            course_id=course.id,
            owner_id=owner.id,
            position=0,
            text=text,
            char_count=len(text),
            token_count=12,
        )
        db.add(chunk)
        db.flush()
        docs.append(doc)
        chunks.append(chunk)
        db.add(ConceptSource(concept_id=concepts[index].id, chunk_id=chunk.id, course_id=course.id, owner_id=owner.id))
    module = Module(course_version_id=version.id, position=0, title="First module")
    db.add(module)
    db.flush()
    lesson = Lesson(module_id=module.id, position=0, title="First lesson", objective="Teach both concepts")
    db.add(lesson)
    db.flush()
    db.add_all(LessonConcept(lesson_id=lesson.id, concept_id=concept.id, weight=0.5) for concept in concepts)
    if with_next_lesson:
        next_module = Module(course_version_id=version.id, position=1, title="Next module")
        db.add(next_module)
        db.flush()
        next_lesson = Lesson(module_id=next_module.id, position=0, title="Next lesson", objective="Review concept two")
        db.add(next_lesson)
        db.flush()
        db.add(LessonConcept(lesson_id=next_lesson.id, concept_id=concepts[1].id, weight=1.0))
    activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id) for concept in concepts],
        lesson_id=lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="detailed",
    )
    db.add(activity)
    db.commit()
    return course, version, concepts, chunks, lesson, activity


def test_uploaded_outline_publish_saved_lesson_and_fixed_mcq_assessment(
    client, owner, db_session, fake_generation, monkeypatch
):
    source = ("Parsing is the process of analysing a string of symbols. " * 12).strip()
    course = client.post(
        "/api/v1/courses", json={"title": "Compilers"}, headers=auth_headers(owner.email)
    ).json()
    uploaded = client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("notes.txt", source.encode(), "text/plain")},
        data={"role": "STUDY"},
        headers=auth_headers(owner.email),
    )
    assert uploaded.status_code == 201
    processed = client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
    assert processed.status_code == 202
    job = client.get(f"/api/v1/jobs/{processed.json()['id']}", headers=auth_headers(owner.email)).json()
    assert job["status"] == "READY"
    structure = client.get(f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)).json()
    assert structure["status"] == "READY"
    assert structure["modules"]

    from app.modules.curriculum.models import ConceptSource, LessonConcept

    active_lesson = structure["modules"][0]["lessons"][0]
    concept_ids = [row[0] for row in db_session.query(LessonConcept.concept_id).filter_by(lesson_id=UUID(active_lesson["id"])).all()]
    chunk_ids = []
    for concept_id in concept_ids:
        chunk_ids.append(db_session.query(ConceptSource.chunk_id).filter_by(concept_id=concept_id).first()[0])
    _configure_generation(fake_generation, concept_ids, chunk_ids)
    dispatcher = RecordingDispatcher()
    preparation_service = ActivityPreparationService(db_session, fake_generation, dispatcher)
    monkeypatch.setitem(__import__("app.main", fromlist=["app"]).app.dependency_overrides, preparation_dependency, lambda: preparation_service)

    headers = auth_headers(owner.email)
    published = client.post(f"/api/v1/courses/{course['id']}/publish-structure", headers=headers)
    assert published.status_code == 200
    assert len(dispatcher.enqueued) == 1
    first_preparation_id = dispatcher.enqueued[0][0]
    first_preparation = db_session.query(ActivityPreparation).filter_by(id=first_preparation_id).one()
    assert first_preparation.activity_id is None
    assert first_preparation.include_assessment is True
    assert first_preparation.is_speculative is False
    provider_calls_before_worker = len(fake_generation.calls)
    usage_before = db_session.query(AIUsageDaily).filter_by(owner_id=owner.id).one_or_none()
    usage_count_before = usage_before.call_count if usage_before is not None else 0
    worker_result = preparation_service.run(first_preparation_id, owner.id)
    assert worker_result.status == PreparationStatus.READY, worker_result.error_category

    selected = client.post(f"/api/v1/courses/{course['id']}/activities/next", headers=headers)
    assert selected.status_code == 200
    activity = selected.json()
    assert activity["lesson_id"] == active_lesson["id"]
    assert activity["preparation"]["status"] == "READY"
    assert UUID(activity["preparation"]["id"]) == first_preparation_id
    assert db_session.query(ActivityPreparation).filter_by(activity_id=UUID(activity["id"])).count() == 1
    assert len(dispatcher.enqueued) == 1

    content_url = f"/api/v1/courses/{course['id']}/activities/{activity['id']}/content?format=detailed"
    saved_response = client.get(content_url, headers=headers)
    assert saved_response.status_code == 200
    preparation_id = first_preparation_id
    saved = saved_response.json()
    assert saved["status"] == "READY"
    assert {"objective", "explanation", "example", "recap"} == set(saved["content"]["sections"])
    assert saved["preparation"]["assessment_ready"] is True
    calls_after_generation = len(fake_generation.calls)
    assert calls_after_generation > provider_calls_before_worker
    usage_after = db_session.query(AIUsageDaily).filter_by(owner_id=owner.id).one()
    assert usage_after.call_count - usage_count_before == calls_after_generation - provider_calls_before_worker
    assert client.get(content_url, headers=headers).json() == saved
    assert len(fake_generation.calls) == calls_after_generation

    preparation_row = db_session.query(ActivityPreparation).filter_by(id=preparation_id).one()
    assert preparation_row.status == PreparationStatus.READY
    prepared = (
        db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=preparation_id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    )
    assert len(prepared) == 5
    questions = [db_session.query(Question).filter_by(id=row.question_id).one() for row in prepared]
    assert [row.position for row in prepared] == list(range(5))
    assert all(question.question_type == "MCQ" and question.is_diagnostic == 0 for question in questions)
    assert all(len(question.options) == 4 and question.correct_answer in question.options for question in questions)
    assert all(question.explanation and question.difficulty == 0.5 for question in questions)

    mastery_before_reading = db_session.query(MasteryEvent).count()
    completed_reading = client.post(
        f"/api/v1/courses/{course['id']}/activities/{activity['id']}/reading-complete", headers=headers
    )
    assert completed_reading.status_code == 200
    assert db_session.query(MasteryEvent).count() == mastery_before_reading

    assessment = client.post(
        f"/api/v1/courses/{course['id']}/activities/{activity['id']}/assessment", headers=headers
    )
    assert assessment.status_code == 200
    session = assessment.json()
    assert [question["question_id"] for question in session["questions"]] == [str(row.question_id) for row in prepared]
    session_url = f"/api/v1/courses/{course['id']}/assessment-sessions/{session['id']}"
    for question in session["questions"]:
        confirmed = client.post(
            f"{session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][0]},
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert all("result" not in item for item in confirmed.json()["questions"])
    resumed = client.get(session_url, headers=headers)
    assert resumed.status_code == 200
    assert all(question["answer"] for question in resumed.json()["questions"])
    assert all("result" not in question for question in resumed.json()["questions"])
    submitted = client.post(f"{session_url}/submit", headers=headers)
    assert submitted.status_code == 200
    assert all(question["result"]["explanation"] for question in submitted.json()["questions"])
    assert all(question["result"]["source_chunk_ids"] for question in submitted.json()["questions"])


def test_unsupported_claim_is_removed_and_failed_stage_retries_without_losing_completed_content(
    db_session, owner
):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(
        FakeGenerationGateway(),
        [concept.id for concept in concepts],
        [chunk.id for chunk in chunks],
        content_unsupported=True,
    )
    gateway.when_prompt_contains(
        "CLAIM:\nThis unsupported claim is not in the course source.", '{"supported": false}'
    )
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, gateway, dispatcher)
    row = service.request_activity(course.id, activity.id, owner.id)
    result = service.run(row.id, owner.id)
    assert result.status == PreparationStatus.READY, result.error_category
    artifact = db_session.query(LessonContentArtifact).filter_by(id=result.content_artifact_id).one()
    rendered = json.dumps(artifact.sections)
    assert "unsupported claim" not in rendered
    assert all(artifact.sections[name] for name in ("objective", "explanation", "example", "recap"))

    # A later questions-stage failure leaves the validated content attached.
    failing_gateway = _configure_generation(
        FakeGenerationGateway(),
        [concept.id for concept in concepts],
        [chunk.id for chunk in chunks],
        duplicate_prompt=True,
    )
    # Reuse the existing content artifact. Three invalid question candidates
    # exhaust the stage, but the content artifact remains available.
    # The same activity row is already ready, so create a fresh activity to
    # exercise failed-stage resume against the shared content artifact.
    activity.status = ActivityStatus.COMPLETED.value
    db_session.commit()
    retry_activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=_version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id) for concept in concepts],
        lesson_id=_lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="detailed",
    )
    db_session.add(retry_activity)
    db_session.commit()
    retry_service = ActivityPreparationService(db_session, failing_gateway, dispatcher)
    retry_row = retry_service.request_activity(course.id, retry_activity.id, owner.id)
    failed = retry_service.run(retry_row.id, owner.id)
    assert failed.status == PreparationStatus.RECOVERABLE_FAILURE
    assert failed.content_artifact_id == artifact.id
    assert failed.stage == "QUESTIONS"
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=failed.id).count() == 0

    recovery_gateway = _configure_generation(
        FakeGenerationGateway(),
        [concept.id for concept in concepts],
        [chunk.id for chunk in chunks],
        prompt_offset=10,
    )
    recovery_service = ActivityPreparationService(db_session, recovery_gateway, dispatcher)
    recovered = recovery_service.retry(course.id, retry_activity.id, owner.id)
    assert recovered.stage == "QUESTIONS"
    assert recovered.content_artifact_id == artifact.id
    final = recovery_service.run(recovered.id, owner.id)
    assert final.status == PreparationStatus.READY
    assert final.content_artifact_id == artifact.id
    assert db_session.query(LessonContentArtifact).count() == 1


def test_duplicate_delivery_and_repeated_requests_share_one_preparation_and_question_set(
    db_session, owner
):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(
        db_session, owner, with_next_lesson=True
    )
    gateway = _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks])
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, gateway, dispatcher)

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: service.request_activity(course.id, activity.id, owner.id), range(2)))
    assert rows[0].id == rows[1].id
    assert len(dispatcher.enqueued) == 1
    preparation_id = rows[0].id

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: service.run(preparation_id, owner.id), range(2)))
    ready = db_session.query(ActivityPreparation).filter_by(id=preparation_id).one()
    assert ready.status == PreparationStatus.READY
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation_id).count() == 5
    assert db_session.query(LessonContentArtifact).count() == 1
    assert len(gateway.calls) > 0
    # One speculative content-only job is permitted; it has no questions.
    speculative = db_session.query(ActivityPreparation).filter(ActivityPreparation.is_speculative.is_(True)).one()
    assert speculative.include_assessment is False
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=speculative.id).count() == 0


def test_foreign_owner_cannot_read_or_prepare_an_activity(client, owner, other_user, db_session, monkeypatch):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(
        db_session,
        _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks]),
        dispatcher,
    )
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: service)
    response = client.get(
        f"/api/v1/courses/{course.id}/activities/{activity.id}/content?format=detailed",
        headers=auth_headers(other_user.email),
    )
    assert response.status_code == 404
    assert dispatcher.enqueued == []


def test_assessment_readiness_requires_complete_prepared_set(db_session, owner):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks])
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    preparation = service.request_activity(course.id, activity.id, owner.id)
    service.run(preparation.id, owner.id)
    db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation.id).delete()
    db_session.commit()
    assert service.preparation_out(preparation)["assessment_ready"] is False
    try:
        service.require_assessment_ready(course.id, activity.id, owner.id)
    except Exception as exc:
        assert type(exc).__name__ == "PreparationConflict"
    else:
        raise AssertionError("an incomplete fixed question set must not be assessable")


def test_inadequate_supported_content_abstains_and_retries_from_content_stage(db_session, owner):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    insufficient = json.loads(
        _lesson_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks])
    )
    insufficient["explanation"] = [
        {
            "text": "No supported explanation remains after validation.",
            "concept_ids": [str(concepts[0].id)],
            "citation_chunk_ids": [str(chunks[0].id)],
        }
    ]
    failing_gateway = FakeGenerationGateway().when_prompt_contains(
        "Prepare the first lesson", json.dumps(insufficient)
    ).when_prompt_contains(
        "CLAIM:\nNo supported explanation remains after validation.", '{"supported": false}'
    ).set_default('{"supported": true}')
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, failing_gateway, dispatcher)
    preparation = service.request_activity(course.id, activity.id, owner.id)
    failed = service.run(preparation.id, owner.id)
    assert failed.status == PreparationStatus.RECOVERABLE_FAILURE
    assert failed.stage == "CONTENT"
    assert failed.candidate_count == 3
    assert failed.content_artifact_id is None
    assert db_session.query(LessonContentArtifact).count() == 0

    recovery_gateway = _configure_generation(
        FakeGenerationGateway(), [concept.id for concept in concepts], [chunk.id for chunk in chunks]
    )
    recovery = ActivityPreparationService(db_session, recovery_gateway, dispatcher)
    retry = recovery.retry(course.id, activity.id, owner.id)
    assert retry.stage == "CONTENT"
    assert retry.retry_count == 1
    assert recovery.run(retry.id, owner.id).status == PreparationStatus.READY


def test_diagnostic_duplicate_is_rejected_from_lesson_set(db_session, owner):
    course, version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    duplicate_prompt = json.loads(
        _question_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks])
    )["questions"][0]["prompt"]
    diagnostic = Question(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        question_type="MCQ",
        prompt=f"  {duplicate_prompt.upper()} ",
        options=["A", "B", "C", "D"],
        correct_answer="A",
        difficulty=0.5,
        is_diagnostic=1,
        version=1,
        model_id="fixture",
        prompt_version="diagnostic-v1",
    )
    db_session.add(diagnostic)
    db_session.flush()
    db_session.add(QuestionConcept(question_id=diagnostic.id, concept_id=concepts[0].id, weight=1.0))
    db_session.commit()

    gateway = _configure_generation(
        FakeGenerationGateway(), [concept.id for concept in concepts], [chunk.id for chunk in chunks]
    )
    preparation = ActivityPreparationService(db_session, gateway, RecordingDispatcher()).request_activity(
        course.id, activity.id, owner.id
    )
    result = ActivityPreparationService(db_session, gateway, RecordingDispatcher()).run(preparation.id, owner.id)
    assert result.status == PreparationStatus.RECOVERABLE_FAILURE
    assert result.stage == "QUESTIONS"
    assert result.content_artifact_id is not None
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=result.id).count() == 0


def test_source_change_blocks_saved_content_artifact(db_session, owner):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks])
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    preparation = service.request_activity(course.id, activity.id, owner.id)
    assert service.run(preparation.id, owner.id).status == PreparationStatus.READY
    generation_count = len(gateway.calls)
    doc = db_session.query(Document).filter_by(course_id=course.id).first()
    doc.checksum_sha256 = hashlib.sha256(b"replaced source").hexdigest()
    db_session.commit()
    try:
        service.content_response(course.id, activity.id, owner.id, "detailed")
    except Exception as exc:
        assert type(exc).__name__ == "PreparationConflict"
    else:
        raise AssertionError("saved content from a replaced source version must not be served")
    assert len(gateway.calls) == generation_count
    assert db_session.query(LessonContentArtifact).count() == 1


def test_replaced_worker_lease_cannot_attach_an_artifact(db_session, owner):
    from datetime import datetime, timedelta, timezone

    course, _version, _concepts, _chunks, _lesson, activity = seed_published_activity(db_session, owner)
    service = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher())
    preparation = service.request_activity(course.id, activity.id, owner.id)
    running, stale_token = service._acquire(preparation.id, owner.id)
    assert stale_token is not None
    running.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    _replacement, replacement_token = service._acquire(preparation.id, owner.id)
    assert replacement_token is not None and replacement_token != stale_token
    try:
        service._attach_content(running, stale_token, None)
    except PreparationFailure as exc:
        assert exc.category == "LEASE_LOST"
    else:
        raise AssertionError("an expired worker must not attach content after lease takeover")
    assert db_session.query(LessonContentArtifact).count() == 0
    current = db_session.query(ActivityPreparation).filter_by(id=preparation.id).one()
    assert current.lease_token == replacement_token
    assert current.status == PreparationStatus.RUNNING


def test_unavailable_distractor_check_does_not_validate_questions(db_session, owner):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)

    class FailingDistractorValidation(FakeGenerationGateway):
        def generate(self, prompt, *args, **kwargs):
            if "is also a supported correct answer." in prompt:
                raise GenerationError("fixture validation unavailable")
            return super().generate(prompt, *args, **kwargs)

    gateway = _configure_generation(
        FailingDistractorValidation(), [item.id for item in concepts], [item.id for item in chunks]
    )
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    preparation = service.request_activity(course.id, activity.id, owner.id)
    result = service.run(preparation.id, owner.id)

    assert result.status == PreparationStatus.RECOVERABLE_FAILURE
    assert result.stage == "QUESTIONS"
    assert result.error_category == "VALIDATION_UNAVAILABLE"
    assert result.candidate_count == 3
    assert result.content_artifact_id is not None
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=result.id).count() == 0


def test_saved_content_rejects_chunk_that_changed_owner(db_session, owner, other_user):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks])
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    preparation = service.request_activity(course.id, activity.id, owner.id)
    assert service.run(preparation.id, owner.id).status == PreparationStatus.READY
    generation_count = len(gateway.calls)

    chunks[0].owner_id = other_user.id
    db_session.commit()
    try:
        service.content_response(course.id, activity.id, owner.id, "detailed")
    except Exception as exc:
        assert type(exc).__name__ == "PreparationConflict"
    else:
        raise AssertionError("saved content with a foreign-owned source chunk must not be served")
    assert len(gateway.calls) == generation_count


def test_content_artifact_cache_key_tracks_prompt_version(db_session, owner, monkeypatch):
    course, version, concepts, _chunks, lesson, _activity = seed_published_activity(db_session, owner)
    service = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher())
    from app.modules.preparation import service as preparation_module

    original_key = service._artifact_key(version, lesson, concepts, "detailed")[0]
    monkeypatch.setattr(preparation_module, "CONTENT_PROMPT_VERSION", "p2-lesson-content-v2")
    updated_key = service._artifact_key(version, lesson, concepts, "detailed")[0]
    assert updated_key != original_key


def test_presentation_variants_prepare_on_demand_and_reuse_saved_artifacts(db_session, owner):
    course, version, concepts, chunks, lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks])
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, gateway, dispatcher)
    core = service.request_activity(course.id, activity.id, owner.id)
    assert service.run(core.id, owner.id).status == PreparationStatus.READY
    core_artifact = core.content_artifact_id

    variant_response = service.content_response(course.id, activity.id, owner.id, "concise")
    assert variant_response["content"] is None
    variant = variant_response["preparation"]
    assert variant["status"] == PreparationStatus.PENDING
    assert len(dispatcher.enqueued) == 2
    variant_row = db_session.query(ActivityPreparation).filter_by(id=variant["id"]).one()
    assert variant_row.include_assessment is False
    assert service.run(variant_row.id, owner.id).status == PreparationStatus.READY
    concise_artifact = variant_row.content_artifact_id
    assert concise_artifact != core_artifact

    calls_after_variant = len(gateway.calls)
    saved_variant = service.content_response(course.id, activity.id, owner.id, "concise")
    assert saved_variant["content"]["artifact_id"] == concise_artifact
    assert len(gateway.calls) == calls_after_variant

    activity.status = ActivityStatus.COMPLETED.value
    db_session.commit()
    next_activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id) for concept in concepts],
        lesson_id=lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="concise",
    )
    db_session.add(next_activity)
    db_session.commit()
    reused = service.content_response(course.id, next_activity.id, owner.id, "concise")
    assert reused["content"]["artifact_id"] == concise_artifact
    assert len(gateway.calls) == calls_after_variant


def test_exhausted_allowance_and_provider_failure_keep_saved_content_for_retry(db_session, owner):
    course, version, concepts, chunks, lesson, first_activity = seed_published_activity(db_session, owner)
    dispatcher = RecordingDispatcher()
    initial_gateway = _configure_generation(
        FakeGenerationGateway(), [item.id for item in concepts], [item.id for item in chunks]
    )
    initial_service = ActivityPreparationService(db_session, initial_gateway, dispatcher)
    initial = initial_service.request_activity(course.id, first_activity.id, owner.id)
    ready = initial_service.run(initial.id, owner.id)
    assert ready.status == PreparationStatus.READY
    content_artifact_id = ready.content_artifact_id

    first_activity.status = ActivityStatus.COMPLETED.value
    next_activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(item.id) for item in concepts],
        lesson_id=lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="detailed",
    )
    db_session.add(next_activity)
    db_session.flush()
    usage = db_session.query(AIUsageDaily).filter_by(
        owner_id=owner.id, usage_date=datetime.now(timezone.utc).date()
    ).one()
    usage.call_count = 200
    db_session.commit()

    service = ActivityPreparationService(db_session, FakeGenerationGateway().set_default('{"supported": true}'), dispatcher)
    preparation = service.request_activity(course.id, next_activity.id, owner.id)
    exhausted = service.run(preparation.id, owner.id)
    assert exhausted.status == PreparationStatus.RECOVERABLE_FAILURE
    assert exhausted.error_category == "AI_ALLOWANCE_EXHAUSTED"
    assert exhausted.content_artifact_id == content_artifact_id
    assert db_session.query(LessonContentArtifact).count() == 1

    usage.call_count = 0
    db_session.commit()

    class QuestionProviderFailure(FakeGenerationGateway):
        def generate(self, prompt, **kwargs):
            if "Write exactly" in prompt:
                self.calls.append(prompt)
                raise GenerationError("fixture provider outage")
            self.calls.append(prompt)
            self.system_instructions.append(kwargs.get("system_instruction"))
            return '{"supported": true}'

    failing = ActivityPreparationService(db_session, QuestionProviderFailure(), dispatcher)
    retry = failing.retry(course.id, next_activity.id, owner.id)
    provider_failed = failing.run(retry.id, owner.id)
    assert provider_failed.status == PreparationStatus.RECOVERABLE_FAILURE
    assert provider_failed.error_category == "PROVIDER_UNAVAILABLE"
    assert provider_failed.content_artifact_id == content_artifact_id

    recovery_gateway = _configure_generation(
        FakeGenerationGateway(),
        [item.id for item in concepts],
        [item.id for item in chunks],
        prompt_offset=10,
    )
    recovery = ActivityPreparationService(db_session, recovery_gateway, dispatcher)
    retry_again = recovery.retry(course.id, next_activity.id, owner.id)
    final = recovery.run(retry_again.id, owner.id)
    assert final.status == PreparationStatus.READY
    assert final.content_artifact_id == content_artifact_id
    assert db_session.query(LessonContentArtifact).count() == 1
