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
    Default: no concepts found in any section. This lets a test that only
    cares about ingestion (not curriculum content) run the full pipeline to
    a real READY without asserting anything about generated concepts -- an
    empty concept set passes validation trivially (no concepts, no lessons
    required, no cycle possible, no blueprint required). A test that DOES
    care about curriculum content overrides this fixture's responses.
    """
    from app.services.generation.fake import FakeGenerationGateway

    return FakeGenerationGateway().set_default('{"concepts": [], "edges": []}')


@pytest.fixture()
def client(db_session, fake_embeddings, fake_vectors, fake_generation):
    """
    TestClient with the database dependency pointed at the test session, and
    the job/retrieval service factories overridden to use fake, offline
    embedding and vector-store providers.

    No test in this suite may depend on a reachable Gemini API or Qdrant
    instance: doing so makes the suite slow, flaky, and dependent on a live
    API key, and settings.QDRANT_URL's default (the Compose service name)
    does not resolve outside the container network at all. A test that wants
    to exercise the real Gemini/Qdrant adapters does so as a narrow,
    explicitly-marked integration test, not through this fixture.
    """
    from app.modules.jobs.router import _service as job_service_dep
    from app.modules.jobs.service import JobService
    from app.modules.retrieval.router import _service as retrieval_service_dep
    from app.modules.retrieval.service import RetrievalService

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[job_service_dep] = lambda: JobService(
        db_session, embeddings=fake_embeddings, vectors=fake_vectors, generation=fake_generation
    )
    app.dependency_overrides[retrieval_service_dep] = lambda: RetrievalService(
        db_session, fake_embeddings, fake_vectors
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
