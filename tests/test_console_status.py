"""dashboard/app/blueprints/console.py's /api/status distinguishes the
CONTAINER's running state (Docker) from the actual game child PROCESS's
(the control agent's own /health) - the Console tab needs the latter
specifically to explain an otherwise silently empty log stream and every
command failing with 502 when the container is healthy but nothing is
actually installed/launched yet (see runtime/adapters/minecraft_java.py's
launch_command() manifest-path fix this status field was added to surface)."""

from __future__ import annotations

from flask import current_app

from tests.conftest import login


class _FakeDockerClient:
    def __init__(self, running: bool):
        self.running = running

    def status(self):
        return {"status": "running" if self.running else "exited", "running": self.running, "started_at": "2026-01-01T00:00:00Z"}


class _FakeGameControl:
    def __init__(self, running: bool):
        self._running = running

    def health(self):
        return {"status": "ok", "running": self._running}

    def send_command(self, command: str):
        return {"ok": False, "error": "El proceso del juego no esta en ejecucion"}


def test_status_distinguishes_container_up_from_game_process_down(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = _FakeDockerClient(running=True)
        current_app.config["GAME_CONTROL"] = _FakeGameControl(running=False)

    login(dashboard_client)
    response = dashboard_client.get("/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["running"] is True
    assert data["game_process_running"] is False


def test_status_reports_game_process_running_true_when_actually_up(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = _FakeDockerClient(running=True)
        current_app.config["GAME_CONTROL"] = _FakeGameControl(running=True)

    login(dashboard_client)
    data = dashboard_client.get("/api/status").get_json()
    assert data["game_process_running"] is True


def test_status_game_process_running_is_null_when_container_itself_is_stopped(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = _FakeDockerClient(running=False)

    login(dashboard_client)
    data = dashboard_client.get("/api/status").get_json()
    assert data["running"] is False
    assert data["game_process_running"] is None
