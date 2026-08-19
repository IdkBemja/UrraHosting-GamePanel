"""Reads CHANGELOG.md - the single source of truth for both the panel's own
version string (previously a hardcoded literal in blueprints/overview.py) and
the "Novedades" modal's content - and renders its most recent entry to
sanitized HTML.

CHANGELOG.md lives at the repo root (GitHub convention), which is one level
above where the rest of this package gets copied in the image (see the
Dockerfile's separate `COPY CHANGELOG.md` line) - _find_changelog() checks
both the container layout and the local/dev checkout layout so this works
identically under `pytest` and under the built image.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import markdown as _markdown
import nh3

_ENTRY_RE = re.compile(r"^## \[(?P<version>[^\]]+)\].*$", re.MULTILINE)

_ALLOWED_TAGS = {
    "p", "ul", "ol", "li", "strong", "em", "code", "pre", "a", "h3", "h4",
    "blockquote", "br", "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "code": {"class"},
    "pre": {"class"},
}


@dataclass(frozen=True)
class PatchNotes:
    version: str
    html: str


def _find_changelog() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "CHANGELOG.md",  # local/dev checkout: repo root
        here.parents[2] / "CHANGELOG.md",  # built image: /app/CHANGELOG.md
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"CHANGELOG.md no encontrado, se probaron: {candidates}")


def _parse_latest_entry(text: str) -> PatchNotes:
    matches = list(_ENTRY_RE.finditer(text))
    if not matches:
        raise ValueError("CHANGELOG.md no tiene ninguna entrada '## [version]'")
    first = matches[0]
    version = first.group("version").strip()
    body_start = first.end()
    body_end = matches[1].start() if len(matches) > 1 else len(text)
    body = text[body_start:body_end].strip()
    raw_html = _markdown.markdown(body, extensions=["fenced_code", "tables"])
    safe_html = nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES)
    return PatchNotes(version=version, html=safe_html)


_lock = threading.Lock()
_cache: dict[str, object] = {"mtime": None, "notes": None}


def get_patch_notes() -> PatchNotes:
    """The current (most recent) CHANGELOG.md entry, parsed and rendered to
    sanitized HTML. Cached in memory, reparsed only if the file's mtime
    changed since the last call - cheap dev-time hot-reload with no effect in
    production, where the file never changes without a redeploy (a fresh
    process either way)."""
    path = _find_changelog()
    mtime = path.stat().st_mtime
    with _lock:
        if _cache["mtime"] != mtime:
            _cache["notes"] = _parse_latest_entry(path.read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
        return _cache["notes"]  # type: ignore[return-value]


def current_version() -> str:
    return get_patch_notes().version
