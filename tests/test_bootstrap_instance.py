"""An instance created without a game chosen yet (GAME_FAMILY empty - see
config/game_config.py's GameConfig.is_configured and
runtime/adapters/null_adapter.py) must still boot the dashboard cleanly, and
the Software tab's install flow is what first collects LICENSE_ACCEPTED and
picks a game/edition/software/version for it. This exercises exactly that
path against a full create_app() with a fake Docker client, mirroring
tests/test_reprovision_flow.py's style.
"""

from __future__ import annotations

import json

from app.services.catalog import DownloadInfo
from app.services.installer import InstallResult
from flask import current_app

from tests.conftest import login


class _FakeDockerClient:
    def status(self):
        return {"status": "exited", "running": False, "started_at": ""}

    def stop(self, timeout=60):
        pass

    def start(self):
        pass

    def restart(self, timeout=60):
        pass


def test_bootstrap_instance_boots_and_reports_unconfigured(bootstrap_dashboard_client):
    token = login(bootstrap_dashboard_client)
    response = bootstrap_dashboard_client.get("/api/overview", headers={"X-CSRFToken": token})
    assert response.status_code == 200
    data = response.get_json()
    assert data["game_family"] == ""
    assert data["license_accepted"] is False


def test_bootstrap_install_without_license_flag_rejected(bootstrap_dashboard_client, monkeypatch):
    with bootstrap_dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = _FakeDockerClient()

    token = login(bootstrap_dashboard_client)
    response = bootstrap_dashboard_client.post(
        "/api/catalog/install",
        json={"game_family": "minecraft", "game_edition": "java", "software": "paper", "version": "1.21.1", "channel": "stable"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 400
    assert "licencia" in response.get_json()["error"]


def test_bootstrap_install_with_license_accepted_succeeds_and_persists(bootstrap_dashboard_client, monkeypatch, tmp_path):
    fake_docker = _FakeDockerClient()

    def fake_get_download(game_family, game_edition, game_software, version, channel):
        return DownloadInfo(url="https://example.test/paper.jar", filename="paper.jar", install_kind="jar", expected_entrypoint=None)

    def fake_install(self, game_family, game_edition, game_software, version, channel, actor, create_backup=True, **kwargs):
        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url="https://example.test/paper.jar",
            checksum="abc123",
            installed_at="2026-01-01T00:00:00Z",
            actor=actor,
        )

    with bootstrap_dashboard_client.application.app_context():
        current_app.config["DOCKER_CLIENT"] = fake_docker
        monkeypatch.setattr(current_app.config["CATALOG"], "get_download", fake_get_download)

    from app.services.installer import Installer

    monkeypatch.setattr(Installer, "install", fake_install)

    token = login(bootstrap_dashboard_client)
    response = bootstrap_dashboard_client.post(
        "/api/catalog/install",
        json={
            "game_family": "minecraft",
            "game_edition": "java",
            "software": "paper",
            "version": "1.21.1",
            "channel": "stable",
            "license_accepted": True,
        },
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["reprovisioned"] is True

    override_path = tmp_path / "install" / "instance_override.json"
    override_data = json.loads(override_path.read_text())
    assert override_data["GAME_FAMILY"] == "minecraft"
    assert override_data["LICENSE_ACCEPTED"] == "true"
