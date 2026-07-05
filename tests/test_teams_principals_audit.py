"""Team membership, principal administration, and audit-trail visibility —
the endpoints that let a caller see and control who has access to what."""


def _create_team(client, admin, name="acme"):
    resp = client.post("/v1/teams", json={"name": name}, headers=admin["headers"])
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_principal(client, admin, name):
    resp = client.post("/v1/principals", json={"name": name}, headers=admin["headers"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], {"Authorization": f"Bearer {body['api_key']}"}


def test_creator_is_enrolled_as_owner(client, admin):
    team_id = _create_team(client, admin)
    members = client.get(f"/v1/teams/{team_id}/members", headers=admin["headers"]).json()
    assert any(m["role"] == "owner" for m in members)


def test_only_platform_admin_creates_principals(client, admin):
    team_id = _create_team(client, admin)
    member_id, member_headers = _create_principal(client, admin, "dana")
    client.post(f"/v1/teams/{team_id}/members", json={"principal_id": member_id, "role": "member"},
                headers=admin["headers"])

    # A regular member cannot mint new API identities.
    resp = client.post("/v1/principals", json={"name": "sam"}, headers=member_headers)
    assert resp.status_code == 403


def test_list_teams_is_scoped_to_membership(client, admin):
    team_a = _create_team(client, admin, "team-a")
    _create_team(client, admin, "team-b")
    dana_id, dana_headers = _create_principal(client, admin, "dana")

    # Add dana (readonly) to team-a only — team-b must stay invisible to her.
    add = client.post(f"/v1/teams/{team_a}/members", json={"principal_id": dana_id, "role": "readonly"},
                       headers=admin["headers"])
    assert add.status_code == 201, add.text

    seen = client.get("/v1/teams", headers=dana_headers).json()
    assert [t["id"] for t in seen] == [team_a]


def test_readonly_member_cannot_add_members(client, admin):
    team_id = _create_team(client, admin)
    dana_id, dana_headers = _create_principal(client, admin, "dana")
    client.post(f"/v1/teams/{team_id}/members", json={"principal_id": dana_id, "role": "readonly"},
                headers=admin["headers"])

    other_id, _ = _create_principal(client, admin, "sam")
    resp = client.post(f"/v1/teams/{team_id}/members", json={"principal_id": other_id},
                        headers=dana_headers)
    assert resp.status_code == 403


def test_me_reports_memberships_and_role(client, admin):
    team_id = _create_team(client, admin)
    dana_id, dana_headers = _create_principal(client, admin, "dana")
    client.post(f"/v1/teams/{team_id}/members", json={"principal_id": dana_id, "role": "admin"},
                headers=admin["headers"])

    me = client.get("/v1/me", headers=dana_headers).json()
    assert me["id"] == dana_id
    assert me["teams"] == [{"team_id": team_id, "team_name": "acme", "role": "admin"}]


def test_remove_member(client, admin):
    team_id = _create_team(client, admin)
    dana_id, dana_headers = _create_principal(client, admin, "dana")
    client.post(f"/v1/teams/{team_id}/members", json={"principal_id": dana_id}, headers=admin["headers"])
    assert client.get("/v1/teams", headers=dana_headers).json() != []

    resp = client.delete(f"/v1/teams/{team_id}/members/{dana_id}", headers=admin["headers"])
    assert resp.status_code == 204
    assert client.get("/v1/teams", headers=dana_headers).json() == []


def test_audit_log_records_team_creation_and_is_scoped(client, admin):
    team_a = _create_team(client, admin, "team-a")
    _create_team(client, admin, "team-b")

    all_entries = client.get("/v1/audit", headers=admin["headers"]).json()
    actions = {e["action"] for e in all_entries}
    assert "team.create" in actions

    scoped = client.get("/v1/audit", params={"team_id": team_a}, headers=admin["headers"]).json()
    assert all(e["team_id"] == team_a for e in scoped)


def test_audit_log_forbidden_for_non_member_team_filter(client, admin):
    team_id = _create_team(client, admin)
    _, dana_headers = _create_principal(client, admin, "dana")  # not a member of team_id
    resp = client.get("/v1/audit", params={"team_id": team_id}, headers=dana_headers)
    assert resp.status_code == 403
