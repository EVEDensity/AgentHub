from app.api.admin.rbac import ALL_SCOPES, ROLES


def test_mission_verify_scope_requires_an_explicit_workspace_grant() -> None:
    assert "mission:verify" in ALL_SCOPES
    assert all("mission:verify" not in role["scopes"] for role in ROLES.values())
