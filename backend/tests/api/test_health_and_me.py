"""Foundation endpoints: GET /health, GET /health/db, GET /me."""
from tests.conftest import auth_headers


class TestHealth:
    def test_liveness_needs_no_auth(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_db_readiness_reports_reachable(self, client):
        response = client.get("/health/db")
        assert response.status_code == 200
        assert response.json()["database"] == "reachable"


class TestMe:
    def test_returns_the_authenticated_user(self, client, owner):
        response = client.get("/api/v1/me", headers=auth_headers(owner.email))
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == owner.email
        assert body["id"] == owner.id

    def test_anonymous_is_rejected(self, client):
        assert client.get("/api/v1/me").status_code == 422

    def test_wrong_token_is_forbidden(self, client, owner):
        response = client.get(
            "/api/v1/me",
            headers={"x-user-email": owner.email, "x-internal-token": "wrong"},
        )
        assert response.status_code == 403

    def test_identity_follows_the_header_not_a_param(self, client, owner, other_user):
        """?user_id= must not change who /me reports."""
        response = client.get(
            f"/api/v1/me?user_id={other_user.id}", headers=auth_headers(owner.email)
        )
        assert response.json()["email"] == owner.email
