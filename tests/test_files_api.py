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


def _neoforge_instance(tmp_path):
    write_override(
        {"GAME_FAMILY": "minecraft", "GAME_EDITION": "java", "GAME_SOFTWARE": "neoforge", "GAME_VERSION": "21.1.248"},
        path=tmp_path / "install" / "instance_override.json",
    )


def test_list_files_marks_mod_config_files_as_editable(dashboard_client, tmp_path):
    _neoforge_instance(tmp_path)
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "somemod-common.toml").write_text("value = 1\n")
    (mods_dir / "somemod.jar").write_bytes(b"fake jar")

    login(dashboard_client)
    entries = {e["name"]: e["editable"] for e in dashboard_client.get("/api/files/mods").get_json()["entries"]}
    assert entries["somemod-common.toml"] is True
    assert entries["somemod.jar"] is False


def test_get_file_content_returns_text_for_editable_file(dashboard_client, tmp_path):
    _neoforge_instance(tmp_path)
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "cfg.toml").write_text("value = 42\n")

    login(dashboard_client)
    response = dashboard_client.get("/api/files/mods/content?path=cfg.toml")
    assert response.status_code == 200
    assert response.get_json() == {"category": "mods", "path": "cfg.toml", "content": "value = 42\n"}


def test_get_file_content_rejects_files_outside_the_allowlist(dashboard_client, tmp_path):
    _neoforge_instance(tmp_path)
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "server.properties").write_text("motd=hi\n")

    login(dashboard_client)
    response = dashboard_client.get("/api/files/game/content?path=server.properties")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_post_file_content_saves_and_records_activity(dashboard_client, tmp_path):
    # "plugins" isn't a registered category for neoforge (only Bukkit-family
    # software gets it - see runtime/adapters/minecraft_java.py's
    # file_categories()); "mods" is neoforge's equivalent, already exercised
    # by the rest of this file, so reused here too.
    _neoforge_instance(tmp_path)
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    target = mods_dir / "cfg.yml"
    target.write_text("old: true\n")

    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/files/mods/content",
        json={"path": "cfg.yml", "content": "old: false\n"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert target.read_text() == "old: false\n"

    activity = dashboard_client.get("/api/activity").get_json()["entries"]
    assert any(e["action"] == "file_edit" for e in activity)


def test_post_file_content_rejects_files_outside_the_allowlist(dashboard_client, tmp_path):
    _neoforge_instance(tmp_path)
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "run.sh").write_text("#!/bin/sh\n")

    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/files/game/content",
        json={"path": "run.sh", "content": "malicious"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 400
    assert (game_dir / "run.sh").read_text() == "#!/bin/sh\n"
