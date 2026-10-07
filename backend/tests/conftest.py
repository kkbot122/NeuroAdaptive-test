"""
Shared test fixtures.

Environment is set before any app module is imported, because
app.core.config.Settings is instantiated at import time and now requires
INTERNAL_API_KEY and SECRET_KEY to be present and strong.
"""
import os

TEST_INTERNAL_TOKEN = "test-internal-token-" + "x" * 24
TEST_SECRET_KEY = "test-secret-key-" + "y" * 24

os.environ.setdefault("INTERNAL_API_KEY", TEST_INTERNAL_TOKEN)
os.environ.setdefault("SECRET_KEY", TEST_SECRET_KEY)
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

# Uploaded files must never land in the repo during a test run.
import tempfile
os.environ.setdefault("DOCUMENT_STORAGE_ROOT", tempfile.mkdtemp(prefix="neurolearn-test-uploads-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.models import User  # noqa: E402
from app.modules.content.models import Article, Paragraph  # noqa: E402
from app.modules.profiling.models import UserProfile  # noqa: E402


@pytest.fixture()
def db_session():
    """
    A SQLite in-memory database with the full schema.

    StaticPool keeps every connection pointed at the same in-memory database;
    without it each connection gets its own empty one.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def fake_embeddings():
    """
    Shared across the whole test session's request lifecycle for one test:
    deterministic, offline, no API key needed.
    """
    from app.services.embedding.fake import FakeEmbeddingGateway

    return FakeEmbeddingGateway()


@pytest.fixture()
def fake_vectors():
    """An in-memory vector store, shared by job processing and retrieval
    within one test so indexing and querying see the same data."""
    from app.services.vectorstore.fake import FakeVectorStore

    return FakeVectorStore()


@pytest.fixture()
def fake_generation():
    """
    A nonempty synthetic concept exercises validation rather than relying on
    an empty course passing vacuous checks. Tests of insufficient generation
    explicitly replace the response with an empty concept list.
    """
    from app.services.generation.fake import FakeGenerationGateway

    return FakeGenerationGateway().set_default('{"concepts": [{"name": "Study concept", "definition": "A concept in the synthetic test material.", "importance": 0.5}], "edges": []}')


@pytest.fixture()
def client(db_session, fake_embeddings, fake_vectors, fake_generation):
    """
    TestClient with the database dependency pointed at the test session, and
    the job/retrieval service factories overridden to use fake, offline
    embedding and vector-store providers.

    No test in this suite may depend on a reachable Gemini API or database
    vector extension: doing so makes the suite slow, flaky, and dependent on
    provider credentials or external infrastructure. A test that exercises
    the real pgvector adapter does so as a narrow integration test, not
    through this fixture.
    """
    from app.modules.adaptation.router import _service as adaptation_service_dep
    from app.modules.adaptation.service import AdaptationService
    from app.modules.jobs.router import _service as job_service_dep
    from app.modules.jobs.router import _dispatcher as job_dispatcher_dep
    from app.modules.jobs.service import JobService
    from app.modules.mastery.router import _service as mastery_service_dep
    from app.modules.mastery.service import MasteryService
    from app.modules.mastery.router import _learning_service as mastery_learning_service_dep
    from app.modules.learning.router import _service as learning_service_dep
    from app.modules.learning.service import LearningService
    from app.modules.learning.router import _preparation_service as preparation_service_dep
    from app.modules.retrieval.router import _service as retrieval_service_dep
    from app.modules.retrieval.service import RetrievalService
    from app.modules.tutor.router import _service as tutor_service_dep
    from app.modules.tutor.service import TutorService

    fake_generation.when_prompt_contains('"criteria_met"', '{"criteria_met": [true, true, true]}')

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[job_service_dep] = lambda: JobService(
        db_session, embeddings=fake_embeddings, vectors=fake_vectors, generation=fake_generation
    )
    class InlineJobDispatcher:
        def enqueue(self, job_id, owner_id):
            JobService(
                db_session,
                embeddings=fake_embeddings,
                vectors=fake_vectors,
                generation=fake_generation,
            ).run(job_id, owner_id)

    app.dependency_overrides[job_dispatcher_dep] = InlineJobDispatcher
    app.dependency_overrides[retrieval_service_dep] = lambda: RetrievalService(
        db_session, fake_embeddings, fake_vectors
    )
    app.dependency_overrides[mastery_service_dep] = lambda: MasteryService(
        db_session, fake_generation, fake_embeddings
    )
    class InlineGradingDispatcher:
        def enqueue(self, answer_submission_id, owner_id):
            LearningService(db_session, fake_generation, fake_embeddings).grade_saved_answer(
                answer_submission_id, owner_id
            )

    app.dependency_overrides[mastery_learning_service_dep] = lambda: LearningService(
        db_session, fake_generation, fake_embeddings
    )
    app.dependency_overrides[learning_service_dep] = lambda: LearningService(
        db_session, fake_generation, fake_embeddings, InlineGradingDispatcher()
    )
    # Existing lifecycle tests exercise the P1 contracts directly. P2 tests
    # opt into a deterministic preparation dispatcher explicitly.
    app.dependency_overrides[preparation_service_dep] = lambda: None
    app.dependency_overrides[adaptation_service_dep] = lambda: AdaptationService(
        db_session, fake_generation, fake_embeddings
    )
    app.dependency_overrides[tutor_service_dep] = lambda: TutorService(
        db_session, fake_generation, fake_embeddings, fake_vectors
    )

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def owner(db_session) -> User:
    """The user who owns the fixture content."""
    user = User(email="owner@example.com", full_name="Owner", is_active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    db_session.add(UserProfile(user_id=user.id, primary_archetype="THE_PIONEER"))
    db_session.commit()
    return user


@pytest.fixture()
def other_user(db_session) -> User:
    """A second, unrelated user — used for negative-authorization cases."""
    user = User(email="intruder@example.com", full_name="Intruder", is_active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def article(db_session) -> Article:
    art = Article(title="Test Article", topic="testing")
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)
    db_session.add(
        Paragraph(article_id=art.id, order_index=1, original_text="Body text.")
    )
    db_session.commit()
    return art


def auth_headers(email: str) -> dict:
    """The header pair the BFF sends. Identity is never a query parameter."""
    return {"x-user-email": email, "x-internal-token": TEST_INTERNAL_TOKEN}
