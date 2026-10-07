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
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.modules.mastery.service import MasteryService
from app.modules.abuse.models import AIUsageDaily
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    PreparedActivityQuestion,
    PreparationStatus,
)
from app.modules.preparation.service import ActivityPreparationService, CandidateRejected, PreparationFailure
from app.modules.preparation.generation import (
    CONTENT_PROMPT_VERSION,
    CONTENT_SCHEMA_VERSION,
    P4_QUESTION_FRESHNESS_POLICY_VERSION,
    VALIDATION_POLICY_VERSION,
    parse_mcq_set,
)
from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError
from app.services.embedding.fake import FakeEmbeddingGateway
from tests.conftest import auth_headers


def _seed_p4_activity(db, owner, course, version, targets, activity_type, *, lesson_id=None):
    db.query(LearningActivity).filter(
        LearningActivity.owner_id == owner.id,
        LearningActivity.course_id == course.id,
    ).update({LearningActivity.status: ActivityStatus.COMPLETED.value}, synchronize_session=False)
    activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type=activity_type,
        target_concept_ids=[str(item.id) for item in targets],
        lesson_id=lesson_id,
        reason_text="Selected from the saved assessment evidence for these concepts.",
        status=ActivityStatus.READY.value,
        presentation_format="worked_example" if activity_type == "PREREQUISITE_REMEDIATION" else "concise",
    )
    db.add(activity)
    db.commit()
    return activity


def _p4_gateway(
    concept_ids, chunk_ids, *, prompt_offset=40, duplicate_prompt=False, content=False, content_variant=""
):
    gateway = FakeGenerationGateway().when_prompt_contains(
        "Write exactly 5 single-answer",
        _question_draft(
            concept_ids,
            chunk_ids,
            prompt_offset=prompt_offset,
            duplicate_prompt=duplicate_prompt,
        ),
    ).when_prompt_contains(
        "is also a supported correct answer.", '{"supported": false}'
    )
    if content:
        gateway.when_prompt_contains(
            "Prepare a focused remediation",
            _lesson_draft(concept_ids, chunk_ids, content_variant=content_variant),
        )
    gateway.set_default('{"supported": true}')
    return gateway


class RecordingDispatcher:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, preparation_id, owner_id, *, speculative=False):
        self.enqueued.append((preparation_id, owner_id, speculative))


def _lesson_draft(concept_ids, chunk_ids, *, unsupported=False, content_variant=""):
    def statement(text, concept_index=0):
        return {
            "text": text,
            "concept_ids": [str(concept_ids[concept_index % len(concept_ids)])],
            "citation_chunk_ids": [str(chunk_ids[concept_index % len(chunk_ids)])],
        }

    explanation = [statement(f"The source explains the first taught idea {content_variant}.")]
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


def test_selected_next_lesson_reuses_speculative_content_without_regenerating_it(db_session, owner):
    course, version, concepts, chunks, _first_lesson, first_activity = seed_published_activity(
        db_session, owner, with_next_lesson=True
    )
    next_lesson = (
        db_session.query(Lesson)
        .join(Module, Module.id == Lesson.module_id)
        .filter(Module.course_version_id == version.id, Lesson.title == "Next lesson")
        .one()
    )
    gateway = FakeGenerationGateway()
    gateway.when_prompt_contains(
        'LESSON: {"title": "Next lesson",',
        _lesson_draft([concepts[1].id], [chunks[1].id]),
    ).when_prompt_contains(
        f'CONCEPTS: [{{"id": "{concepts[1].id}"',
        _question_draft([concepts[1].id], [chunks[1].id], prompt_offset=20),
    ).when_prompt_contains(
        "Prepare the first lesson",
        _lesson_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks]),
    ).when_prompt_contains(
        "Write exactly 5 single-answer",
        _question_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks]),
    ).when_prompt_contains(
        "is also a supported correct answer.", '{"supported": false}'
    ).set_default('{"supported": true}')
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())

    first_preparation = service.request_activity(course.id, first_activity.id, owner.id)
    assert service.run(first_preparation.id, owner.id).status == PreparationStatus.READY
    speculative = (
        db_session.query(ActivityPreparation)
        .filter(ActivityPreparation.is_speculative.is_(True))
        .one()
    )
    assert speculative.lesson_id == next_lesson.id
    speculative_result = service.run(speculative.id, owner.id)
    assert speculative_result.status == PreparationStatus.READY, speculative_result.error_category
    saved_content_id = speculative.content_artifact_id
    next_content_calls = [
        prompt for prompt in gateway.calls if 'LESSON: {"title": "Next lesson",' in prompt
    ]
    assert saved_content_id is not None
    assert len(next_content_calls) == 1
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=speculative.id).count() == 0

    first_activity.status = ActivityStatus.COMPLETED.value
    next_activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concepts[1].id)],
        lesson_id=next_lesson.id,
        status=ActivityStatus.SELECTED.value,
        presentation_format="detailed",
    )
    db_session.add(next_activity)
    db_session.commit()

    selected_preparation = service.request_activity(course.id, next_activity.id, owner.id)
    assert service.run(selected_preparation.id, owner.id).status == PreparationStatus.READY
    assert selected_preparation.content_artifact_id == saved_content_id
    assert len([prompt for prompt in gateway.calls if 'LESSON: {"title": "Next lesson",' in prompt]) == 1
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=selected_preparation.id).count() == 5
    saved = service.content_response(course.id, next_activity.id, owner.id, "detailed")
    assert saved["content"]["artifact_id"] == saved_content_id


def test_submitted_lesson_results_select_next_activity_and_resume_reused_preparation(
    client, owner, db_session, fake_generation, monkeypatch
):
    course, version, concepts, chunks, _first_lesson, first_activity = seed_published_activity(db_session, owner)
    next_concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        canonical_key=f"p3-next-{uuid4().hex[:8]}",
        name="Next concept",
        definition="A later, source-backed concept.",
        importance=0.8,
    )
    db_session.add(next_concept)
    db_session.flush()
    next_source_text = "The next course concept is explained in this source passage."
    next_checksum = hashlib.sha256(next_source_text.encode()).hexdigest()
    next_document = Document(
        course_id=course.id,
        owner_id=owner.id,
        filename="next-concept.txt",
        role="STUDY",
        status="EXTRACTED",
        storage_path=f"/tmp/p3-{uuid4().hex}.txt",
        size_bytes=len(next_source_text),
        checksum_sha256=next_checksum,
    )
    db_session.add(next_document)
    db_session.flush()
    next_chunk = Chunk(
        id=uuid4(),
        document_id=next_document.id,
        course_id=course.id,
        owner_id=owner.id,
        position=0,
        text=next_source_text,
        char_count=len(next_source_text),
        token_count=10,
    )
    db_session.add(next_chunk)
    db_session.flush()
    checksums = [
        row[0]
        for row in db_session.query(Document.checksum_sha256).filter_by(course_id=course.id).all()
    ]
    version.source_fingerprint = hashlib.sha256("|".join(sorted(checksums)).encode()).hexdigest()
    next_lesson = Lesson(
        module_id=db_session.query(Module).filter_by(course_version_id=version.id).one().id,
        position=1,
        title="Next lesson",
        objective="Teach the next concept",
    )
    db_session.add(next_lesson)
    db_session.flush()
    db_session.add(LessonConcept(lesson_id=next_lesson.id, concept_id=next_concept.id, weight=1.0))
    db_session.add(
        ConceptSource(
            concept_id=next_concept.id,
            chunk_id=next_chunk.id,
            course_id=course.id,
            owner_id=owner.id,
        )
    )

    mastery = MasteryService(db_session, fake_generation, FakeEmbeddingGateway())
    for concept in concepts:
        question = mastery.create_question(
            course.id,
            version.id,
            owner.id,
            "MCQ",
            f"Prior fixture check for {concept.name}?",
            {concept.id: 1.0},
            options=["yes", "no"],
            correct_answer="yes",
            difficulty=0.5,
            commit=False,
        )
        for _ in range(9):
            mastery.record_graded_attempt(question, owner.id, "yes", 1.0, commit=False)
    db_session.commit()

    gateway = FakeGenerationGateway()
    gateway.when_prompt_contains(
        'LESSON: {"title": "Next lesson",',
        _lesson_draft([next_concept.id], [next_chunk.id]),
    ).when_prompt_contains(
        f'CONCEPTS: [{{"id": "{next_concept.id}"',
        _question_draft([next_concept.id], [next_chunk.id], prompt_offset=30),
    ).when_prompt_contains(
        "Prepare the first lesson",
        _lesson_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks]),
    ).when_prompt_contains(
        "Write exactly 5 single-answer",
        _question_draft([concept.id for concept in concepts], [chunk.id for chunk in chunks]),
    ).when_prompt_contains(
        "is also a supported correct answer.", '{"supported": false}'
    ).set_default('{"supported": true}')
    dispatcher = RecordingDispatcher()
    preparation = ActivityPreparationService(db_session, gateway, dispatcher)
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: preparation)
    headers = auth_headers(owner.email)

    first = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
    assert first.status_code == 200
    assert first.json()["id"] == str(first_activity.id)
    first_preparation = db_session.query(ActivityPreparation).filter_by(activity_id=first_activity.id).one()
    assert preparation.run(first_preparation.id, owner.id).status == PreparationStatus.READY

    speculative = (
        db_session.query(ActivityPreparation)
        .filter(ActivityPreparation.is_speculative.is_(True))
        .one()
    )
    assert speculative.lesson_id == next_lesson.id
    assert preparation.run(speculative.id, owner.id).status == PreparationStatus.READY
    saved_next_content_id = speculative.content_artifact_id
    next_content_prompts = [
        prompt for prompt in gateway.calls if 'LESSON: {"title": "Next lesson",' in prompt
    ]
    assert saved_next_content_id is not None
    assert len(next_content_prompts) == 1

    content = client.get(
        f"/api/v1/courses/{course.id}/activities/{first_activity.id}/content?format=detailed",
        headers=headers,
    )
    assert content.status_code == 200
    assert content.json()["content"]["artifact_id"] == str(first_preparation.content_artifact_id)
    completed_reading = client.post(
        f"/api/v1/courses/{course.id}/activities/{first_activity.id}/reading-complete",
        headers=headers,
    )
    assert completed_reading.status_code == 200
    session_response = client.post(
        f"/api/v1/courses/{course.id}/activities/{first_activity.id}/assessment",
        headers=headers,
    )
    assert session_response.status_code == 200
    session = session_response.json()
    session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
    for question in session["questions"]:
        saved_answer = client.post(
            f"{session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][0]},
            headers=headers,
        )
        assert saved_answer.status_code == 200
        assert all("result" not in row for row in saved_answer.json()["questions"])
    submitted = client.post(f"{session_url}/submit", headers=headers)
    assert submitted.status_code == 200
    assert submitted.json()["grading_state"] == "COMPLETE"
    assert submitted.json()["graded_answer_count"] == len(session["questions"])
    assert submitted.json()["concept_progress"]

    selected = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
    assert selected.status_code == 200
    selected_body = selected.json()
    assert selected_body["activity_type"] == "NEW_LESSON"
    assert selected_body["experience_availability"] == "SUPPORTED"
    assert selected_body["lesson_id"] == str(next_lesson.id)
    assert selected_body["target_concept_ids"] == [str(next_concept.id)]
    assert selected_body["preparation"]["status"] == "PENDING"
    selected_preparation = db_session.query(ActivityPreparation).filter_by(
        activity_id=UUID(selected_body["id"])
    ).one()

    assert preparation.run(selected_preparation.id, owner.id).status == PreparationStatus.READY
    assert selected_preparation.content_artifact_id == saved_next_content_id
    assert len([prompt for prompt in gateway.calls if 'LESSON: {"title": "Next lesson",' in prompt]) == 1
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=selected_preparation.id).count() == 5

    resumed = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["id"] == selected_body["id"]
    assert resumed.json()["decision_id"] == selected_body["decision_id"]
    assert resumed.json()["preparation"]["id"] == str(selected_preparation.id)
    assert resumed.json()["preparation"]["status"] == "READY"
    assert len([prompt for prompt in gateway.calls if 'LESSON: {"title": "Next lesson",' in prompt]) == 1


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


def test_legacy_ready_p2_preparation_reuses_saved_work_without_enqueue_or_ai_calls(db_session, owner):
    course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(
        FakeGenerationGateway(), [concept.id for concept in concepts], [chunk.id for chunk in chunks]
    )
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, gateway, dispatcher)
    original = service.request_activity(course.id, activity.id, owner.id)
    ready = service.run(original.id, owner.id)
    assert ready.status == PreparationStatus.READY
    saved_question_ids = [
        row.question_id
        for row in db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=ready.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    ]

    # P2/P3 stored activity-scoped keys without a purpose or target hash.
    ready.preparation_key = f"activity:{activity.id}:default"
    db_session.commit()
    call_count = len(gateway.calls)
    dispatcher.enqueued.clear()

    adopted = service.request_activity(course.id, activity.id, owner.id)

    assert adopted.id == ready.id
    assert adopted.status == PreparationStatus.READY
    assert dispatcher.enqueued == []
    assert len(gateway.calls) == call_count
    assert db_session.query(ActivityPreparation).filter_by(activity_id=activity.id).count() == 1
    assert [
        row.question_id
        for row in db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=adopted.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    ] == saved_question_ids


def test_legacy_ready_p2_published_preparation_is_adopted_by_first_activity(db_session, owner):
    course, version, concepts, chunks, lesson, activity = seed_published_activity(db_session, owner)
    gateway = _configure_generation(
        FakeGenerationGateway(), [concept.id for concept in concepts], [chunk.id for chunk in chunks]
    )
    dispatcher = RecordingDispatcher()
    service = ActivityPreparationService(db_session, gateway, dispatcher)
    published = service.prepare_first_activity(course.id, owner.id)
    assert published is not None
    ready = service.run(published.id, owner.id)
    assert ready is not None and ready.status == PreparationStatus.READY

    # P2 publication work used the lesson-only published-first key.
    ready.preparation_key = f"published-first:{version.id}:{lesson.id}:default"
    db_session.commit()
    call_count = len(gateway.calls)
    dispatcher.enqueued.clear()

    adopted = service.request_activity(course.id, activity.id, owner.id)

    assert adopted.id == ready.id
    assert adopted.activity_id == activity.id
    assert adopted.status == PreparationStatus.READY
    assert dispatcher.enqueued == []
    assert len(gateway.calls) == call_count


def test_legacy_p2_content_artifact_is_reused_for_compatible_lesson_preparation(db_session, owner):
    course, version, concepts, chunks, lesson, first_activity = seed_published_activity(db_session, owner)
    first_gateway = _configure_generation(
        FakeGenerationGateway(), [concept.id for concept in concepts], [chunk.id for chunk in chunks]
    )
    dispatcher = RecordingDispatcher()
    first_service = ActivityPreparationService(db_session, first_gateway, dispatcher)
    first_row = first_service.request_activity(course.id, first_activity.id, owner.id)
    ready = first_service.run(first_row.id, owner.id)
    assert ready.status == PreparationStatus.READY
    saved_artifact = db_session.query(LessonContentArtifact).filter_by(id=ready.content_artifact_id).one()

    # Reconstruct the P2 key shape retained by artifacts written before P4.
    ordered_concepts = sorted(concepts, key=lambda item: (item.name, str(item.id)))
    legacy_curriculum_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "lesson_id": str(lesson.id),
                "title": lesson.title,
                "objective": lesson.objective,
                "concepts": [
                    {"id": str(item.id), "name": item.name, "definition": item.definition}
                    for item in ordered_concepts
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    saved_artifact.artifact_key = hashlib.sha256(
        json.dumps(
            {
                "course_version_id": str(version.id),
                "source_fingerprint": version.source_fingerprint,
                "curriculum_fingerprint": legacy_curriculum_fingerprint,
                "lesson_id": str(lesson.id),
                "format": "detailed",
                "prompt": CONTENT_PROMPT_VERSION,
                "schema": CONTENT_SCHEMA_VERSION,
                "validation": VALIDATION_POLICY_VERSION,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    saved_artifact.curriculum_fingerprint = legacy_curriculum_fingerprint
    assert ActivityPreparationService._legacy_p2_artifact_key(
        version, lesson, ordered_concepts, "detailed"
    ) == (saved_artifact.artifact_key, legacy_curriculum_fingerprint)
    first_activity.status = ActivityStatus.COMPLETED.value
    next_activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id) for concept in reversed(concepts)],
        lesson_id=lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="detailed",
    )
    db_session.add(next_activity)
    db_session.commit()

    next_gateway = _configure_generation(
        FakeGenerationGateway(),
        [concept.id for concept in concepts],
        [chunk.id for chunk in chunks],
        prompt_offset=120,
    )
    next_service = ActivityPreparationService(db_session, next_gateway, dispatcher)
    next_row = next_service.request_activity(course.id, next_activity.id, owner.id)
    completed = next_service.run(next_row.id, owner.id)

    assert completed.status == PreparationStatus.READY
    assert completed.content_artifact_id == saved_artifact.id
    assert not any("Prepare the first lesson" in prompt for prompt in next_gateway.calls)


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


def test_targeted_practice_prepares_questions_and_uses_p3_results_without_reading(
    client, owner, db_session, monkeypatch
):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(db_session, owner, course, version, concepts[:1], "TARGETED_PRACTICE")
    gateway = _p4_gateway([concepts[0].id], [chunks[0].id])
    preparation_service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: preparation_service)
    headers = auth_headers(owner.email)
    activity_url = f"/api/v1/courses/{course.id}/activities/{activity.id}"

    selected = client.get(activity_url, headers=headers)
    assert selected.status_code == 200
    assert selected.json()["experience_availability"] == "SUPPORTED"
    assert selected.json().get("lesson_id") is None
    assert selected.json()["question_count"] == 5
    row = db_session.query(ActivityPreparation).filter_by(activity_id=activity.id).one()
    assert row.activity_purpose == "TARGETED_PRACTICE"
    assert row.lesson_id is None
    assert row.stage == "QUESTIONS"
    assert preparation_service.run(row.id, owner.id).status == PreparationStatus.READY

    refreshed = client.get(activity_url, headers=headers).json()
    assert refreshed["preparation"]["assessment_ready"] is True
    assert refreshed["preparation"]["content_ready"] is True
    assert refreshed.get("reading_completed_at") is None
    saved_questions = (
        db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=row.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    )
    assert len(saved_questions) == 5
    question_ids = [saved.question_id for saved in saved_questions]

    started = client.post(f"{activity_url}/assessment", headers=headers)
    assert started.status_code == 200
    session = started.json()
    assert [UUID(question["question_id"]) for question in session["questions"]] == question_ids
    assert db_session.query(LearningActivity).filter_by(id=activity.id).one().reading_completed_at is None
    session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
    for question in session["questions"]:
        confirmed = client.post(
            f"{session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][0]},
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert all("result" not in item for item in confirmed.json()["questions"])
    submitted = client.post(f"{session_url}/submit", headers=headers)
    assert submitted.status_code == 200
    assert [row["concept_id"] for row in submitted.json()["concept_progress"]] == [str(concepts[0].id)]
    assert submitted.json()["questions"][0]["result"]["source_chunk_ids"]


def test_challenge_has_single_concept_attribution_per_question_and_no_reading_gate(
    client, owner, db_session, monkeypatch
):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(db_session, owner, course, version, concepts, "CHALLENGE")
    preparation_service = ActivityPreparationService(
        db_session,
        _p4_gateway([item.id for item in concepts], [item.id for item in chunks]),
        RecordingDispatcher(),
    )
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: preparation_service)
    headers = auth_headers(owner.email)
    activity_url = f"/api/v1/courses/{course.id}/activities/{activity.id}"
    selected = client.get(activity_url, headers=headers)
    assert selected.status_code == 200
    assert selected.json().get("lesson_id") is None
    preparation = db_session.query(ActivityPreparation).filter_by(activity_id=activity.id).one()
    assert preparation_service.run(preparation.id, owner.id).status == PreparationStatus.READY
    duplicate_delivery = preparation_service.run(preparation.id, owner.id)
    assert duplicate_delivery.id == preparation.id
    assert duplicate_delivery.status == PreparationStatus.READY
    duplicate_request = preparation_service.request_activity(course.id, activity.id, owner.id)
    assert duplicate_request.id == preparation.id
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation.id).count() == 5
    memberships = (
        db_session.query(PreparedActivityQuestion, Question)
        .join(Question, Question.id == PreparedActivityQuestion.question_id)
        .filter(PreparedActivityQuestion.preparation_id == preparation.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    )
    attributed_concepts = []
    for membership, question in memberships:
        links = db_session.query(QuestionConcept).filter_by(question_id=question.id).all()
        assert len(links) == 1
        attributed_concepts.append(links[0].concept_id)
        assert membership.question_version == question.version
    assert len(memberships) == 5
    assert set(attributed_concepts) == {concept.id for concept in concepts}

    started = client.post(f"{activity_url}/assessment", headers=headers)
    assert started.status_code == 200
    session = started.json()
    session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
    for question in session["questions"]:
        saved = client.post(
            f"{session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][1]},
            headers=headers,
        )
        assert saved.status_code == 200
        assert all("result" not in item for item in saved.json()["questions"])
    submitted = client.post(f"{session_url}/submit", headers=headers)
    assert submitted.status_code == 200
    assert {row["concept_id"] for row in submitted.json()["concept_progress"]} == {
        str(concept.id) for concept in concepts
    }
    attempts = db_session.query(QuestionAttempt).filter_by(owner_id=owner.id, course_id=course.id).all()
    assert len(attempts) == 5
    assert {event.concept_id for event in db_session.query(MasteryEvent).filter_by(course_id=course.id).all()} == {
        concept.id for concept in concepts
    }


def test_remediation_prepares_focused_teaching_then_requires_reading_before_assessment(
    client, owner, db_session, monkeypatch
):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(
        db_session, owner, course, version, concepts[:1], "PREREQUISITE_REMEDIATION"
    )
    preparation_service = ActivityPreparationService(
        db_session,
        _p4_gateway([concepts[0].id], [chunks[0].id], content=True),
        RecordingDispatcher(),
    )
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: preparation_service)
    headers = auth_headers(owner.email)
    activity_url = f"/api/v1/courses/{course.id}/activities/{activity.id}"
    selected = client.get(activity_url, headers=headers)
    assert selected.status_code == 200
    assert selected.json()["question_count"] == 5
    preparation = db_session.query(ActivityPreparation).filter_by(activity_id=activity.id).one()
    assert preparation.activity_purpose == "PREREQUISITE_REMEDIATION"
    assert preparation_service.run(preparation.id, owner.id).status == PreparationStatus.READY

    content_response = client.get(f"{activity_url}/content?format=worked_example", headers=headers)
    assert content_response.status_code == 200
    content = content_response.json()["content"]
    assert content["lesson_id"] is None
    all_statements = [statement for values in content["sections"].values() for statement in values]
    assert all(set(statement["concept_ids"]) == {str(concepts[0].id)} for statement in all_statements)
    assert all(statement["citation_chunk_ids"] for statement in all_statements)

    before_reading = client.post(f"{activity_url}/assessment", headers=headers)
    assert before_reading.status_code == 409
    reading = client.post(f"{activity_url}/reading-complete", headers=headers)
    assert reading.status_code == 200
    assert reading.json()["reading_completed_at"] is not None
    assessment = client.post(f"{activity_url}/assessment", headers=headers)
    assert assessment.status_code == 200
    assert len(assessment.json()["questions"]) == 5
    assert all(
        len(db_session.query(QuestionConcept).filter_by(question_id=UUID(question["question_id"])).all()) == 1
        for question in assessment.json()["questions"]
    )


def test_repeated_remediation_uses_a_fresh_supported_explanation_and_question_set(db_session, owner):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    first_activity = _seed_p4_activity(
        db_session, owner, course, version, concepts[:1], "PREREQUISITE_REMEDIATION"
    )
    first_service = ActivityPreparationService(
        db_session,
        _p4_gateway(
            [concepts[0].id], [chunks[0].id], prompt_offset=70, content=True, content_variant="first approach"
        ),
        RecordingDispatcher(),
    )
    first_preparation = first_service.request_activity(course.id, first_activity.id, owner.id)
    assert first_service.run(first_preparation.id, owner.id).status == PreparationStatus.READY
    first_artifact = db_session.query(LessonContentArtifact).filter_by(
        id=first_preparation.content_artifact_id
    ).one()
    first_questions = (
        db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=first_preparation.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    )

    second_activity = _seed_p4_activity(
        db_session, owner, course, version, concepts[:1], "PREREQUISITE_REMEDIATION"
    )
    second_service = ActivityPreparationService(
        db_session,
        _p4_gateway(
            [concepts[0].id], [chunks[0].id], prompt_offset=150, content=True, content_variant="second approach"
        ),
        RecordingDispatcher(),
    )
    second_preparation = second_service.request_activity(course.id, second_activity.id, owner.id)
    assert second_preparation.id != first_preparation.id
    assert second_service.run(second_preparation.id, owner.id).status == PreparationStatus.READY
    second_artifact = db_session.query(LessonContentArtifact).filter_by(
        id=second_preparation.content_artifact_id
    ).one()
    second_questions = (
        db_session.query(PreparedActivityQuestion)
        .filter_by(preparation_id=second_preparation.id)
        .order_by(PreparedActivityQuestion.position)
        .all()
    )
    assert second_artifact.id != first_artifact.id
    assert second_artifact.artifact_key != first_artifact.artifact_key
    assert second_artifact.sections["explanation"] != first_artifact.sections["explanation"]
    assert len(first_questions) == len(second_questions) == 5
    assert {row.question_id for row in first_questions}.isdisjoint({row.question_id for row in second_questions})
    assert db_session.query(LessonContentArtifact).filter(
        LessonContentArtifact.activity_purpose == "PREREQUISITE_REMEDIATION"
    ).count() == 2


def test_missing_remediation_sources_remain_saved_and_recoverable(db_session, owner):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(
        db_session, owner, course, version, concepts[:1], "PREREQUISITE_REMEDIATION"
    )
    db_session.query(ConceptSource).filter_by(concept_id=concepts[0].id).delete()
    activity.reading_position = 31
    db_session.commit()
    service = ActivityPreparationService(
        db_session,
        _p4_gateway([concepts[0].id], [chunks[0].id], content=True),
        RecordingDispatcher(),
    )
    preparation = service.request_activity(course.id, activity.id, owner.id)
    failed = service.run(preparation.id, owner.id)
    assert failed.status == PreparationStatus.RECOVERABLE_FAILURE
    assert failed.error_category == "INSUFFICIENT_SOURCE_PROVENANCE"
    assert failed.content_artifact_id is None
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=failed.id).count() == 0
    saved_activity = db_session.query(LearningActivity).filter_by(id=activity.id).one()
    assert saved_activity.reading_position == 31
    retry = service.retry(course.id, activity.id, owner.id)
    assert retry.status == PreparationStatus.PENDING
    assert service.run(retry.id, owner.id).status == PreparationStatus.RECOVERABLE_FAILURE


def test_p4_freshness_failure_retries_without_replacing_the_saved_activity(db_session, owner):
    course, version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(db_session, owner, course, version, concepts[:1], "TARGETED_PRACTICE")
    dispatcher = RecordingDispatcher()
    failed_service = ActivityPreparationService(
        db_session,
        _p4_gateway([concepts[0].id], [chunks[0].id], duplicate_prompt=True),
        dispatcher,
    )
    row = failed_service.request_activity(course.id, activity.id, owner.id)
    failed = failed_service.run(row.id, owner.id)
    assert failed.status == PreparationStatus.RECOVERABLE_FAILURE
    assert failed.stage == "QUESTIONS"
    assert failed.content_artifact_id is None
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=failed.id).count() == 0

    recovery_service = ActivityPreparationService(
        db_session, _p4_gateway([concepts[0].id], [chunks[0].id], prompt_offset=120), dispatcher
    )
    retried = recovery_service.retry(course.id, activity.id, owner.id)
    assert retried.id == failed.id
    recovered = recovery_service.run(retried.id, owner.id)
    assert recovered.status == PreparationStatus.READY
    assert db_session.query(PreparedActivityQuestion).filter_by(preparation_id=failed.id).count() == 5
    assert db_session.query(LearningActivity).filter_by(id=activity.id).one().id == activity.id


def test_p4_question_validation_rejects_non_isolatable_concept_attribution(db_session, owner):
    course, _version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    service = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher())
    source_by_id = {chunk.id: chunk for chunk in chunks[:1]}
    chunks_by_concept = {concepts[0].id: {chunks[0].id}}

    def checker(claim, _passage):
        return "without needing another selected concept" not in claim

    try:
        service._validate_question_set(
            parse_mcq_set(_question_draft([concepts[0].id], [chunks[0].id])),
            concepts[:1],
            source_by_id,
            chunks_by_concept,
            checker,
            5,
            set(),
            P4_QUESTION_FRESHNESS_POLICY_VERSION,
        )
    except CandidateRejected as exc:
        assert exc.category == "QUESTION_ATTRIBUTION_NOT_ISOLATED"
    else:
        raise AssertionError("P4 must reject a question that cannot isolate its attributed concept")


def test_activity_purpose_and_target_sets_do_not_share_preparation_or_artifact_keys(db_session, owner):
    _course, version, concepts, _chunks, lesson, _activity = seed_published_activity(db_session, owner)
    service = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher())
    new_lesson_key = service._artifact_key(version, lesson, concepts[:1], "detailed", "NEW_LESSON")[0]
    remediation_key = service._artifact_key(
        version, lesson, concepts[:1], "detailed", "PREREQUISITE_REMEDIATION"
    )[0]
    assert new_lesson_key != remediation_key
    prep_activity_id = uuid4()
    practice_preparation_key = service._prep_key(
        prep_activity_id, version.id, None, "__activity_default__", False, "TARGETED_PRACTICE", [concepts[0].id]
    )
    challenge_preparation_key = service._prep_key(
        prep_activity_id, version.id, None, "__activity_default__", False, "CHALLENGE", [concepts[0].id]
    )
    changed_target_key = service._prep_key(
        prep_activity_id, version.id, None, "__activity_default__", False, "TARGETED_PRACTICE", [concepts[1].id]
    )
    assert practice_preparation_key != challenge_preparation_key
    assert practice_preparation_key != changed_target_key


def test_lesson_weak_result_remediation_reassessment_and_continue_cycle(
    client, owner, db_session, monkeypatch
):
    course, version, concepts, chunks, _lesson, lesson_activity = seed_published_activity(db_session, owner)
    initial_gateway = _configure_generation(
        FakeGenerationGateway(),
        [concept.id for concept in concepts],
        [chunk.id for chunk in chunks],
    )
    dispatcher = RecordingDispatcher()
    initial_preparation_service = ActivityPreparationService(db_session, initial_gateway, dispatcher)
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: initial_preparation_service)
    headers = auth_headers(owner.email)
    lesson_url = f"/api/v1/courses/{course.id}/activities/{lesson_activity.id}"
    selected_lesson = client.get(lesson_url, headers=headers)
    assert selected_lesson.status_code == 200
    lesson_preparation = db_session.query(ActivityPreparation).filter_by(activity_id=lesson_activity.id).one()
    assert initial_preparation_service.run(lesson_preparation.id, owner.id).status == PreparationStatus.READY
    assert client.post(f"{lesson_url}/reading-complete", headers=headers).status_code == 200

    first_assessment = client.post(f"{lesson_url}/assessment", headers=headers)
    assert first_assessment.status_code == 200
    first_session = first_assessment.json()
    first_session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{first_session['id']}"
    for question in first_session["questions"]:
        answered = client.post(
            f"{first_session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][1]},
            headers=headers,
        )
        assert answered.status_code == 200
        assert all("result" not in item for item in answered.json()["questions"])
    first_results = client.post(f"{first_session_url}/submit", headers=headers)
    assert first_results.status_code == 200
    assert len(first_results.json()["concept_progress"]) == 2

    # Continue persists the recommendation first; preparation begins when that
    # exact saved activity is loaded, so the fixture can target its concept.
    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: None)
    remediation_response = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
    assert remediation_response.status_code == 200
    remediation = remediation_response.json()
    assert remediation["activity_type"] == "PREREQUISITE_REMEDIATION"
    assert len(remediation["target_concept_ids"]) == 1
    assert "Needs attention" in remediation["reason"]
    remediation_target = UUID(remediation["target_concept_ids"][0])
    target_index = next(index for index, concept in enumerate(concepts) if concept.id == remediation_target)
    remediation_service = ActivityPreparationService(
        db_session,
        _p4_gateway([remediation_target], [chunks[target_index].id], prompt_offset=90, content=True),
        dispatcher,
    )
    monkeypatch.setitem(app.dependency_overrides, preparation_dependency, lambda: remediation_service)
    remediation_url = f"/api/v1/courses/{course.id}/activities/{remediation['id']}"
    loaded_remediation = client.get(remediation_url, headers=headers)
    assert loaded_remediation.status_code == 200
    remediation_preparation = db_session.query(ActivityPreparation).filter_by(activity_id=UUID(remediation["id"])).one()
    assert remediation_service.run(remediation_preparation.id, owner.id).status == PreparationStatus.READY
    grounded = client.get(
        f"{remediation_url}/content?format={remediation['presentation_format']}", headers=headers
    )
    assert grounded.status_code == 200
    assert grounded.json()["content"]["source_chunk_ids"]
    assert client.post(f"{remediation_url}/reading-complete", headers=headers).status_code == 200

    reassessment = client.post(f"{remediation_url}/assessment", headers=headers)
    assert reassessment.status_code == 200
    remediation_session = reassessment.json()
    remediation_session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{remediation_session['id']}"
    for question in remediation_session["questions"]:
        answered = client.post(
            f"{remediation_session_url}/questions/{question['question_id']}/answer",
            json={"given_answer": question["options"][0]},
            headers=headers,
        )
        assert answered.status_code == 200
    results = client.post(f"{remediation_session_url}/submit", headers=headers)
    assert results.status_code == 200
    assert [row["concept_id"] for row in results.json()["concept_progress"]] == [str(remediation_target)]

    continued = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
    assert continued.status_code == 200
    assert continued.json()["id"] != remediation["id"]
    assert continued.json()["activity_type"] in {
        "PREREQUISITE_REMEDIATION", "TARGETED_PRACTICE", "NEW_LESSON", "CHALLENGE"
    }
