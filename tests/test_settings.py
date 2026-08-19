"""Tests for the Configuracion tab's settings API (dashboard/app/blueprints/
settings.py) - a curated, safe subset of server.properties/serverconfig.txt
(MOTD, max players, difficulty, gamemode, online-mode) editable without a
redeploy, validated with the exact same rules the game-runtime container
itself enforces at boot."""

from __future__ import annotations

import json

from config.instance_state import write_override
from flask import current_app

from tests.conftest import login


def test_get_settings_returns_full_schema_for_java(dashboard_client):
    """dashboard_client's default instance is minecraft/java - every safe
    key applies there, including ONLINE_MODE (Java-only, see
    runtime/adapters/minecraft_java.py)."""
    login(dashboard_client)
    response = dashboard_client.get("/api/settings")
    assert response.status_code == 200
    data = response.get_json()
    assert data["game_family"] == "minecraft"
    assert data["game_edition"] == "java"
    keys = {field["key"] for field in data["settings"]}
    assert keys == {"MOTD", "MAX_PLAYERS", "DIFFICULTY", "GAMEMODE", "ONLINE_MODE"}
    difficulty = next(f for f in data["settings"] if f["key"] == "DIFFICULTY")
    assert difficulty["options"] == ["peaceful", "easy", "normal", "hard"]
    assert difficulty["value"] == "easy"  # adapter default, nothing overridden yet
    online_mode = next(f for f in data["settings"] if f["key"] == "ONLINE_MODE")
    assert online_mode["value"] is True


def test_get_settings_requires_login(dashboard_client):
    response = dashboard_client.get("/api/settings")
    assert response.status_code in (302, 401)


def test_get_settings_excludes_online_mode_for_bedrock(dashboard_client, tmp_path):
    """ONLINE_MODE is hardcoded in runtime/adapters/minecraft_bedrock.py's
    prepare() - it must never be offered as an editable field there."""
    override_path = tmp_path / "install" / "instance_override.json"
    write_override(
        {"GAME_FAMILY": "minecraft", "GAME_EDITION": "bedrock", "GAME_SOFTWARE": "bedrock", "GAME_VERSION": "1.21.50.7"},
        path=override_path,
    )
    login(dashboard_client)
    response = dashboard_client.get("/api/settings")
    data = response.get_json()
    keys = {field["key"] for field in data["settings"]}
    assert keys == {"MOTD", "MAX_PLAYERS", "DIFFICULTY", "GAMEMODE"}
    gamemode = next(f for f in data["settings"] if f["key"] == "GAMEMODE")
    assert "spectator" not in gamemode["options"]  # Bedrock has no spectator mode


def test_get_settings_terraria_only_maxplayers_difficulty_and_secure(dashboard_client, tmp_path):
    override_path = tmp_path / "install" / "instance_override.json"
    write_override({"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "vanilla", "GAME_VERSION": "1.4.4.9"}, path=override_path)
    login(dashboard_client)
    response = dashboard_client.get("/api/settings")
    data = response.get_json()
    keys = {field["key"] for field in data["settings"]}
    assert keys == {"MAX_PLAYERS", "DIFFICULTY", "SECURE"}
    difficulty = next(f for f in data["settings"] if f["key"] == "DIFFICULTY")
    assert difficulty["options"] == ["classic", "expert", "master", "journey"]
    secure = next(f for f in data["settings"] if f["key"] == "SECURE")
    assert secure["type"] == "bool"
    assert secure["value"] is True  # default, nothing overridden yet


def test_update_settings_can_disable_terraria_secure(dashboard_client, tmp_path):
    override_path = tmp_path / "install" / "instance_override.json"
    write_override(
        {"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "vanilla", "GAME_VERSION": "1.4.4.9", "DIFFICULTY": "classic"},
        path=override_path,
    )
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"SECURE": False}, headers={"X-CSRFToken": token})
    assert response.status_code == 200

    saved = json.loads(override_path.read_text())
    assert saved["SECURE"] == "false"

    follow_up = dashboard_client.get("/api/settings")
    secure = next(f for f in follow_up.get_json()["settings"] if f["key"] == "SECURE")
    assert secure["value"] is False


def test_update_settings_rejects_secure_for_minecraft(dashboard_client):
    """SECURE only applies to Terraria/tModLoader - the default dashboard_client
    instance is minecraft/java, so it must not be an editable key there."""
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"SECURE": False}, headers={"X-CSRFToken": token})
    assert response.status_code == 400


def test_update_settings_persists_and_is_reflected_on_next_get(dashboard_client, tmp_path):
    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/settings",
        json={"MOTD": "Bienvenido!", "MAX_PLAYERS": 42, "DIFFICULTY": "hard", "GAMEMODE": "creative", "ONLINE_MODE": False},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["restart_required"] is True

    override_path = tmp_path / "install" / "instance_override.json"
    saved = json.loads(override_path.read_text())
    assert saved["MOTD"] == "Bienvenido!"
    assert saved["MAX_PLAYERS"] == "42"
    assert saved["DIFFICULTY"] == "hard"
    assert saved["GAMEMODE"] == "creative"
    assert saved["ONLINE_MODE"] == "false"

    follow_up = dashboard_client.get("/api/settings")
    keys_to_values = {f["key"]: f["value"] for f in follow_up.get_json()["settings"]}
    assert keys_to_values["MOTD"] == "Bienvenido!"
    assert keys_to_values["MAX_PLAYERS"] == 42
    assert keys_to_values["DIFFICULTY"] == "hard"
    assert keys_to_values["ONLINE_MODE"] is False


def test_update_settings_rejects_invalid_difficulty(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"DIFFICULTY": "impossible"}, headers={"X-CSRFToken": token})
    assert response.status_code == 400
    assert "DIFFICULTY" in response.get_json()["error"]


def test_update_settings_rejects_motd_over_max_length(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"MOTD": "x" * 500}, headers={"X-CSRFToken": token})
    assert response.status_code == 400


def test_update_settings_rejects_max_players_out_of_range(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"MAX_PLAYERS": 0}, headers={"X-CSRFToken": token})
    assert response.status_code == 400


def test_update_settings_rejects_key_not_applicable_to_current_adapter(dashboard_client, tmp_path):
    """ONLINE_MODE only applies to Minecraft Java - trying to set it for
    Bedrock must be rejected even though ONLINE_MODE is in the outer
    SETTINGS_KEYS allowlist."""
    override_path = tmp_path / "install" / "instance_override.json"
    write_override(
        {"GAME_FAMILY": "minecraft", "GAME_EDITION": "bedrock", "GAME_SOFTWARE": "bedrock", "GAME_VERSION": "1.21.50.7"},
        path=override_path,
    )
    token = login(dashboard_client)
    response = dashboard_client.post("/api/settings", json={"ONLINE_MODE": False}, headers={"X-CSRFToken": token})
    assert response.status_code == 400


def test_update_settings_rejects_unauthenticated(dashboard_client):
    # No session, no CSRF token: CSRFProtect rejects it (400) before
    # login_required even runs - same as every other POST route here (see
    # test_operator_cannot_install in test_reprovision_flow.py for the
    # logged-in-but-wrong-role case, which does reach 403).
    response = dashboard_client.post("/api/settings", json={"MOTD": "hi"})
    assert response.status_code == 400


def test_operator_cannot_update_settings(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    token = login(dashboard_client, "operator1", "another-strong-pw")
    response = dashboard_client.post("/api/settings", json={"MOTD": "hi"}, headers={"X-CSRFToken": token})
    assert response.status_code == 403


def test_update_settings_records_activity(dashboard_client):
    token = login(dashboard_client)
    dashboard_client.post("/api/settings", json={"MOTD": "hola"}, headers={"X-CSRFToken": token})
    response = dashboard_client.get("/api/activity")
    entries = response.get_json()["entries"]
    assert any(e["action"] == "settings_update" for e in entries)


def test_reprovision_clears_adapter_specific_settings_but_keeps_universal_ones(dashboard_client, monkeypatch, tmp_path):
    """A DIFFICULTY/GAMEMODE saved for the OLD adapter can be meaningless or
    outright invalid for the new one (Terraria's difficulty values aren't
    Minecraft's) - reprovisioning must replace DIFFICULTY with a fresh, valid
    default for the NEW adapter (not just drop it - see
    config/instance_state.py's default_gameplay_settings() for why a dropped
    key isn't safe either) and drop GAMEMODE outright since Terraria has no
    such setting. MOTD/MAX_PLAYERS are universal and should survive."""
    token = login(dashboard_client)
    dashboard_client.post(
        "/api/settings",
        json={"MOTD": "Server viejo", "MAX_PLAYERS": 7, "DIFFICULTY": "hard", "GAMEMODE": "creative"},
        headers={"X-CSRFToken": token},
    )

    from app.services.catalog import DownloadInfo
    from app.services.installer import Installer, InstallResult

    def fake_get_download(game_family, game_edition, game_software, version, channel):
        return DownloadInfo(url="https://terraria.org/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="TerrariaServer.bin.x86_64")

    def fake_install(self, game_family, game_edition, game_software, version, channel, actor, create_backup=True, **kwargs):
        return InstallResult(
            game_software=game_software, game_version=version, channel=channel, url="https://terraria.org/x.zip",
            checksum="abc123", installed_at="2026-01-01T00:00:00Z", actor=actor,
        )

    class _FakeDockerClient:
        def status(self):
            return {"status": "running", "running": True, "started_at": ""}

        def stop(self, timeout=60):
            pass

        def start(self):
            pass

    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = _FakeDockerClient()
        monkeypatch.setattr(current_app.config["CATALOG"], "get_download", fake_get_download)
    monkeypatch.setattr(Installer, "install", fake_install)

    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "terraria", "game_edition": "", "software": "vanilla", "version": "1.4.4.9", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200

    override_path = tmp_path / "install" / "instance_override.json"
    saved = json.loads(override_path.read_text())
    assert saved["DIFFICULTY"] == "classic"
    assert saved["SECURE"] == "true"
    assert "GAMEMODE" not in saved
    assert saved["MOTD"] == "Server viejo"
    assert saved["MAX_PLAYERS"] == "7"
