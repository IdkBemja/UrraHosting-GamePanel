"""End-to-end-ish smoke tests for the Flask app factory: auth, CSRF, RBAC
and security headers (plan.md sections 5 and 8.7). No real Docker/network
access is needed - InstanceDockerClient/GameControlClient/CatalogService
never connect at construction time, only on first actual use, so
`create_app()` is safe to call in-process against a temp DATA_ROOT.
"""

import re

import app.main as main_module
import pytest

from tests.conftest import base_env


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "_DATA_ROOT", tmp_path)
    env = base_env()
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    flask_app = main_module.create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in rendered login page"
    return match.group(1)


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"UrraHosting" in response.data


def test_unauthenticated_api_returns_401(client):
    response = client.get("/api/overview")
    assert response.status_code == 401


def test_login_without_csrf_token_rejected(client):
    response = client.post("/login", data={"username": "admin", "password": "a-strong-password"})
    assert response.status_code in (400, 403)


def test_login_success_and_dashboard_access(client):
    login_page = client.get("/login")
    token = _csrf_token(login_page.get_data(as_text=True))

    response = client.post(
        "/login", data={"username": "admin", "password": "a-strong-password", "csrf_token": token}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"dashboard-shell" in response.data


def test_login_wrong_password_rejected(client):
    login_page = client.get("/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    response = client.post("/login", data={"username": "admin", "password": "wrong-password", "csrf_token": token}, follow_redirects=True)
    assert b"Credenciales invalidas" in response.data


def test_security_headers_present(client):
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_operator_cannot_manage_users(client, monkeypatch):
    login_page = client.get("/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/login", data={"username": "admin", "password": "a-strong-password", "csrf_token": token})

    # Promote-then-demote path is simplest: create an operator directly via
    # the store (bypassing HTTP CSRF plumbing) then log in as them.
    from flask import current_app

    with client.application.app_context():
        current_app.config["USERS"].create("operator1", "another-strong-pw", "operator")
    client.post("/logout")

    login_page = client.get("/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/login", data={"username": "operator1", "password": "another-strong-pw", "csrf_token": token})

    response = client.get("/api/users")
    assert response.status_code == 403
