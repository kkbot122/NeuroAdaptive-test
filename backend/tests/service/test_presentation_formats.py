"""Grounded connections and upgrades without replacing fixed assessment questions."""
import json

import pytest
from pydantic import ValidationError

from app.modules.learning.models import AssessmentSession
from app.modules.preparation.generation import (
    DIAGRAM_PROMPT_VERSION, DIAGRAM_SCHEMA_VERSION, DIAGRAM_VALIDATION_POLICY_VERSION, parse_lesson_content,
)
from app.modules.preparation.models import LessonContentArtifact, PreparedActivityQuestion
from app.modules.preparation.service import ActivityPreparationService, CandidateRejected
from tests.api.test_preparation import (
    RecordingDispatcher, _configure_generation, _lesson_draft, _seed_p4_activity, seed_published_activity,
)
from tests.preparation_generation import PreparationGenerationGateway


def diagram_payload(concepts, chunks):
    payload = json.loads(_lesson_draft([item.id for item in concepts], [item.id for item in chunks]))
    if len(concepts) == 1:
        payload["explanation"].append({**payload["explanation"][0], "text": "Another supported feature of this concept."})
    payload["diagram_edges"] = [{
        "from_index": 0, "to_index": 1, "text": "The first idea has a source-supported connection to the second.",
        "concept_ids": [str(item.id) for item in concepts], "citation_chunk_ids": [str(item.id) for item in chunks],
    }]
    return payload


def validate(db, concepts, chunks, payload, checker=lambda claim, source: True):
    return ActivityPreparationService(db, PreparationGenerationGateway(), RecordingDispatcher())._validate_content_draft(
        parse_lesson_content(json.dumps(payload)), concepts,
        {concept.id: {chunks[index].id} for index, concept in enumerate(concepts)},
        {chunk.id: chunk for chunk in chunks}, checker, "diagram",
    )


def test_connections_check_endpoint_meaning_and_direction_not_just_the_label(db_session, owner):
    _course, _version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    payload = diagram_payload(concepts, chunks)
    checks = []

    def checker(claim, source):
        checks.append((claim, source))
        return True

    sections = validate(db_session, concepts, chunks, payload, checker)
    relationship, source = next(pair for pair in checks if "AND its direction" in pair[0])
    assert f'FROM [{payload["explanation"][0]["text"]}]' in relationship
    assert f'TO [{payload["explanation"][1]["text"]}]' in relationship
    assert all(chunk.text in source for chunk in chunks)
    assert sections["diagram_edges"][0]["from_index"] == 0
    assert sections["diagram_edges"][0]["to_index"] == 1


@pytest.mark.parametrize("failure", ["relationship", "node", "missing", "out_of_range", "duplicate", "foreign_source", "endpoint_concepts"])
def test_unchecked_or_invalid_connections_never_become_a_diagram(db_session, owner, failure):
    from uuid import uuid4
    _course, _version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    payload = diagram_payload(concepts, chunks)
    if failure == "missing":
        payload["diagram_edges"] = []
    elif failure == "out_of_range":
        payload["diagram_edges"][0]["to_index"] = 10
    elif failure == "duplicate":
        payload["diagram_edges"] *= 2
    elif failure == "foreign_source":
        payload["diagram_edges"][0]["citation_chunk_ids"] = [str(uuid4())]
    elif failure == "endpoint_concepts":
        payload["diagram_edges"][0]["concept_ids"] = [str(concepts[0].id)]

    def checker(claim, source):
        return not ((failure == "relationship" and "AND its direction" in claim)
                    or (failure == "node" and claim == payload["explanation"][0]["text"]))

    with pytest.raises(CandidateRejected):
        validate(db_session, concepts, chunks, payload, checker)


@pytest.mark.parametrize("index", [True, -1, 12, 0])
def test_connections_reject_coerced_invalid_or_identical_indexes(db_session, owner, index):
    _course, _version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    payload = diagram_payload(concepts, chunks)
    payload["diagram_edges"][0]["to_index"] = index
    with pytest.raises(ValidationError):
        parse_lesson_content(json.dumps(payload))


def test_old_diagram_content_is_upgraded_without_regenerating_fixed_questions(db_session, owner):
    course, version, concepts, chunks, lesson, activity = seed_published_activity(db_session, owner)
    dispatcher = RecordingDispatcher()
    gateway = _configure_generation(PreparationGenerationGateway(), [c.id for c in concepts], [c.id for c in chunks])
    service = ActivityPreparationService(db_session, gateway, dispatcher)
    preparation = service.request_activity(course.id, activity.id, owner.id)
    service.run(preparation.id, owner.id)
    question_ids = [row.question_id for row in db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation.id)]
    saved_artifact_id = preparation.content_artifact_id
    old = db_session.get(LessonContentArtifact, saved_artifact_id)
    old.presentation_format = "diagram"
    preparation.presentation_format = "diagram"
    activity.presentation_format = "diagram"
    db_session.commit()

    diagram_gateway = PreparationGenerationGateway().when_prompt_contains("Prepare the first lesson", json.dumps(diagram_payload(concepts, chunks))).set_default('{"supported": true}')
    upgrade = ActivityPreparationService(db_session, diagram_gateway, dispatcher)
    artifact, pending = upgrade.request_format(course.id, activity.id, owner.id, "diagram")
    assert artifact is None and pending.id == preparation.id
    assert pending.status == "PENDING" and pending.stage == "CONTENT"
    assert pending.content_artifact_id is None
    assert upgrade.run(pending.id, owner.id).status == "READY"
    assert [row.question_id for row in db_session.query(PreparedActivityQuestion).filter_by(preparation_id=preparation.id)] == question_ids
    assert db_session.query(AssessmentSession).count() == 0
    content = upgrade.content_response(course.id, activity.id, owner.id, "diagram")["content"]
    assert content["sections"]["diagram_edges"]
    assert content["artifact_id"] != saved_artifact_id
    assert db_session.get(LessonContentArtifact, saved_artifact_id) is not None
    new = db_session.get(LessonContentArtifact, content["artifact_id"])
    assert (new.prompt_version, new.schema_version, new.validation_policy_version) == (
        DIAGRAM_PROMPT_VERSION, DIAGRAM_SCHEMA_VERSION, DIAGRAM_VALIDATION_POLICY_VERSION,
    )
    assert not any("Write exactly" in prompt for prompt in diagram_gateway.calls)


def test_remediation_diagram_has_the_same_grounding_contract_and_provenance(db_session, owner):
    course, _version, concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    activity = _seed_p4_activity(db_session, owner, course, _version, concepts[:1], "PREREQUISITE_REMEDIATION")
    gateway = PreparationGenerationGateway().when_prompt_contains("Prepare a focused remediation", json.dumps(diagram_payload(concepts[:1], chunks[:1]))).set_default('{"supported": true}')
    service = ActivityPreparationService(db_session, gateway, RecordingDispatcher())
    _artifact, preparation = service.request_format(course.id, activity.id, owner.id, "diagram")
    assert not preparation.include_assessment
    assert service.run(preparation.id, owner.id).status == "READY"
    saved = db_session.get(LessonContentArtifact, preparation.content_artifact_id)
    assert saved.prompt_version == DIAGRAM_PROMPT_VERSION
    assert saved.sections["diagram_edges"]
