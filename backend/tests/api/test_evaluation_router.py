from tests.conftest import auth_headers


class TestEvaluationRouterHappyPath:
    def test_create_experiment_assign_and_check_condition(self, client, owner, other_user):
        create_resp = client.post(
            "/api/v1/evaluation/experiments",
            json={
                "name": "Pilot v1",
                "conditions": [
                    {"code": "B1", "description": "Fixed-sequence", "config": {"adaptive": False}},
                    {"code": "B2", "description": "Real system", "config": {}},
                ],
            },
            headers=auth_headers(owner.email),
        )
        assert create_resp.status_code == 201
        experiment_id = create_resp.json()["id"]

        conditions_resp = client.get(
            f"/api/v1/evaluation/experiments/{experiment_id}/conditions", headers=auth_headers(owner.email)
        )
        assert {c["code"] for c in conditions_resp.json()} == {"B1", "B2"}

        assign_resp = client.post(
            f"/api/v1/evaluation/experiments/{experiment_id}/assign",
            json={"learner_email": other_user.email, "condition_code": "B2"},
            headers=auth_headers(owner.email),
        )
        assert assign_resp.status_code == 200

        mine_resp = client.get(
            f"/api/v1/evaluation/experiments/{experiment_id}/my-condition",
            headers=auth_headers(other_user.email),
        )
        assert mine_resp.json() == {"assigned": True, "code": "B2", "config": {}}

    def test_unassigned_learner_gets_assigned_false(self, client, owner, other_user):
        create_resp = client.post(
            "/api/v1/evaluation/experiments",
            json={"name": "Pilot v2", "conditions": [{"code": "B2", "description": "Real system", "config": {}}]},
            headers=auth_headers(owner.email),
        )
        experiment_id = create_resp.json()["id"]
        resp = client.get(
            f"/api/v1/evaluation/experiments/{experiment_id}/my-condition",
            headers=auth_headers(other_user.email),
        )
        assert resp.json() == {"assigned": False}

    def test_double_assignment_is_conflict(self, client, owner, other_user):
        create_resp = client.post(
            "/api/v1/evaluation/experiments",
            json={
                "name": "Pilot v3",
                "conditions": [
                    {"code": "B1", "description": "x", "config": {}},
                    {"code": "B2", "description": "y", "config": {}},
                ],
            },
            headers=auth_headers(owner.email),
        )
        experiment_id = create_resp.json()["id"]
        client.post(
            f"/api/v1/evaluation/experiments/{experiment_id}/assign",
            json={"learner_email": other_user.email, "condition_code": "B1"},
            headers=auth_headers(owner.email),
        )
        second = client.post(
            f"/api/v1/evaluation/experiments/{experiment_id}/assign",
            json={"learner_email": other_user.email, "condition_code": "B2"},
            headers=auth_headers(owner.email),
        )
        assert second.status_code == 409
