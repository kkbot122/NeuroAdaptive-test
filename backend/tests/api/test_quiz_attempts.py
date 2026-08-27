"""
API tests for /api/v1/assessment/quiz-attempts.

Regression coverage for K-10 (quiz half). The quiz page made zero network
calls: it read the quiz from sessionStorage, scored it in the browser, and
wrote the result back to sessionStorage, so every result was discarded when
the tab closed.
"""
from app.modules.assessment.models import QuizAttempt
from tests.conftest import auth_headers

ENDPOINT = "/api/v1/assessment/quiz-attempts"


def quiz(answers):
    return {
        "title": "Test Quiz",
        "topic": "testing",
        "questions": [
            {"question": "Q1?", "options": ["a", "b"], "correct_answer": "a"},
            {"question": "Q2?", "options": ["c", "d"], "correct_answer": "d"},
        ],
        "answers": answers,
    }


class TestAuthorization:
    def test_anonymous_is_rejected(self, client):
        assert client.post(ENDPOINT, json=quiz(["a", "d"])).status_code == 422

    def test_wrong_token_is_forbidden(self, client, owner):
        response = client.post(
            ENDPOINT,
            json=quiz(["a", "d"]),
            headers={"x-user-email": owner.email, "x-internal-token": "wrong"},
        )
        assert response.status_code == 403

    def test_attempt_is_attributed_to_the_caller(self, client, owner, other_user, db_session):
        client.post(ENDPOINT, json=quiz(["a", "d"]), headers=auth_headers(owner.email))
        attempt = db_session.query(QuizAttempt).one()
        assert attempt.user_id == owner.id

    def test_listing_shows_only_your_own_attempts(self, client, owner, other_user):
        client.post(ENDPOINT, json=quiz(["a", "d"]), headers=auth_headers(owner.email))

        mine = client.get(ENDPOINT, headers=auth_headers(owner.email)).json()
        theirs = client.get(ENDPOINT, headers=auth_headers(other_user.email)).json()

        assert len(mine) == 1
        assert theirs == []


class TestServerSideGrading:
    def test_all_correct(self, client, owner):
        response = client.post(ENDPOINT, json=quiz(["a", "d"]), headers=auth_headers(owner.email))
        assert response.status_code == 201
        body = response.json()
        assert body["score"] == 2
        assert body["total_questions"] == 2
        assert body["correct"] == [True, True]

    def test_partially_correct(self, client, owner):
        body = client.post(ENDPOINT, json=quiz(["a", "c"]), headers=auth_headers(owner.email)).json()
        assert body["score"] == 1
        assert body["correct"] == [True, False]

    def test_client_cannot_report_its_own_score(self, client, owner, db_session):
        """
        The whole point of persisting server-side. A `score` field in the body
        is ignored — the schema does not declare one and grading is derived.
        """
        payload = quiz(["c", "c"])
        payload["score"] = 999

        body = client.post(ENDPOINT, json=payload, headers=auth_headers(owner.email)).json()
        assert body["score"] == 0
        assert db_session.query(QuizAttempt).one().score == 0

    def test_unanswered_question_counts_as_wrong(self, client, owner):
        body = client.post(ENDPOINT, json=quiz(["a", None]), headers=auth_headers(owner.email)).json()
        assert body["score"] == 1
        assert body["correct"] == [True, False]

    def test_short_answer_list_counts_missing_as_wrong(self, client, owner):
        body = client.post(ENDPOINT, json=quiz(["a"]), headers=auth_headers(owner.email)).json()
        assert body["score"] == 1
        assert body["total_questions"] == 2

    def test_more_answers_than_questions_is_rejected(self, client, owner):
        response = client.post(
            ENDPOINT, json=quiz(["a", "d", "extra"]), headers=auth_headers(owner.email)
        )
        assert response.status_code == 400


class TestPersistence:
    def test_stores_the_quiz_as_presented(self, client, owner, db_session):
        client.post(ENDPOINT, json=quiz(["a", "d"]), headers=auth_headers(owner.email))
        attempt = db_session.query(QuizAttempt).one()
        assert len(attempt.questions) == 2
        assert attempt.answers == ["a", "d"]
        assert attempt.topic == "testing"

    def test_assigns_a_uuid_primary_key(self, client, owner, db_session):
        client.post(ENDPOINT, json=quiz(["a", "d"]), headers=auth_headers(owner.email))
        assert len(str(db_session.query(QuizAttempt).one().id)) == 36

    def test_rejects_an_empty_quiz(self, client, owner):
        payload = {"title": "x", "questions": [], "answers": []}
        assert client.post(ENDPOINT, json=payload, headers=auth_headers(owner.email)).status_code == 422
