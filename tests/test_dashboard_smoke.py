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


def test_health_endpoint_is_public(client):
    # No login, no CSRF token: this is what compose.yml's dashboard
    # healthcheck calls from inside the container.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


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


def test_boot_survives_invalid_gameplay_setting(tmp_path, monkeypatch):
    """Regression test: an instance whose base env has an adapter-invalid
    DIFFICULTY (e.g. left over from a reprovision to a different game
    family - see tests/test_reprovision_flow.py) must not crash the whole
    dashboard at boot. That value is only ever fixable FROM the running
    dashboard's Configuracion tab, so create_app() must still succeed (with
    a printed warning) instead of raising - only the game-runtime container
    is meant to refuse to boot over it."""
    monkeypatch.setattr(main_module, "_DATA_ROOT", tmp_path)
    env = base_env(GAME_FAMILY="terraria", GAME_EDITION="", GAME_SOFTWARE="vanilla", GAME_VERSION="1.4.4.9", DIFFICULTY="hard")
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    flask_app = main_module.create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        login_page = test_client.get("/login")
        token = _csrf_token(login_page.get_data(as_text=True))
        response = test_client.post(
            "/login", data={"username": "admin", "password": "a-strong-password", "csrf_token": token}, follow_redirects=True
        )
        assert response.status_code == 200
        assert b"dashboard-shell" in response.data


def test_boot_still_fails_on_structurally_invalid_config(tmp_path, monkeypatch):
    """The distinction test_boot_survives_invalid_gameplay_setting relies on:
    a broken instance IDENTITY (not a self-service settings field) must
    still fail loudly at boot - there is no tab that could fix a bogus
    GAME_FAMILY from within the dashboard."""
    monkeypatch.setattr(main_module, "_DATA_ROOT", tmp_path)
    env = base_env(GAME_FAMILY="not-a-real-game")
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match="Configuracion de instancia invalida"):
        main_module.create_app()


def test_security_headers_present(client):
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def _login(client) -> str:
    login_page = client.get("/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/login", data={"username": "admin", "password": "a-strong-password", "csrf_token": token})
    dashboard_page = client.get("/dashboard")
    match = re.search(r'name="csrf-token" content="([^"]+)"', dashboard_page.get_data(as_text=True))
    assert match
    return match.group(1)


def test_csrf_failure_on_api_route_returns_json_not_html(client):
    """Before main.py registered global error handlers, a CSRF failure
    (flask_wtf's CSRFError, an HTTPException) on an /api/* route fell
    through to Flask's default HTML error page - app.js's apiFetch() cannot
    JSON.parse that, so it silently shows a useless generic error with no
    real cause visible, no matter what actually went wrong."""
    _login(client)
    response = client.post("/api/files/game/delete", json={"path": "x"})  # no X-CSRFToken header
    assert response.status_code == 400
    assert response.content_type.startswith("application/json")
    assert "error" in response.get_json()


def test_unhandled_exception_on_api_route_returns_json_500(client, monkeypatch):
    """Any bug/OS-level failure a route doesn't explicitly catch (the
    reported symptom: uploading a .jar to mods failed with a generic
    message and no visible cause) must still come back as clean JSON, never
    Flask's default HTML error page."""
    import flask

    token = _login(client)

    class _FailingDockerClient:
        def status(self):
            raise RuntimeError("boom: simulated unexpected failure")

    with client.application.app_context():
        flask.current_app.config["DOCKER_CLIENT"] = _FailingDockerClient()

    response = client.get("/api/overview", headers={"X-CSRFToken": token})
    assert response.status_code == 500
    assert response.content_type.startswith("application/json")
    assert response.get_json() == {"error": "Error interno del servidor"}


def test_unhandled_exception_on_page_route_is_not_swallowed_as_json(client, monkeypatch):
    """Only /api/* gets the JSON-error treatment - a real bug on a page
    route must behave like normal Flask, never silently turn into a JSON
    blob a browser would just download. Flask's test client re-raises
    unhandled exceptions instead of turning them into an HTTP response
    (TESTING=True's normal behavior, same with or without this app's own
    handlers) - production (TESTING=False) renders Flask's default HTML 500
    page instead, which is what actually matters here: the /api/ branch in
    main.py's _handle_unexpected_exception is never reached for this path."""
    import flask
    import pytest as _pytest

    _login(client)

    def _boom():
        raise RuntimeError("boom")

    with client.application.app_context():
        flask.current_app.view_functions["dashboard.index"] = _boom

    with _pytest.raises(RuntimeError, match="boom"):
        client.get("/dashboard")


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
