"""Tests for the "Novedades" feature: parsing CHANGELOG.md's most recent
entry (app/services/patch_notes.py) and the /api/version, /api/patchnotes,
/api/patchnotes/dismiss endpoints (dashboard/app/blueprints/overview.py) that
expose it, with per-user dismissal persisted in UserStore."""

from __future__ import annotations

import re

from app.services import patch_notes

from tests.conftest import login

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def test_parse_latest_entry_takes_the_first_version_block():
    text = (
        "# Changelog\n\n"
        "## [2.1.0] - 2026-01-01\n\n"
        "### Added\n"
        "- Cosa nueva.\n\n"
        "## [2.0.0] - 2025-01-01\n\n"
        "- Cosa vieja, no debe aparecer.\n"
    )
    notes = patch_notes._parse_latest_entry(text)
    assert notes.version == "2.1.0"
    assert "Cosa nueva" in notes.html
    assert "Cosa vieja" not in notes.html


def test_parse_latest_entry_strips_the_date_from_the_header_line():
    """The '- 2026-01-01' after the version bracket must not leak into the
    rendered body as a stray Markdown list item (regression: an earlier
    version of the header regex only consumed '## [x.y.z]', leaving ' -
    DATE' to be parsed as '- DATE', i.e. a one-item bullet list)."""
    text = "## [1.0.0] - 2026-01-01\n\nHola.\n"
    notes = patch_notes._parse_latest_entry(text)
    assert "2026-01-01" not in notes.html
    assert "<li>" not in notes.html


def test_parse_latest_entry_sanitizes_raw_html():
    text = '## [1.0.0]\n\n<script>alert(1)</script>\n\nTexto normal.\n'
    notes = patch_notes._parse_latest_entry(text)
    assert "<script" not in notes.html
    assert "Texto normal" in notes.html


def test_parse_latest_entry_requires_at_least_one_version_header():
    import pytest

    with pytest.raises(ValueError):
        patch_notes._parse_latest_entry("# Changelog\n\nNada aqui.\n")


def test_get_patch_notes_reads_the_real_changelog():
    notes = patch_notes.get_patch_notes()
    assert _VERSION_RE.match(notes.version)
    assert notes.html
    assert "##" not in notes.html


def test_version_endpoint_reflects_changelog(dashboard_client):
    login(dashboard_client)
    response = dashboard_client.get("/api/version")
    assert response.status_code == 200
    assert response.get_json()["version"] == patch_notes.current_version()


def test_get_patchnotes_requires_login(dashboard_client):
    response = dashboard_client.get("/api/patchnotes")
    assert response.status_code in (302, 401)


def test_patchnotes_unseen_for_a_fresh_user_then_seen_after_dismiss(dashboard_client):
    token = login(dashboard_client)
    before = dashboard_client.get("/api/patchnotes")
    assert before.status_code == 200
    data = before.get_json()
    assert data["seen"] is False
    assert data["version"] == patch_notes.current_version()
    assert data["html"]

    dismiss = dashboard_client.post("/api/patchnotes/dismiss", headers={"X-CSRFToken": token})
    assert dismiss.status_code == 200

    after = dashboard_client.get("/api/patchnotes")
    assert after.get_json()["seen"] is True


def test_dismiss_patchnotes_requires_login(dashboard_client):
    response = dashboard_client.post("/api/patchnotes/dismiss")
    assert response.status_code in (302, 400, 401)
