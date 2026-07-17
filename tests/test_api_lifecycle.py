"""End-to-end through the HTTP API, converging state by draining the worker with a
fake provisioner (ADR-003). Covers auth, the create→ready→backup→rotate→delete
happy path, idempotent create (DR-6), and quota rejection (BR-2)."""

from app.lifecycle.worker import drain


def _create_team(client, admin, **overrides):
    body = {"name": "team", "max_instances": 5, "max_total_memory_mb": 8192, "max_storage_gb": 100}
    body.update(overrides)
    resp = client.post("/v1/teams", json=body, headers=admin["headers"])
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_requires_auth(client):
    assert client.get("/v1/instances").status_code == 401


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_full_lifecycle(client, admin, fake_prov):
    team_id = _create_team(client, admin)

    created = client.post(
        "/v1/instances", json={"team_id": team_id, "name": "db1", "size": "small"},
        headers=admin["headers"],
    )
    assert created.status_code == 202, created.text
    body = created.json()
    instance_id = body["id"]
    assert body["password"]                       # secret revealed exactly once
    assert body["observed_state"] == "pending"    # not provisioned synchronously
    assert body["connection"]["port"] >= 15000

    drain(fake_prov)  # run the queued PROVISION job

    status = client.get(f"/v1/instances/{instance_id}/status", headers=admin["headers"]).json()
    assert status["observed_state"] == "ready"

    # Connection info must survive past the one-time create response — only the
    # password is one-time-reveal (previously GET responses omitted it entirely).
    fetched = client.get(f"/v1/instances/{instance_id}", headers=admin["headers"]).json()
    assert fetched["connection"] == body["connection"]
    assert "password" not in fetched
    listed = client.get("/v1/instances", headers=admin["headers"]).json()
    assert listed[0]["connection"]["port"] == body["connection"]["port"]

    backup = client.post(f"/v1/instances/{instance_id}/backups", json={}, headers=admin["headers"])
    assert backup.status_code == 201, backup.text
    assert backup.json()["status"] == "ok"

    rotated = client.post(f"/v1/instances/{instance_id}/credentials/rotate", headers=admin["headers"])
    assert rotated.status_code == 200
    assert rotated.json()["password"]

    assert client.delete(f"/v1/instances/{instance_id}", headers=admin["headers"]).status_code == 202
    drain(fake_prov)  # run the queued DELETE job
    assert client.get(f"/v1/instances/{instance_id}", headers=admin["headers"]).status_code == 404


def test_idempotent_create(client, admin, fake_prov):
    team_id = _create_team(client, admin)
    headers = {**admin["headers"], "Idempotency-Key": "same-key"}
    first = client.post("/v1/instances", json={"team_id": team_id, "name": "d", "size": "small"}, headers=headers)
    second = client.post("/v1/instances", json={"team_id": team_id, "name": "d", "size": "small"}, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["password"] == ""  # not re-revealed on replay


def test_quota_rejected(client, admin, fake_prov):
    team_id = _create_team(client, admin, max_instances=1)
    ok = client.post("/v1/instances", json={"team_id": team_id, "name": "a", "size": "small"}, headers=admin["headers"])
    assert ok.status_code == 202
    over = client.post("/v1/instances", json={"team_id": team_id, "name": "b", "size": "small"}, headers=admin["headers"])
    assert over.status_code == 409
