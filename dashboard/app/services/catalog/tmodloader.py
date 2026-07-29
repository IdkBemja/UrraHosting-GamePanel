"""Catalog provider for tModLoader (plan.md sections 4 and 11), sourced from
its official GitHub releases at
https://github.com/tModLoader/tModLoader/releases via the GitHub REST API.
Each release ships a single cross-platform `tModLoader.zip` containing,
among other things, `start-tModLoaderServer.sh` (a self-contained .NET
launcher for Linux) - there is no separate Linux-only asset to pick.
`prerelease: true` on GitHub maps to our "preview" channel.
"""

from __future__ import annotations

from .base import CatalogError, DownloadInfo, _TTLCache, get_json

_HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"})
_RELEASES_URL = "https://api.github.com/repos/tModLoader/tModLoader/releases"
_ASSET_NAME = "tModLoader.zip"


class TModLoaderProvider:
    name = "tmodloader"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def _releases(self) -> list[dict]:
        cached = self._cache.get("tmodloader:releases")
        if cached is not None:
            return cached
        data = get_json(_RELEASES_URL, _HOSTS)
        if not isinstance(data, list) or not data:
            raise CatalogError("GitHub no devolvio releases de tModLoader")
        self._cache.set("tmodloader:releases", data)
        return data

    def _filtered(self, channel: str) -> list[dict]:
        want_preview = channel == "preview"
        return [r for r in self._releases() if bool(r.get("prerelease")) == want_preview]

    def list_versions(self, channel: str = "stable") -> list[str]:
        releases = self._filtered(channel)
        return [r["tag_name"] for r in releases if r.get("tag_name")]

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        releases = self._filtered(channel) or self._releases()
        release = next((r for r in releases if r.get("tag_name") == version), None)
        if release is None:
            raise CatalogError(f"tModLoader {version} no se encontro en el canal {channel}")

        asset = next((a for a in release.get("assets", []) if a.get("name") == _ASSET_NAME), None)
        if asset is None:
            raise CatalogError(f"El release {version} de tModLoader no publica {_ASSET_NAME}")

        digest = asset.get("digest") or ""
        sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None

        return DownloadInfo(
            url=asset["browser_download_url"],
            filename=_ASSET_NAME,
            size=asset.get("size"),
            sha256=sha256,
            install_kind="zip",
            expected_entrypoint="start-tModLoaderServer.sh",
        )


def build_providers(cache: _TTLCache) -> dict[str, object]:
    return {"tmodloader": TModLoaderProvider(cache)}
