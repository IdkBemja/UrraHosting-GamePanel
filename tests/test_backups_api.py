"""Tests for the Backups tab's API (dashboard/app/blueprints/backups.py):
async backup creation + progress polling, delete, and the retention/auto-
backup settings added alongside the fix for the "PermissionError on one
file crashes the whole backup" production incident (see
tests/test_backup.py for the service-level coverage of that fix)."""

from __future__ import annotations

import time

import pytest
from flask import current_app

from app.services.game_control import GameControlClient, GameControlError
from tests.conftest import login


@pytest.fixture(autouse=True)
def _no_real_game_control_calls(monkeypatch):
    """create_backup() best-effort sends save-off/save-on to the game
    control agent (see MinecraftJavaAdapter.backup_pause_commands()) - the
    dashboard_client fixture's GAME_CONTROL points at an unreachable
    "game-runtime" host with no DNS/network in this test environment, which
    would otherwise make every create_backup call here spend real time on
    connection/DNS failures. Simulates "server not reachable", which is
    exactly the case create_backup must already tolerate."""

    def _unreachable(self, command):
        raise GameControlError("server not reachable in tests")

    monkeypatch.setattr(GameControlClient, "send_command", _unreachable)


def _seed_game_dir(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "server.properties").write_text("motd=hi\n")


def _wait_for_backup_done(client, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get("/api/backups/progress").get_json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("backup never finished")


def test_create_backup_is_async_and_reports_progress(dashboard_client, tmp_path):
    _seed_game_dir(tmp_path)
    token = login(dashboard_client)

    response = dashboard_client.post("/api/backups/create", headers={"X-CSRFToken": token})
    assert response.status_code == 202
    assert response.get_json()["started"] is True

    progress = _wait_for_backup_done(dashboard_client)
    assert progress["status"] == "done"
    assert progress["percent"] == 100

    listed = dashboard_client.get("/api/backups").get_json()["backups"]
    assert len(listed) == 1
    assert listed[0]["skipped_files"] == []


def test_create_backup_pauses_and_resumes_autosave_for_minecraft_java(dashboard_client, tmp_path, monkeypatch):
    """MinecraftJavaAdapter.backup_pause_commands()/backup_resume_commands()
    (runtime/adapters/minecraft_java.py) exist precisely to avoid the
    PermissionError-on-a-mid-autosave-world-file production incident this
    whole feature fixes - verifies the route actually sends them, in order,
    around the backup rather than just tolerating their absence."""
    _seed_game_dir(tmp_path)
    token = login(dashboard_client)

    calls = []

    def _record(self, command):
        calls.append(command)
        return {"output": "ok"}

    monkeypatch.setattr(GameControlClient, "send_command", _record)

    response = dashboard_client.post("/api/backups/create", headers={"X-CSRFToken": token})
    assert response.status_code == 202
    # Pause commands are sent synchronously, before the request even returns.
    assert calls == ["save-all flush", "save-off"]

    _wait_for_backup_done(dashboard_client)
    # Resume is sent once the background archive copy actually finishes.
    assert calls == ["save-all flush", "save-off", "save-on"]


def test_create_backup_rejects_concurrent_request(dashboard_client, tmp_path):
    _seed_game_dir(tmp_path)
    token = login(dashboard_client)
    with dashboard_client.application.app_context():
        current_app.config["BACKUPS"]._busy_lock.acquire()
    try:
        response = dashboard_client.post("/api/backups/create", headers={"X-CSRFToken": token})
        assert response.status_code == 409
    finally:
        with dashboard_client.application.app_context():
            current_app.config["BACKUPS"]._busy_lock.release()


def test_create_backup_requires_admin(dashboard_client, tmp_path):
    _seed_game_dir(tmp_path)
    with dashboard_client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    token = login(dashboard_client, "operator1", "another-strong-pw")
    response = dashboard_client.post("/api/backups/create", headers={"X-CSRFToken": token})
    assert response.status_code == 403


def test_delete_backup(dashboard_client, tmp_path):
    _seed_game_dir(tmp_path)
    token = login(dashboard_client)
    dashboard_client.post("/api/backups/create", headers={"X-CSRFToken": token})
    _wait_for_backup_done(dashboard_client)
    backup_id = dashboard_client.get("/api/backups").get_json()["backups"][0]["id"]

    response = dashboard_client.delete(f"/api/backups/{backup_id}", headers={"X-CSRFToken": token})
    assert response.status_code == 200
    assert dashboard_client.get("/api/backups").get_json()["backups"] == []


def test_delete_unknown_backup_returns_404(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.delete("/api/backups/does-not-exist", headers={"X-CSRFToken": token})
    assert response.status_code == 404


def test_delete_backup_requires_admin(dashboard_client, tmp_path):
    _seed_game_dir(tmp_path)
    with dashboard_client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    token = login(dashboard_client, "operator1", "another-strong-pw")
    response = dashboard_client.delete("/api/backups/anything", headers={"X-CSRFToken": token})
    assert response.status_code == 403


def test_get_and_update_backup_settings(dashboard_client):
    token = login(dashboard_client)

    initial = dashboard_client.get("/api/backups/settings").get_json()["settings"]
    assert initial["auto_enabled"] is False

    response = dashboard_client.post(
        "/api/backups/settings",
        json={"retention": 5, "auto_enabled": True, "auto_interval_hours": 6},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert response.get_json()["settings"] == {"retention": 5, "auto_enabled": True, "auto_interval_hours": 6}

    follow_up = dashboard_client.get("/api/backups/settings").get_json()["settings"]
    assert follow_up == {"retention": 5, "auto_enabled": True, "auto_interval_hours": 6}


def test_update_backup_settings_rejects_invalid_retention(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/backups/settings", json={"retention": 0}, headers={"X-CSRFToken": token}
    )
    assert response.status_code == 400


def test_update_backup_settings_requires_admin(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    token = login(dashboard_client, "operator1", "another-strong-pw")
    response = dashboard_client.post(
        "/api/backups/settings", json={"retention": 5}, headers={"X-CSRFToken": token}
    )
    assert response.status_code == 403


def test_backups_progress_requires_login(dashboard_client):
    response = dashboard_client.get("/api/backups/progress")
    assert response.status_code in (302, 401)
