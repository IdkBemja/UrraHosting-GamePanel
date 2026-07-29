"""Shared plumbing for every catalog provider (plan.md sections 4.3 and
8.2): a host allowlist enforced on every request AND every redirect hop (so
a compromised/typo'd upstream can never smuggle a download from an
unexpected host), a short-TTL cache, and the `DownloadInfo`/`CatalogError`
types every family-specific provider module returns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

USER_AGENT = "UrraHosting-GamePanel/1.0 (+self-hosted)"
REQUEST_TIMEOUT = 10
CACHE_TTL_SECONDS = 300
_MAX_REDIRECTS = 5


class CatalogError(RuntimeError):
    pass


@dataclass
class DownloadInfo:
    url: str
    filename: str
    size: int | None = None
    sha1: str | None = None
    sha256: str | None = None
    md5: str | None = None
    # "jar" (drop-in replace), "installer" (Forge/NeoForge run-installer),
    # "buildtools" (Spigot/CraftBukkit compile), "zip" (Bedrock/Terraria/
    # tModLoader extraction).
    install_kind: str = "jar"
    minecraft_version: str | None = None
    # For "zip" installs: the file inside the extracted tree that must exist
    # for the install to be considered successful (e.g. "bedrock_server").
    expected_entrypoint: str | None = None


class _TTLCache:
    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)


def _host_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    host = urlsplit(url).hostname or ""
    return host.lower() in allowed_hosts


def request_allowlisted(url: str, allowed_hosts: frozenset[str], *, method: str = "GET", **kwargs) -> requests.Response:
    """`requests` with `allow_redirects=False` and a manual redirect loop
    that re-checks the allowlist on every hop, so a 30x response can never
    quietly send us to an unexpected host."""
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        if not _host_allowed(current_url, allowed_hosts):
            raise CatalogError(f"Host no permitido para descarga/consulta: {current_url}")
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", USER_AGENT)
        try:
            response = requests.request(method, current_url, allow_redirects=False, timeout=REQUEST_TIMEOUT, headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise CatalogError(f"No se pudo contactar {current_url}: {exc}") from exc

        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                raise CatalogError(f"Redireccion sin Location desde {current_url}")
            current_url = location
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise CatalogError(f"Respuesta {response.status_code} de {current_url}: {exc}") from exc
        return response

    raise CatalogError(f"Demasiadas redirecciones al consultar {url}")


def get_json(url: str, allowed_hosts: frozenset[str]) -> dict | list:
    response = request_allowlisted(url, allowed_hosts)
    try:
        return response.json()
    except ValueError as exc:
        raise CatalogError(f"Respuesta invalida (no JSON) de {url}") from exc


def get_text(url: str, allowed_hosts: frozenset[str]) -> str:
    response = request_allowlisted(url, allowed_hosts)
    return response.text
