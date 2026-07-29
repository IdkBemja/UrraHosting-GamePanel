"""Catalog providers for Minecraft Java Edition (plan.md sections 4 and 11).
Only distributions with an official public API are automated: Vanilla
(Mojang's version manifest, verified against
https://launchermeta.mojang.com/mc/game/version_manifest_v2.json), Paper
(PaperMC's v3 "fill" API at fill.papermc.io - the v2 API at api.papermc.io
returns HTTP 410 Gone as of this writing), Purpur (PurpurMC's API), Fabric
(Fabric Meta) and Forge/NeoForge (their Maven metadata + official
installer). Spigot/CraftBukkit run through Spigot's own BuildTools instead
of a redistributed jar, since BuildTools compiles from source under terms
that are ambiguous for redistributing binaries.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import CatalogError, DownloadInfo, _TTLCache, get_json, get_text

_MOJANG_HOSTS = frozenset({"launchermeta.mojang.com", "piston-meta.mojang.com", "piston-data.mojang.com"})
_PAPER_HOSTS = frozenset({"fill.papermc.io", "fill-data.papermc.io"})
_PURPUR_HOSTS = frozenset({"api.purpurmc.org"})
_SPIGOT_HOSTS = frozenset({"hub.spigotmc.org"})
_FABRIC_HOSTS = frozenset({"meta.fabricmc.net", "maven.fabricmc.net"})
_FORGE_HOSTS = frozenset({"maven.minecraftforge.net"})
_NEOFORGE_HOSTS = frozenset({"maven.neoforged.net"})


class VanillaProvider:
    name = "vanilla"
    _MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def list_versions(self, channel: str = "stable") -> list[str]:
        cached = self._cache.get("vanilla:versions")
        if cached is not None:
            return cached
        manifest = get_json(self._MANIFEST_URL, _MOJANG_HOSTS)
        versions = [entry["id"] for entry in manifest["versions"] if entry.get("type") == "release"]
        self._cache.set("vanilla:versions", versions)
        return versions

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        cache_key = f"vanilla:download:{version}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        manifest = get_json(self._MANIFEST_URL, _MOJANG_HOSTS)
        entry = next((v for v in manifest["versions"] if v["id"] == version), None)
        if entry is None:
            raise CatalogError(f"Version de Vanilla desconocida: {version}")

        version_meta = get_json(entry["url"], _MOJANG_HOSTS)
        server = version_meta.get("downloads", {}).get("server")
        if not server:
            raise CatalogError(f"Vanilla {version} no publica un server.jar oficial")

        info = DownloadInfo(url=server["url"], filename="server.jar", size=server.get("size"), sha1=server.get("sha1"))
        self._cache.set(cache_key, info)
        return info


class PaperProvider:
    name = "paper"
    _PROJECT_URL = "https://fill.papermc.io/v3/projects/paper"
    _BUILDS_URL = "https://fill.papermc.io/v3/projects/paper/versions/{version}/builds"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def list_versions(self, channel: str = "stable") -> list[str]:
        cached = self._cache.get("paper:versions")
        if cached is not None:
            return cached
        data = get_json(self._PROJECT_URL, _PAPER_HOSTS)
        versions: list[str] = []
        for group in data.get("versions", {}).values():
            versions.extend(group)
        self._cache.set("paper:versions", versions)
        return versions

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        cache_key = f"paper:download:{version}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        builds = get_json(self._BUILDS_URL.format(version=version), _PAPER_HOSTS)
        if not builds:
            raise CatalogError(f"Paper no tiene builds publicados para {version}")

        stable_builds = [b for b in builds if b.get("channel") == "STABLE"] if channel == "stable" else builds
        candidates = stable_builds or builds
        latest = max(candidates, key=lambda build: build.get("id", 0))
        download = latest.get("downloads", {}).get("server:default")
        if not download:
            raise CatalogError(f"El build {latest.get('id')} de Paper {version} no publica server:default")

        info = DownloadInfo(
            url=download["url"],
            filename=download.get("name", "server.jar"),
            size=download.get("size"),
            sha256=(download.get("checksums") or {}).get("sha256"),
        )
        self._cache.set(cache_key, info)
        return info


class PurpurProvider:
    name = "purpur"
    _PROJECT_URL = "https://api.purpurmc.org/v2/purpur"
    _LATEST_URL = "https://api.purpurmc.org/v2/purpur/{version}/latest"
    _DOWNLOAD_URL = "https://api.purpurmc.org/v2/purpur/{version}/latest/download"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def list_versions(self, channel: str = "stable") -> list[str]:
        cached = self._cache.get("purpur:versions")
        if cached is not None:
            return cached
        data = get_json(self._PROJECT_URL, _PURPUR_HOSTS)
        versions = list(data.get("versions", []))
        self._cache.set("purpur:versions", versions)
        return versions

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        cache_key = f"purpur:download:{version}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        meta = get_json(self._LATEST_URL.format(version=version), _PURPUR_HOSTS)
        if meta.get("result") != "SUCCESS":
            raise CatalogError(f"Purpur {version} no tiene un build exitoso disponible")

        info = DownloadInfo(
            url=self._DOWNLOAD_URL.format(version=version),
            filename=f"purpur-{version}.jar",
            md5=meta.get("md5"),
        )
        self._cache.set(cache_key, info)
        return info


class BuildToolsProvider:
    """Spigot's official BuildTools compiles Spigot/CraftBukkit on demand."""

    _BUILD_TOOLS_URL = "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar"

    def __init__(self, cache: _TTLCache, server_type: str):
        self._cache, self.name = cache, server_type

    def list_versions(self, channel: str = "stable") -> list[str]:
        # BuildTools accepts the same released Minecraft revisions as Vanilla.
        return VanillaProvider(self._cache).list_versions(channel)

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        return DownloadInfo(url=self._BUILD_TOOLS_URL, filename="BuildTools.jar", install_kind="buildtools", minecraft_version=version)


class FabricProvider:
    name = "fabric"
    _GAME_URL = "https://meta.fabricmc.net/v2/versions/game"
    _LOADER_URL = "https://meta.fabricmc.net/v2/versions/loader/{version}"

    def __init__(self, cache: _TTLCache):
        self._cache = cache

    def list_versions(self, channel: str = "stable") -> list[str]:
        cached = self._cache.get("fabric:versions")
        if cached is not None:
            return cached
        versions = [item["version"] for item in get_json(self._GAME_URL, _FABRIC_HOSTS) if item.get("stable")]
        self._cache.set("fabric:versions", versions)
        return versions

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        loaders = get_json(self._LOADER_URL.format(version=version), _FABRIC_HOSTS)
        stable = next((item for item in loaders if item.get("loader", {}).get("stable")), None)
        if not stable:
            raise CatalogError(f"Fabric no tiene un loader estable para {version}")
        loader, installer = stable["loader"]["version"], stable["installer"]["version"]
        url = f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader}/{installer}/server/jar"
        return DownloadInfo(url=url, filename="fabric-server-launch.jar", minecraft_version=version)


class MavenInstallerProvider:
    def __init__(self, cache: _TTLCache, server_type: str, metadata_url: str, base_url: str, allowed_hosts: frozenset[str]):
        self._cache, self.name = cache, server_type
        self._metadata_url, self._base_url, self._hosts = metadata_url, base_url, allowed_hosts

    def list_versions(self, channel: str = "stable") -> list[str]:
        cached = self._cache.get(f"{self.name}:versions")
        if cached is not None:
            return cached
        try:
            text = get_text(self._metadata_url, self._hosts)
            root = ET.fromstring(text)
            versions = [node.text for node in root.findall(".//version") if node.text]
        except ET.ParseError as exc:
            raise CatalogError(f"No se pudo interpretar el catalogo de {self.name}: {exc}") from exc
        self._cache.set(f"{self.name}:versions", list(reversed(versions)))
        return list(reversed(versions))

    def get_download(self, version: str, channel: str = "stable") -> DownloadInfo:
        mc_version = version.split("-", 1)[0] if self.name == "forge" else f"1.{version.split('.', 1)[0]}"
        url = f"{self._base_url}/{version}/{self.name}-{version}-installer.jar"
        return DownloadInfo(url=url, filename=f"{self.name}-installer.jar", install_kind="installer", minecraft_version=mc_version)


def build_providers(cache: _TTLCache) -> dict[str, object]:
    return {
        "vanilla": VanillaProvider(cache),
        "paper": PaperProvider(cache),
        "purpur": PurpurProvider(cache),
        "spigot": BuildToolsProvider(cache, "spigot"),
        "bukkit": BuildToolsProvider(cache, "bukkit"),
        "fabric": FabricProvider(cache),
        "forge": MavenInstallerProvider(
            cache,
            "forge",
            "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml",
            "https://maven.minecraftforge.net/net/minecraftforge/forge",
            _FORGE_HOSTS,
        ),
        "neoforge": MavenInstallerProvider(
            cache,
            "neoforge",
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml",
            "https://maven.neoforged.net/releases/net/neoforged/neoforge",
            _NEOFORGE_HOSTS,
        ),
    }
