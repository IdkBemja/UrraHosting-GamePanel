"""Catalog provider for Terraria Dedicated Server, vanilla (plan.md sections
4 and 11). Re-Logic does not publish a JSON version-history API (unlike
Mojang's version manifest): terraria.org only ever serves the CURRENT
official Linux dedicated-server build, at

  https://terraria.org/api/download/pc-dedicated-server/terraria-server-<compact-version>.zip

where <compact-version> is the version with dots removed (e.g. "1449" for
1.4.4.9). This provider discovers that current version by looking for that
URL pattern on the public terraria.org site (never following a redirect or
link to any other host) and only ever reports that one version - there is
no "list of past versions" to offer, which matches what Re-Logic actually
publishes. Terraria does not publish a checksum for this build either, so
the installer records the SHA-256 it computes itself.
"""

from __future__ import annotations

import re

from .base import CatalogError, DownloadInfo, _TTLCache, get_text

_HOSTS = frozenset({"terraria.org"})
_INDEX_URL = "https://terraria.org/"
_VERSION_RE = re.compile(r"pc-dedicated-server/terraria-server-(\d+)\.zip", re.IGNORECASE)


def _compact_to_dotted(compact: str) -> str:
    # "1449" -> "1.4.4.9"; Terraria versions are always 4 digits historically.
    if len(compact) == 4:
        return ".".join(compact)
    return compact


class TerrariaProvider:
    name = "vanilla"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def _current_compact_version(self) -> str:
        cached = self._cache.get("terraria:compact_version")
        if cached is not None:
            return cached
        html = get_text(_INDEX_URL, _HOSTS)
        match = _VERSION_RE.search(html)
        if not match:
            raise CatalogError("No se pudo determinar la version actual del servidor dedicado de Terraria")
        compact = match.group(1)
        self._cache.set("terraria:compact_version", compact)
        return compact

    def list_versions(self, channel: str = "stable") -> list[str]:
        compact = self._current_compact_version()
        return [_compact_to_dotted(compact)]

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        compact = self._current_compact_version()
        current_dotted = _compact_to_dotted(compact)
        if version not in {current_dotted, compact, "latest"}:
            raise CatalogError(f"La version {version} ya no es la publicada por Re-Logic (actual: {current_dotted})")
        url = f"https://terraria.org/api/download/pc-dedicated-server/terraria-server-{compact}.zip"
        return DownloadInfo(
            url=url,
            filename=f"terraria-server-{compact}.zip",
            install_kind="zip",
            expected_entrypoint="TerrariaServer.bin.x86_64",
        )


def build_providers(cache: _TTLCache) -> dict[str, object]:
    return {"vanilla": TerrariaProvider(cache)}
