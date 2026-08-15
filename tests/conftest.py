"""Puts the repo layout on sys.path the same way the dashboard/game-runtime
containers see it at runtime: repo root (for the top-level `config` and
`runtime` packages) and `dashboard/` (so `app.*` resolves exactly like it
does from `/app` inside the dashboard container - see dashboard/Dockerfile).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"

for path in (_REPO_ROOT, _DASHBOARD_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def base_env(**overrides) -> dict:
    """A minimal, valid minecraft-java/paper instance contract. Tests
    override only the keys relevant to what they're checking."""
    env = {
        "INSTANCE_ID": "550e8400-e29b-41d4-a716-446655440000",
        "DATA_DIR": "/data/instances/test",
        "GAME_FAMILY": "minecraft",
        "GAME_EDITION": "java",
        "GAME_SOFTWARE": "paper",
        "GAME_VERSION": "1.21.1",
        "CHANNEL": "stable",
        "LICENSE_ACCEPTED": "true",
        "WORLD_NAME": "world",
        "MAX_PLAYERS": "20",
        "MOTD": "Test server",
        "GAME_PORT": "25565",
        "DASHBOARD_PORT": "8080",
        "RCON_PASSWORD": "a-strong-rcon-password",
        "GAME_CONTROL_TOKEN": "a-long-enough-control-token-value",
        "APP_USER": "admin",
        "APP_PASSWORD": "a-strong-password",
        "APP_SECRET": "x" * 40,
        "SESSION_COOKIE_SECURE": "true",
        "DEBUG": "false",
        "MAX_UPLOAD_MB": "2048",
        "GAME_CPU_LIMIT": "2.0",
        "GAME_CPU_RESERVATION": "0.5",
        "GAME_MEMORY_LIMIT": "6G",
        "GAME_MEMORY_RESERVATION": "1G",
        "GAME_STORAGE_LIMIT_GB": "20",
        "DASHBOARD_CPU_LIMIT": "0.5",
        "DASHBOARD_MEMORY_LIMIT": "256M",
        "JAVA_VERSION": "auto",
        "DIFFICULTY": "easy",
        "GAMEMODE": "survival",
        "ONLINE_MODE": "true",
    }
    env.update(overrides)
    return env


@pytest.fixture
def bootstrap_dashboard_client(tmp_path, monkeypatch):
    """Same as dashboard_client, but for an instance created without a game
    chosen yet (GAME_FAMILY/EDITION/SOFTWARE/VERSION empty, LICENSE_ACCEPTED
    false) - the state a container created straight from
    UrraHosting-Dashboard's platform_stack seed is now in, since it no
    longer collects those fields at creation time (see
    scripts/seed_platform_stack_templates.py::seed_gamepanel() in that
    repo)."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_DATA_ROOT", tmp_path)
    env = base_env(GAME_FAMILY="", GAME_EDITION="", GAME_SOFTWARE="", GAME_VERSION="", LICENSE_ACCEPTED="false")
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    flask_app = main_module.create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


@pytest.fixture
def dashboard_client(tmp_path, monkeypatch):
    """A Flask test client for a full `create_app()`, pointed at a temp
    DATA_ROOT and a fake (unreachable) DOCKER_HOST - InstanceDockerClient/
    GameControlClient/CatalogService never connect at construction time,
    only on first actual use, so building the app is safe without a real
    Docker daemon or network access. Tests that touch Docker or the catalog
    must monkeypatch those specific calls themselves."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_DATA_ROOT", tmp_path)
    env = base_env()
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    flask_app = main_module.create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


def login(client, username: str = "admin", password: str = "a-strong-password") -> str:
    """Logs `client` in and returns a valid CSRF token for tests that need
    to make their own POST/PATCH requests afterwards, via the `X-CSRFToken`
    header (exactly like app.js does).

    auth.login() calls `session.clear()` on success, which wipes whatever
    csrf_token was stored in the session for the pre-login page - so the
    token embedded in THAT page is worthless afterwards. The real frontend
    never notices because the dashboard page it loads right after the
    login redirect renders a fresh `{{ csrf_token() }}` into its own meta
    tag, which stores a new one in the (now-cleared) session. This helper
    does the same: fetch /dashboard once after logging in to mint a fresh
    token before returning it.
    """
    import re

    login_page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.get_data(as_text=True))
    assert match, "csrf token not found in rendered login page"
    client.post("/login", data={"username": username, "password": password, "csrf_token": match.group(1)})

    dashboard_page = client.get("/dashboard")
    fresh_match = re.search(r'name="csrf-token" content="([^"]+)"', dashboard_page.get_data(as_text=True))
    assert fresh_match, "csrf token not found in rendered dashboard page after login"
    return fresh_match.group(1)
