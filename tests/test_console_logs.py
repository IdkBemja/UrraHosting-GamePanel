"""The Console tab's SSE log stream (`GET /api/logs/stream`) must only ever
show what the game server itself printed (plus whatever the RCON client
renders client-side from a command response, which never goes through this
endpoint) - never entrypoint.sh's or game_control_agent.py's own operational
bookkeeping lines, since game-runtime's stdout is shared by all three
writers (see dashboard/app/blueprints/console.py's `_INTERNAL_LOG_PREFIXES`
comment for why).
"""

from __future__ import annotations

from flask import current_app

from tests.conftest import login


class _FakeDockerClient:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def stream_logs(self, tail: int = 50):
        yield from self._lines


def _stream_lines(dashboard_client, monkeypatch, raw_lines: list[str]) -> list[str]:
    fake_docker = _FakeDockerClient([line.encode("utf-8") for line in raw_lines])
    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = fake_docker

    login(dashboard_client)
    response = dashboard_client.get("/api/logs/stream")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    return [chunk.split("data: ", 1)[1] for chunk in body.split("\n\n") if chunk.startswith("data: ")]


def test_entrypoint_and_agent_lines_filtered_out(dashboard_client, monkeypatch):
    lines = _stream_lines(
        dashboard_client,
        monkeypatch,
        [
            "[entrypoint] Validando configuracion de la instancia...\n",
            "[game-control-agent] Iniciando proceso del juego (adapter=minecraft-java/vanilla): ...\n",
            "[game-control-agent] http: 127.0.0.1 - - [GET /health]\n",
            '[09:22:41] [Server thread/INFO]: Done (12.345s)! For help, type "help"\n',
        ],
    )
    assert lines == ['[09:22:41] [Server thread/INFO]: Done (12.345s)! For help, type "help"']


def test_server_output_line_passes_through_unchanged(dashboard_client, monkeypatch):
    lines = _stream_lines(
        dashboard_client,
        monkeypatch,
        ["[09:23:01] [Server thread/INFO]: <Steve> hello world\n"],
    )
    assert lines == ["[09:23:01] [Server thread/INFO]: <Steve> hello world"]


def test_blank_lines_dropped(dashboard_client, monkeypatch):
    lines = _stream_lines(dashboard_client, monkeypatch, ["\n", "   \n", "real output line\n"])
    assert lines == ["real output line"]
