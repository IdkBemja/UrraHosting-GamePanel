"""End-to-end test of the Software tab's "change the game" flow (plan.md
follow-up: pick Game -> Edition -> Software -> Version, and installing a
different game/edition than what's currently running reprovisions the
instance via config/instance_state.py's override file - see
dashboard/app/blueprints/catalog.py's /install route). Docker and the
catalog's real network calls are monkeypatched; everything else (override
file, installer, activity log, before_request state refresh) runs for
real against a temp DATA_ROOT.
"""

from __future__ import annotations

import json
import os

from app.services.catalog import DownloadInfo
from app.services.installer import InstallResult
from flask import current_app

from tests.conftest import login


class _FakeDockerClient:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append("status")
        return {"status": "running", "running": True, "started_at": ""}

    def stop(self, timeout=60):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")

    def restart(self, timeout=60):
        self.calls.append("restart")


def _install_terraria(dashboard_client, monkeypatch):
    fake_docker = _FakeDockerClient()

    def fake_get_download(game_family, game_edition, game_software, version, channel):
        return DownloadInfo(
            url="https://terraria.org/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="TerrariaServer.bin.x86_64"
        )

    def fake_install(self, game_family, game_edition, game_software, version, channel, actor, create_backup=True, **kwargs):
        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url="https://terraria.org/x.zip",
            checksum="abc123",
            installed_at="2026-01-01T00:00:00Z",
            actor=actor,
        )

    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = fake_docker
        monkeypatch.setattr(current_app.config["CATALOG"], "get_download", fake_get_download)

    from app.services.installer import Installer

    monkeypatch.setattr(Installer, "install", fake_install)

    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "terraria", "game_edition": "", "software": "vanilla", "version": "1.4.4.9", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    return response, fake_docker


def test_install_different_game_reprovisions_instance(dashboard_client, monkeypatch):
    response, fake_docker = _install_terraria(dashboard_client, monkeypatch)

    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["reprovisioned"] is True
    assert "stop" in fake_docker.calls
    assert "start" in fake_docker.calls


def test_reprovision_writes_override_file(dashboard_client, monkeypatch, tmp_path):
    _install_terraria(dashboard_client, monkeypatch)

    override_path = tmp_path / "install" / "instance_override.json"
    assert override_path.exists()
    data = json.loads(override_path.read_text())
    assert data["GAME_FAMILY"] == "terraria"
    assert data["GAME_EDITION"] == ""
    assert data["GAME_SOFTWARE"] == "vanilla"


def test_reprovision_from_hard_difficulty_leaves_new_adapter_bootable(dashboard_client, monkeypatch, tmp_path):
    """Regression test: a Minecraft instance with DIFFICULTY=hard (valid for
    Minecraft, meaningless for Terraria) reprovisioned to Terraria must not
    leave the effective DIFFICULTY as "hard" - that value only ever lived in
    the base process env (this fixture's DIFFICULTY=hard), never in the
    override file, so simply deleting the override key isn't enough: the
    game-runtime container's boot-time validate_extra() would still see
    "hard" and crash-loop (see config/instance_state.py's
    default_gameplay_settings())."""
    monkeypatch.setenv("DIFFICULTY", "hard")

    _install_terraria(dashboard_client, monkeypatch)

    from config.instance_state import effective_environ
    from runtime.adapters.terraria import TerrariaAdapter

    override_path = tmp_path / "install" / "instance_override.json"
    env = effective_environ(dict(os.environ), path=override_path)
    assert env["DIFFICULTY"] != "hard"
    assert TerrariaAdapter().validate_extra(env) == []


def test_overview_reflects_reprovision_on_next_request(dashboard_client, monkeypatch):
    _install_terraria(dashboard_client, monkeypatch)

    response = dashboard_client.get("/api/overview")
    assert response.status_code == 200
    data = response.get_json()
    assert data["game_family"] == "terraria"
    assert data["game_software"] == "vanilla"
    assert data["protocol"] == "tcp"


def test_install_status_reflects_completion(dashboard_client, monkeypatch):
    """The Software tab's progress modal polls GET /api/catalog/install/status
    while the (blocking) POST /api/catalog/install request runs on another
    gunicorn thread - after a successful install, the tracker must report
    active: False and ok: True so the modal can show a final "completed"
    state instead of spinning forever."""
    response, _ = _install_terraria(dashboard_client, monkeypatch)
    assert response.status_code == 200

    token = login(dashboard_client)
    status_response = dashboard_client.get("/api/catalog/install/status", headers={"X-CSRFToken": token})
    assert status_response.status_code == 200
    data = status_response.get_json()
    assert data["active"] is False
    assert data["ok"] is True


def test_install_status_idle_before_any_install(dashboard_client):
    token = login(dashboard_client)
    response = dashboard_client.get("/api/catalog/install/status", headers={"X-CSRFToken": token})
    assert response.status_code == 200
    data = response.get_json()
    assert data["active"] is False
    assert data["phase"] == "idle"


def test_install_wipes_plugins_belonging_to_old_software(dashboard_client, monkeypatch, tmp_path):
    """Every (re)install is a clean install now (by request): a plugin built
    for the OLD software (here, Paper - the dashboard_client fixture's
    default) is meaningless once different software is installed, so
    /install must wipe the plugins/ category too, not just game/ (which
    Installer itself already snapshots and wipes)."""
    fake_docker = _FakeDockerClient()

    def fake_get_download(game_family, game_edition, game_software, version, channel):
        return DownloadInfo(
            url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="TerrariaServer.bin.x86_64"
        )

    def fake_install(self, game_family, game_edition, game_software, version, channel, actor, create_backup=True, **kwargs):
        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url="https://example.test/x.zip",
            checksum="abc123",
            installed_at="2026-01-01T00:00:00Z",
            actor=actor,
        )

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "essentials.jar").write_bytes(b"old plugin")

    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = fake_docker
        monkeypatch.setattr(current_app.config["CATALOG"], "get_download", fake_get_download)

    from app.services.installer import Installer

    monkeypatch.setattr(Installer, "install", fake_install)

    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "terraria", "game_edition": "", "software": "vanilla", "version": "1.4.4.9", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert not (plugins_dir / "essentials.jar").exists()
    assert plugins_dir.exists()  # the mount point itself is recreated, not left missing


def test_install_unexpected_oserror_reports_cleanly(dashboard_client, monkeypatch):
    """An unexpected OSError (e.g. a permission quirk on some bind-mount
    backend, or the shutil.Error a partially-failed snapshot copy can still
    raise) must never crash the request as a raw 500 - it should come back
    as a normal JSON error, and the progress tracker must not get stuck
    "active" forever (the Software tab's progress modal polls it and would
    otherwise spin indefinitely)."""
    fake_docker = _FakeDockerClient()

    def fake_get_download(game_family, game_edition, game_software, version, channel):
        return DownloadInfo(
            url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="TerrariaServer.bin.x86_64"
        )

    def failing_install(self, game_family, game_edition, game_software, version, channel, actor, create_backup=True, **kwargs):
        raise OSError(13, "Permission denied")

    with dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = fake_docker
        monkeypatch.setattr(current_app.config["CATALOG"], "get_download", fake_get_download)

    from app.services.installer import Installer

    monkeypatch.setattr(Installer, "install", failing_install)

    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "terraria", "game_edition": "", "software": "vanilla", "version": "1.4.4.9", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 400
    assert "error" in response.get_json()

    status_response = dashboard_client.get("/api/catalog/install/status")
    data = status_response.get_json()
    assert data["active"] is False
    assert data["ok"] is False


def test_install_unknown_combo_rejected(dashboard_client, monkeypatch):
    token = login(dashboard_client)
    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "minecraft", "game_edition": "bedrock", "software": "paper", "version": "1.0", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 400
    assert "desconocida" in response.get_json()["error"]


def test_operator_cannot_install(dashboard_client):
    with dashboard_client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    token = login(dashboard_client, "operator1", "another-strong-pw")

    response = dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "minecraft", "game_edition": "java", "software": "paper", "version": "1.21.1", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 403
