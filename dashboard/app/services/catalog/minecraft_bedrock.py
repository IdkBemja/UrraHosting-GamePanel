"""Catalog provider for Minecraft Bedrock Dedicated Server (plan.md sections
4 and 11). Mojang does not publish a version-list JSON API for Bedrock the
way it does for Java; the official download page at
https://www.minecraft.net/en-us/download/server/bedrock is itself backed by
a JSON endpoint the page's own client-side code calls:

  https://net-secondary.web.minecraft-services.net/api/v1.0/download/links

which returns, among others, "serverBedrockLinux" (current stable) and
"serverBedrockPreviewLinux" (preview channel) direct .zip URLs under
https://www.minecraft.net/bedrockdedicatedserver/bin-linux(-preview)/. This
is verified against the live endpoint (see plan.md section 11), not a
scrape of rendered HTML. Mojang does not publish a checksum for these
builds, so `DownloadInfo.sha256` stays None; the installer records the
SHA-256 it computes itself in the install manifest instead of verifying
against a published value that does not exist.
"""

from __future__ import annotations

import re

from .base import CatalogError, DownloadInfo, _TTLCache, get_json

_METADATA_HOSTS = frozenset({"net-secondary.web.minecraft-services.net"})
_DOWNLOAD_HOSTS = frozenset({"www.minecraft.net"})
_LINKS_URL = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
_VERSION_RE = re.compile(r"bedrock-server-([0-9.]+)\.zip", re.IGNORECASE)

_DOWNLOAD_TYPE_BY_CHANNEL = {
    "stable": "serverBedrockLinux",
    "preview": "serverBedrockPreviewLinux",
}


class BedrockProvider:
    name = "bedrock"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def _links(self) -> list[dict]:
        cached = self._cache.get("bedrock:links")
        if cached is not None:
            return cached
        data = get_json(_LINKS_URL, _METADATA_HOSTS)
        links = data.get("result", {}).get("links", [])
        if not links:
            raise CatalogError("La API de Bedrock no devolvio enlaces de descarga")
        self._cache.set("bedrock:links", links)
        return links

    def _entry(self, channel: str) -> dict:
        download_type = _DOWNLOAD_TYPE_BY_CHANNEL.get(channel)
        if download_type is None:
            raise CatalogError(f"Canal invalido para Bedrock: {channel}")
        entry = next((link for link in self._links() if link.get("downloadType") == download_type), None)
        if entry is None:
            raise CatalogError(f"No se encontro un enlace de Bedrock para el canal {channel}")
        return entry

    def list_versions(self, channel: str = "stable") -> list[str]:
        entry = self._entry(channel)
        match = _VERSION_RE.search(entry.get("downloadUrl", ""))
        if not match:
            raise CatalogError("No se pudo determinar la version de Bedrock desde la URL oficial")
        return [match.group(1)]

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        entry = self._entry(channel)
        url = entry.get("downloadUrl", "")
        match = _VERSION_RE.search(url)
        actual_version = match.group(1) if match else version
        if version not in {actual_version, "latest"}:
            raise CatalogError(f"La version {version} ya no es la publicada por Mojang para el canal {channel} (actual: {actual_version})")
        if not url.split("//", 1)[-1].split("/", 1)[0] in _DOWNLOAD_HOSTS:
            raise CatalogError("La URL de descarga de Bedrock no proviene de un host permitido")
        return DownloadInfo(
            url=url,
            filename=f"bedrock-server-{actual_version}.zip",
            install_kind="zip",
            expected_entrypoint="bedrock_server",
        )


def build_providers(cache: _TTLCache) -> dict[str, object]:
    return {"bedrock": BedrockProvider(cache)}
