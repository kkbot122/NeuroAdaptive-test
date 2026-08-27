"""
API tests for GET /api/v1/content/articles/{id}.

Regression coverage for K-9. The endpoint was declared

    async def get_article(article_id: int, user_id: int = 1, ...)

with no auth dependency, so an anonymous caller could write ArticleReading
rows for any user and read any user's archetype, and every real read was
attributed to user 1.

CONTRIBUTING.md §4 requires every endpoint test to include a negative
authorization case.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.content.models import ArticleReading
from tests.conftest import auth_headers

ENDPOINT = "/api/v1/content/articles/{}"


@pytest.fixture(autouse=True)
def _no_network():
    """The handler calls an LLM per paragraph. Never do that in a test."""
    with patch(
        "app.services.adaptation.adaptation_service.adapt_content",
        new=AsyncMock(return_value="adapted text"),
    ) as mock:
        yield mock


class TestAuthenticationRequired:
    def test_anonymous_request_is_rejected(self, client, article):
        """Previously this returned 200 and logged a read against user 1."""
        response = client.get(ENDPOINT.format(article.id))
        assert response.status_code == 422

    def test_missing_internal_token_is_rejected(self, client, article):
        response = client.get(
            ENDPOINT.format(article.id), headers={"x-user-email": "owner@example.com"}
        )
        assert response.status_code == 422

    def test_wrong_internal_token_is_forbidden(self, client, article, owner):
        response = client.get(
            ENDPOINT.format(article.id),
            headers={"x-user-email": owner.email, "x-internal-token": "wrong"},
        )
        assert response.status_code == 403

    def test_unknown_user_is_not_found(self, client, article):
        response = client.get(
            ENDPOINT.format(article.id), headers=auth_headers("ghost@example.com")
        )
        assert response.status_code == 404


class TestIdentityIsNotClientSupplied:
    def test_user_id_query_param_is_ignored(self, client, article, owner, other_user, db_session):
        """
        The core of K-9: supplying user_id must not change who the read is
        attributed to. FastAPI ignores the unknown parameter; the reading is
        recorded against the authenticated caller.
        """
        response = client.get(
            f"{ENDPOINT.format(article.id)}?user_id={other_user.id}",
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 200

        readings = db_session.query(ArticleReading).all()
        assert len(readings) == 1
        assert readings[0].user_id == owner.id
        assert readings[0].user_id != other_user.id

    def test_reading_is_attributed_to_the_authenticated_user(
        self, client, article, owner, db_session
    ):
        client.get(ENDPOINT.format(article.id), headers=auth_headers(owner.email))
        reading = db_session.query(ArticleReading).one()
        assert reading.user_id == owner.id


class TestSuccessfulRead:
    def test_returns_the_article_with_adapted_paragraphs(self, client, article, owner):
        response = client.get(ENDPOINT.format(article.id), headers=auth_headers(owner.email))
        assert response.status_code == 200

        body = response.json()
        assert body["id"] == article.id
        assert body["title"] == "Test Article"
        assert len(body["paragraphs"]) == 1
        assert body["paragraphs"][0]["original_text"] == "Body text."
        assert body["paragraphs"][0]["adapted_text"] == "adapted text"

    def test_adaptation_receives_a_mapping_not_a_label(self, client, article, owner, _no_network):
        """
        Second half of K-9: a str was passed where a dict was required, so the
        endpoint raised AttributeError on any article with a paragraph.
        """
        client.get(ENDPOINT.format(article.id), headers=auth_headers(owner.email))
        _no_network.assert_awaited()
        _, scores = _no_network.await_args.args
        assert isinstance(scores, dict)
        assert set(scores) == {"visual", "structural", "active", "logic"}

    def test_missing_article_is_not_found(self, client, owner):
        response = client.get(ENDPOINT.format(999999), headers=auth_headers(owner.email))
        assert response.status_code == 404
