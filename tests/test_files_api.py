"""Blueprint-level test for the Files tab's "ir a la categoria real"
shortcuts (dashboard/app/blueprints/files.py + InstanceStorage.
shadowed_categories() in storage.py) - the unit tests in test_storage.py
cover the underlying logic in detail; this just confirms the HTTP route
actually wires it through end to end."""

from __future__ import annotations

from config.instance_state import write_override

from tests.conftest import login


def test_list_files_game_root_reports_mods_as_a_shortcut(dashboard_client, tmp_path):
    override_path = tmp_path / "install" / "instance_override.json"
    write_override(
        {"GAME_FAMILY": "minecraft", "GAME_EDITION": "java", "GAME_SOFTWARE": "neoforge", "GAME_VERSION": "21.1.248"},
        path=override_path,
    )

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "server.properties").write_text("motd=hi\n")
    stray_mods = game_dir / "mods"
    stray_mods.mkdir()

    login(dashboard_client)
    response = dashboard_client.get("/api/files/game")
    assert response.status_code == 200
    data = response.get_json()

    assert "mods" not in {entry["name"] for entry in data["entries"]}
    assert data["shortcuts"] == ["mods"]


def test_list_files_mods_category_itself_reports_no_shortcuts(dashboard_client, tmp_path):
    override_path = tmp_path / "install" / "instance_override.json"
    write_override(
        {"GAME_FAMILY": "minecraft", "GAME_EDITION": "java", "GAME_SOFTWARE": "neoforge", "GAME_VERSION": "21.1.248"},
        path=override_path,
    )
    (tmp_path / "mods").mkdir()

    login(dashboard_client)
    response = dashboard_client.get("/api/files/mods")
    assert response.status_code == 200
    assert response.get_json()["shortcuts"] == []
