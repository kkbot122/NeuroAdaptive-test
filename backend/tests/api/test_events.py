"""
API tests for POST /api/v1/events/batch.

Regression coverage for K-10 (telemetry half). Three Tracked* components
posted to /api/v1/profile/pulse, which did not exist, from a client component,
with no auth headers, against a hardcoded localhost URL — and logged success
on the resulting 404.
"""
from app.modules.events.models import LearningEvent
from tests.conftest import auth_headers

ENDPOINT = "/api/v1/events/batch"


def one(**overrides):
    event = {"event_type": "paragraph_view", "dimension": "textual", "seconds": 5}
    event.update(overrides)
    return {"events": [event]}


class TestAuthorization:
    def test_anonymous_is_rejected(self, client):
        assert client.post(ENDPOINT, json=one()).status_code == 422

    def test_wrong_token_is_forbidden(self, client, owner):
        response = client.post(
            ENDPOINT,
            json=one(),
            headers={"x-user-email": owner.email, "x-internal-token": "wrong"},
        )
        assert response.status_code == 403

    def test_unknown_user_is_not_found(self, client):
        response = client.post(ENDPOINT, json=one(), headers=auth_headers("ghost@example.com"))
        assert response.status_code == 404

    def test_events_are_attributed_to_the_caller(self, client, owner, other_user, db_session):
        """Identity comes from the header, never from the body."""
        payload = one()
        payload["events"][0]["payload"] = {"user_id": other_user.id}

        assert client.post(ENDPOINT, json=payload, headers=auth_headers(owner.email)).status_code == 202

        event = db_session.query(LearningEvent).one()
        assert event.user_id == owner.id
        assert event.user_id != other_user.id


class TestRecording:
    def test_accepts_and_persists(self, client, owner, db_session):
        response = client.post(ENDPOINT, json=one(), headers=auth_headers(owner.email))
        assert response.status_code == 202
        assert response.json() == {"accepted": 1, "rejected": 0}

        event = db_session.query(LearningEvent).one()
        assert event.event_type == "paragraph_view"
        assert event.dimension == "textual"
        assert event.seconds == 5

    def test_assigns_a_uuid_primary_key(self, client, owner, db_session):
        client.post(ENDPOINT, json=one(), headers=auth_headers(owner.email))
        event = db_session.query(LearningEvent).one()
        assert len(str(event.id)) == 36

    def test_accepts_a_multi_event_batch(self, client, owner, db_session):
        batch = {"events": [
            {"event_type": "paragraph_view", "dimension": "textual", "seconds": 5},
            {"event_type": "image_view", "dimension": "visual", "seconds": 3},
        ]}
        response = client.post(ENDPOINT, json=batch, headers=auth_headers(owner.email))
        assert response.json()["accepted"] == 2
        assert db_session.query(LearningEvent).count() == 2

    def test_unknown_dimension_is_nulled_not_rejected(self, client, owner, db_session):
        """Timing evidence survives a misspelled label."""
        response = client.post(
            ENDPOINT, json=one(dimension="nonsense"), headers=auth_headers(owner.email)
        )
        assert response.status_code == 202
        assert db_session.query(LearningEvent).one().dimension is None


class TestInputBounds:
    def test_rejects_seconds_above_the_cap(self, client, owner):
        """A client must not be able to inflate its own engagement signal."""
        response = client.post(ENDPOINT, json=one(seconds=10_000), headers=auth_headers(owner.email))
        assert response.status_code == 422

    def test_rejects_negative_seconds(self, client, owner):
        response = client.post(ENDPOINT, json=one(seconds=-1), headers=auth_headers(owner.email))
        assert response.status_code == 422

    def test_rejects_an_empty_batch(self, client, owner):
        response = client.post(ENDPOINT, json={"events": []}, headers=auth_headers(owner.email))
        assert response.status_code == 422

    def test_rejects_an_oversized_batch(self, client, owner):
        batch = {"events": [{"event_type": "x", "seconds": 1}] * 101}
        response = client.post(ENDPOINT, json=batch, headers=auth_headers(owner.email))
        assert response.status_code == 422
