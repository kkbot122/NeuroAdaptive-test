"""Real PostgreSQL attempt reservations and Redis capacity coordination."""
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import redis
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.ai_limits import AICapacityUnavailable, shared_slot
from app.core.config import settings
from app.modules.abuse.models import AIProviderCall, AIUsageDaily
from app.modules.preparation.models import PreparedActivityQuestion
from app.modules.preparation.service import ActivityPreparationService
from app.modules.tutor.entailment import GeminiEntailmentChecker
from app.services.ai_usage import ai_usage_scope
from app.services.generation.gemini import GeminiGenerationGateway
from tests.api.test_preparation import RecordingDispatcher, _lesson_draft, _question_draft, seed_published_activity
from tests.integration.test_learning_postgresql import postgres_schema_engine  # noqa: F401
from app.modules.auth.models import User

pytestmark = pytest.mark.integration


@pytest.fixture()
def ai_schema_engine(postgres_schema_engine):
    from app.db.base import Base
    with postgres_schema_engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    engine = postgres_schema_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    yield engine


@pytest.fixture()
def live_redis(monkeypatch):
    url = os.getenv("AI_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set AI_TEST_REDIS_URL to isolated Redis database 14 or 15")
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path in {"/14", "/15"}
    monkeypatch.setattr(settings, "AI_SHARED_LIMITS_ENABLED", True)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", url)
    client = redis.Redis.from_url(url)
    # Only the accounting test keys in an explicitly isolated database.
    yield client
    for key in client.scan_iter("ai:*"):
        client.delete(key)
    client.close()


def test_concurrent_preparation_attempts_match_postgres_ledger_and_daily_budget(ai_schema_engine):
    with Session(ai_schema_engine) as db:
        owner = User(email=f"ai-{uuid4()}@example.com", is_active=True)
        db.add(owner); db.commit()
        course, _version, concepts, chunks, _lesson, activity = seed_published_activity(db, owner)
        gateway = GeminiGenerationGateway(api_key="fake-key")
        gateway._genai = MagicMock()

        def respond(prompt, **kwargs):
            if "Prepare the first lesson" in prompt:
                result = _lesson_draft([item.id for item in concepts], [item.id for item in chunks])
            elif "Write exactly" in prompt:
                result = _question_draft([item.id for item in concepts], [item.id for item in chunks])
            else:
                payload = json.loads(prompt.split("BATCH CHECKS:\n")[1].split("\n\nReturn ONLY JSON:")[0])
                assert len(payload["sources"]) == 1
                result = json.dumps({"results": [{"id": check["id"], "supported": "is also a supported correct answer." not in check["claim"]}
                                                  for check in payload["checks"]]})
                time.sleep(0.01)
            return SimpleNamespace(text=result, usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                                                               total_token_count=15, cached_content_token_count=0))

        gateway._genai.GenerativeModel.return_value.generate_content.side_effect = respond
        service = ActivityPreparationService(db, gateway, RecordingDispatcher())
        preparation = service.request_activity(course.id, activity.id, owner.id)
        with ai_usage_scope(db, owner.id, "preparation", preparation.id, worker=True):
            result = service.run(preparation.id, owner.id)
        assert result.status == "READY"
        physical_calls = gateway._genai.GenerativeModel.return_value.generate_content.call_count
        assert result.provider_call_count == physical_calls
        assert db.query(AIUsageDaily).filter_by(owner_id=owner.id).one().call_count == physical_calls
        assert db.query(AIProviderCall).filter_by(owner_id=owner.id).count() == physical_calls
        assert db.query(PreparedActivityQuestion).filter_by(preparation_id=preparation.id).count() == 5
        assert len(db.query(AIProviderCall.operation_id).filter_by(owner_id=owner.id).distinct().all()) == 1
        assert db.query(func.sum(AIProviderCall.total_tokens)).filter_by(owner_id=owner.id).scalar() == 15 * physical_calls


def test_parallel_validation_uses_separate_sessions_and_overlaps_real_attempts(ai_schema_engine):
    with Session(ai_schema_engine) as db:
        owner = User(email=f"ai-{uuid4()}@example.com", is_active=True)
        db.add(owner); db.commit()
        starts = [threading.Event(), threading.Event()]
        gateway = GeminiGenerationGateway(api_key="fake-key")
        gateway._genai = MagicMock()

        def respond(prompt, **kwargs):
            payload = json.loads(prompt.split("BATCH CHECKS:\n")[1].split("\n\nReturn ONLY JSON:")[0])
            index = int(payload["sources"]["s0"][-1])
            starts[index].set()
            assert starts[1 - index].wait(2), "PostgreSQL attempt accounting serialized independent validation"
            return SimpleNamespace(text='{"results": [{"id": 0, "supported": true}]}')

        gateway._genai.GenerativeModel.return_value.generate_content.side_effect = respond
        with ai_usage_scope(db, owner.id, "tutor"):
            assert GeminiEntailmentChecker(gateway).check_many([("first", "source 0"), ("second", "source 1")]) == [True, True]
        assert db.query(AIUsageDaily).filter_by(owner_id=owner.id).one().call_count == 2
        assert db.query(AIProviderCall).filter_by(owner_id=owner.id, status="SUCCEEDED").count() == 2


def test_redis_slots_are_shared_across_processes_and_released(live_redis):
    from app.core.ai_limits import _ACQUIRE
    key = f"ai:integration:{uuid4().hex}"
    script = "import redis,sys,time; c=redis.Redis.from_url(sys.argv[1]); n=int(time.time()*1000); print(c.eval(sys.argv[3],1,sys.argv[2],n,n+5000,'child',1))"
    command = [sys.executable, "-c", script, settings.CELERY_BROKER_URL, key, _ACQUIRE]
    with shared_slot({key: 1}, ttl_seconds=5):
        assert subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip() == "0"
    assert subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip() == "1"


def test_worker_capacity_leaves_interactive_slots_and_token_expiry_recovers(live_redis):
    prefix = f"ai:integration:{uuid4().hex}"
    total, worker = f"{prefix}:total", f"{prefix}:worker"
    with shared_slot({total: 2, worker: 1}, ttl_seconds=5):
        with pytest.raises(AICapacityUnavailable):
            with shared_slot({total: 2, worker: 1}, ttl_seconds=5):
                pass
        with shared_slot({total: 2}, ttl_seconds=5):
            assert live_redis.zcard(total) == 2
    # A crashed holder leaves a token; acquisition removes expired leases.
    live_redis.zadd(total, {"abandoned": int(time.time() * 1000) - 1})
    with shared_slot({total: 1}, ttl_seconds=5):
        assert live_redis.zcard(total) == 1


def test_shorter_token_does_not_expire_a_longer_held_slot(live_redis):
    key = f"ai:integration:{uuid4().hex}"
    with shared_slot({key: 2}, ttl_seconds=5):
        with shared_slot({key: 2}, ttl_seconds=1):
            assert live_redis.pttl(key) > 4000


def test_database_contention_stops_before_dispatch_and_does_not_consume_budget(ai_schema_engine, monkeypatch):
    from app.services.generation.gateway import GenerationError
    from datetime import datetime, timezone
    monkeypatch.setattr(settings, "AI_ACCOUNTING_RESERVATION_TIMEOUT_SECONDS_V1", 0.1)
    with Session(ai_schema_engine) as db:
        owner = User(email=f"ai-{uuid4()}@example.com", is_active=True)
        db.add(owner); db.commit()
        db.add(AIUsageDaily(owner_id=owner.id, usage_date=datetime.now(timezone.utc).date(), call_count=0))
        db.commit()
        with Session(ai_schema_engine) as blocker:
            blocker.query(AIUsageDaily).filter_by(owner_id=owner.id).with_for_update().one()
            gateway = GeminiGenerationGateway(api_key="fake-key")
            gateway._genai = MagicMock()
            with ai_usage_scope(db, owner.id, "tutor"), pytest.raises(GenerationError, match="reservation"):
                gateway.generate("not sent")
            assert gateway._genai.GenerativeModel.return_value.generate_content.call_count == 0
            blocker.rollback()
        assert db.query(AIUsageDaily).filter_by(owner_id=owner.id).populate_existing().one().call_count == 0
        assert db.query(AIProviderCall).filter_by(owner_id=owner.id).count() == 0


def test_processing_stage_reports_embedding_retry_from_the_same_ledger(ai_schema_engine):
    from unittest.mock import patch
    from app.modules.jobs.service import JobService
    from app.modules.jobs.router import _out
    from app.services.embedding.gemini import GeminiEmbeddingGateway
    from app.services.vectorstore.fake import FakeVectorStore

    class ResourceExhausted(Exception):
        pass

    with Session(ai_schema_engine) as db:
        owner = User(email=f"ai-{uuid4()}@example.com", is_active=True)
        db.add(owner); db.commit()
        course, _version, _concepts, chunks, _lesson, _activity = seed_published_activity(db, owner)
        embeddings = GeminiEmbeddingGateway(api_key="fake-key")
        embeddings._client = MagicMock()
        embeddings._client.embed_content.side_effect = [ResourceExhausted("private error"),
            {"embedding": [[0.1] * embeddings.dimensions for _ in chunks]}]
        service = JobService(db, embeddings=embeddings, vectors=FakeVectorStore())
        job = service.create_for_course(course.id, owner.id)
        stage = next(stage for stage in job.stages if stage.name == "INDEXING")
        with ai_usage_scope(db, owner.id, "processing", job.id, worker=True), patch("app.services.embedding.gemini.time.sleep"):
            service._run_stage(job, stage)
        assert stage.provider_call_count == 2
        assert next(item for item in _out(job)["stages"] if item["name"] == "INDEXING")["provider_call_count"] == 2
        assert db.query(AIProviderCall).filter_by(resource_id=job.id, phase="INDEXING").count() == 2
