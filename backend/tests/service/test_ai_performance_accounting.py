"""Actual gateway attempts and validation overlap, without live provider requests."""
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.main import app
from app.modules.abuse.models import AIProviderCall, AIUsageDaily
from app.modules.preparation.service import ActivityPreparationService, BudgetedGenerationGateway
from app.modules.tutor.entailment import GeminiEntailmentChecker
from app.modules.tutor.router import _service as tutor_dependency
from app.modules.tutor.service import TutorService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gemini import GeminiGenerationGateway
from app.services.vectorstore.fake import FakeVectorStore
from tests.api.test_preparation import RecordingDispatcher, seed_published_activity
from tests.conftest import auth_headers


class ResourceExhausted(Exception):
    pass


def sdk_gateway(*responses):
    gateway = GeminiGenerationGateway(api_key="fake-key")
    gateway._genai = MagicMock()
    gateway._genai.GenerativeModel.return_value.generate_content.side_effect = list(responses)
    return gateway


def test_sdk_retries_are_disabled_so_each_transport_attempt_has_one_deadline():
    gateway = sdk_gateway(SimpleNamespace(text="done"))
    gateway.generate("question")
    options = gateway._genai.GenerativeModel.return_value.generate_content.call_args.kwargs["request_options"]
    assert "retry" in options and options["retry"] is None


def test_preparation_counts_both_rate_limited_attempt_and_retry(db_session, owner):
    course, _version, _concepts, _chunks, _lesson, activity = seed_published_activity(db_session, owner)
    preparation = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher()).request_activity(course.id, activity.id, owner.id)
    token = uuid4()
    preparation.status = "RUNNING"
    preparation.lease_token = token
    preparation.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    db_session.commit()
    gateway = sdk_gateway(ResourceExhausted("private upstream payload"), SimpleNamespace(text="done"))
    with patch("app.services.generation.gemini.time.sleep"):
        assert BudgetedGenerationGateway(gateway, db_session, owner.id, preparation.id, token).generate("private prompt") == "done"
    db_session.refresh(preparation)
    assert preparation.provider_call_count == 2
    assert db_session.query(AIUsageDaily).filter_by(owner_id=owner.id).one().call_count == 2
    calls = db_session.query(AIProviderCall).order_by(AIProviderCall.started_at).all()
    assert [call.status for call in calls] == ["FAILED", "SUCCEEDED"]
    assert [call.retry_index for call in calls] == [0, 1]
    assert calls[0].error_category == "RATE_LIMITED"
    assert len({call.operation_id for call in calls}) == 1


def test_tutor_counts_answer_and_semantic_check_separately(client, db_session, owner, monkeypatch):
    course, _version, _concepts, chunks, _lesson, _activity = seed_published_activity(db_session, owner)
    answer = json.dumps({"insufficient_evidence": False, "answer_markdown": "The first course concept has a source passage.",
                         "claims": [{"text": "The first course concept has a source passage.", "chunk_id": str(chunks[0].id)}]})
    metadata = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15, cached_content_token_count=0)
    gateway = sdk_gateway(SimpleNamespace(text=answer, usage_metadata=metadata),
                          SimpleNamespace(text='{"results": [{"id": 0, "supported": true}]}', usage_metadata=metadata))
    monkeypatch.setitem(app.dependency_overrides, tutor_dependency, lambda: TutorService(db_session, gateway, FakeEmbeddingGateway(), FakeVectorStore()))
    reply = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "Explain the first course concept"}, headers=auth_headers(owner.email))
    assert reply.status_code == 200
    assert db_session.query(AIUsageDaily).filter_by(owner_id=owner.id).one().call_count == 2
    usage = client.get("/api/v1/ai/usage", headers=auth_headers(owner.email))
    assert usage.headers["cache-control"] == "no-store"
    body = usage.json()
    assert body["generation_budget_used"] == body["totals"]["generation_attempts"] == 2
    assert body["totals"]["reported_total_tokens"] == 30
    assert body["totals"]["token_usage_complete"] is True
    assert {call["phase"] for call in body["recent_calls"]} == {"generation", "validation"}
    assert len({call["operation_id"] for call in body["recent_calls"]}) == 1
    assert '"reported_total_tokens": 30' in reply.text


def test_usage_stays_owner_scoped_and_missing_tokens_are_explicit(client, db_session, owner, other_user):
    from app.services.ai_usage import ai_usage_scope
    from google.generativeai import protos
    for user, metadata in [(owner, protos.GenerateContentResponse.UsageMetadata()),
                           (other_user, SimpleNamespace(prompt_token_count=900, candidates_token_count=100, total_token_count=1000))]:
        gateway = sdk_gateway(SimpleNamespace(text="done", usage_metadata=metadata))
        with ai_usage_scope(db_session, user.id, "tutor"):
            gateway.generate("PRIVATE PROMPT SENTINEL")
    body = client.get("/api/v1/ai/usage", headers=auth_headers(owner.email)).json()
    assert body["totals"]["generation_attempts"] == 1
    assert body["totals"]["reported_total_tokens"] == 0
    assert body["totals"]["token_usage_complete"] is False
    assert body["recent_calls"][0]["input_tokens"] is None
    assert "PRIVATE PROMPT SENTINEL" not in json.dumps(body)
    assert "1000" not in json.dumps(body)


def test_quota_blocked_retry_does_not_send_or_count_a_second_attempt(db_session, owner):
    from app.core.problem_details import ProblemDetailException
    from app.services.ai_usage import ai_usage_scope
    db_session.add(AIUsageDaily(owner_id=owner.id, usage_date=datetime.now(timezone.utc).date(), call_count=199))
    db_session.commit()
    gateway = sdk_gateway(ResourceExhausted("private upstream error"), SimpleNamespace(text="never sent"))
    with ai_usage_scope(db_session, owner.id, "tutor"), patch("app.services.generation.gemini.time.sleep"):
        with pytest.raises(ProblemDetailException):
            gateway.generate("private prompt")
    assert gateway._genai.GenerativeModel.return_value.generate_content.call_count == 1
    assert db_session.query(AIUsageDaily).populate_existing().one().call_count == 200
    assert db_session.query(AIProviderCall).count() == 1


def test_embedding_retries_are_recorded_without_fabricating_token_usage(db_session, owner):
    from app.services.ai_usage import ai_usage_scope
    from app.services.embedding.gemini import GeminiEmbeddingGateway
    gateway = GeminiEmbeddingGateway(api_key="fake-key")
    gateway._client = MagicMock()
    gateway._client.embed_content.side_effect = [ResourceExhausted("private error"), {"embedding": [[0.1], [0.2]]}]
    with ai_usage_scope(db_session, owner.id, "retrieval"), patch("app.services.embedding.gemini.time.sleep"):
        assert gateway.embed_texts(["private passage 1", "private passage 2"]) == [[0.1], [0.2]]
    calls = db_session.query(AIProviderCall).order_by(AIProviderCall.started_at).all()
    assert [call.status for call in calls] == ["FAILED", "SUCCEEDED"]
    assert all(call.input_items == 2 and call.total_tokens is None for call in calls)
    assert db_session.query(AIUsageDaily).count() == 0
    assert gateway._client.embed_content.call_args.kwargs["request_options"]["retry"] is None


def test_expired_operation_sends_no_request_and_consumes_no_allowance(db_session, owner):
    from app.services.ai_usage import ai_usage_scope
    from app.services.generation.gateway import GenerationError
    gateway = sdk_gateway(SimpleNamespace(text="never sent"))
    with ai_usage_scope(db_session, owner.id, "tutor"), patch("app.services.ai_usage.time.monotonic", return_value=float("inf")):
        with pytest.raises(GenerationError, match="deadline"):
            gateway.generate("private prompt")
    assert gateway._genai.GenerativeModel.return_value.generate_content.call_count == 0
    assert db_session.query(AIUsageDaily).count() == 0
    assert db_session.query(AIProviderCall).count() == 0


def test_grading_retry_cannot_bypass_fixed_answer_call_limit(db_session, owner):
    from app.modules.learning.models import AnswerSubmission, AssessmentQuestion, AssessmentSession
    from app.modules.learning.service import BudgetedGradingGateway, GradingCallsExhausted
    from app.modules.mastery.service import MasteryService
    course, version, concepts, _chunks, _lesson, activity = seed_published_activity(db_session, owner)
    question = MasteryService(db_session, FakeGenerationGateway()).create_question(
        course.id, version.id, owner.id, "MCQ", "Which answer is supported?", {concepts[0].id: 1.0},
        options=["A", "B", "C", "D"], correct_answer="A",
    )
    session = AssessmentSession(activity_id=activity.id, course_version_id=version.id, assessment_type="ACTIVITY", status="SUBMITTED")
    db_session.add(session); db_session.flush()
    member = AssessmentQuestion(session_id=session.id, question_id=question.id, question_version=question.version, position=0)
    db_session.add(member); db_session.flush()
    token = uuid4()
    answer = AnswerSubmission(assessment_question_id=member.id, given_answer="text", status="AWAITING_GRADING",
                              grading_call_limit=1, grading_lease_token=token,
                              grading_lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120))
    db_session.add(answer); db_session.commit()
    gateway = sdk_gateway(ResourceExhausted("private error"), SimpleNamespace(text="must not send"))
    with patch("app.services.generation.gemini.time.sleep"), pytest.raises(GradingCallsExhausted):
        BudgetedGradingGateway(gateway, db_session, answer.id, owner.id, token).generate("grade")
    db_session.refresh(answer)
    assert answer.grading_attempt_count == 1
    assert gateway._genai.GenerativeModel.return_value.generate_content.call_count == 1
    assert db_session.query(AIUsageDaily).one().call_count == 1


def test_independent_source_batches_overlap_without_mixing_their_passages():
    started = [threading.Event(), threading.Event()]

    class ParallelGateway(FakeGenerationGateway):
        reports_provider_attempts = True

        def generate(self, prompt, **kwargs):
            payload = json.loads(prompt.split("BATCH CHECKS:\n", 1)[1].split("\n\nReturn ONLY JSON:", 1)[0])
            assert len(payload["sources"]) == 1
            index = int(payload["sources"]["s0"][-1])
            started[index].set()
            assert started[1 - index].wait(0.5), "Independent checks were unnecessarily serialized"
            return '{"results": [{"id": 0, "supported": true}]}'

    assert GeminiEntailmentChecker(ParallelGateway()).check_many([("first", "source 0"), ("second", "source 1")]) == [True, True]


def test_parallel_validation_outage_does_not_send_remaining_batches():
    import time
    from app.modules.tutor.entailment import EntailmentUnavailable
    from app.services.generation.gateway import GenerationError

    class FailingGateway(FakeGenerationGateway):
        reports_provider_attempts = True

        def generate(self, prompt, **kwargs):
            self.calls.append(prompt)
            payload = json.loads(prompt.split("BATCH CHECKS:\n")[1].split("\n\nReturn ONLY JSON:")[0])
            if payload["sources"]["s0"] == "source 0":
                raise GenerationError("private upstream outage")
            time.sleep(0.02)
            return '{"results": [{"id": 0, "supported": true}]}'

    gateway = FailingGateway()
    with pytest.raises(EntailmentUnavailable):
        GeminiEntailmentChecker(gateway).check_many([(f"claim {index}", f"source {index}") for index in range(12)])
    assert len(gateway.calls) <= 2


def test_slow_reservation_commit_cancels_dispatch_and_refunds_counters(db_session, owner, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.services.ai_usage import ai_usage_scope
    from app.services.generation.gateway import GenerationError
    course, _version, _concepts, _chunks, _lesson, activity = seed_published_activity(db_session, owner)
    preparation = ActivityPreparationService(db_session, FakeGenerationGateway(), RecordingDispatcher()).request_activity(course.id, activity.id, owner.id)
    token = uuid4()
    preparation.status = "RUNNING"
    preparation.lease_token = token
    preparation.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    db_session.commit()
    gateway = sdk_gateway(SimpleNamespace(text="must not send"))
    clock = [0.0]
    monkeypatch.setattr("app.services.ai_usage.time.monotonic", lambda: clock[0])

    def delayed_commit(session):
        clock[0] = 200.0

    event.listen(Session, "after_commit", delayed_commit)
    try:
        with ai_usage_scope(db_session, owner.id, "tutor"), pytest.raises(GenerationError, match="before dispatch"):
            BudgetedGenerationGateway(gateway, db_session, owner.id, preparation.id, token).generate("private prompt")
    finally:
        event.remove(Session, "after_commit", delayed_commit)
    db_session.refresh(preparation)
    assert gateway._genai.GenerativeModel.return_value.generate_content.call_count == 0
    assert preparation.provider_call_count == 0
    assert db_session.query(AIUsageDaily).one().call_count == 0
    assert db_session.query(AIProviderCall).one().status == "CANCELLED"
