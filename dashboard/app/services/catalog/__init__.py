"""Aggregates every family/edition-specific provider behind one
`CatalogService`, keyed the same way as `GameConfig.adapter_id`
(plan.md section 4) so blueprints never import a provider module directly.
"""

from __future__ import annotations

from . import minecraft_bedrock, minecraft_java, terraria, tmodloader
from .base import CACHE_TTL_SECONDS, CatalogError, DownloadInfo, _TTLCache

ALL_ALLOWED_HOSTS = frozenset().union(
    {"launchermeta.mojang.com", "piston-meta.mojang.com", "piston-data.mojang.com"},
    {"fill.papermc.io", "fill-data.papermc.io"},
    {"api.purpurmc.org"},
    {"hub.spigotmc.org"},
    {"meta.fabricmc.net", "maven.fabricmc.net"},
    {"maven.minecraftforge.net"},
    {"maven.neoforged.net"},
    {"net-secondary.web.minecraft-services.net", "www.minecraft.net"},
    {"terraria.org"},
    {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"},
)


class CatalogService:
    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS):
        cache = _TTLCache(ttl_seconds)
        self._by_key: dict[tuple[str, str, str], object] = {}

        for software, provider in minecraft_java.build_providers(cache).items():
            self._by_key[("minecraft", "java", software)] = provider
        for software, provider in minecraft_bedrock.build_providers(cache).items():
            self._by_key[("minecraft", "bedrock", software)] = provider
        for software, provider in terraria.build_providers(cache).items():
            self._by_key[("terraria", "", software)] = provider
        for software, provider in tmodloader.build_providers(cache).items():
            self._by_key[("terraria", "", software)] = provider

    def software_options(self, game_family: str, game_edition: str) -> list[str]:
        return sorted(software for (family, edition, software) in self._by_key if family == game_family and edition == game_edition)

    def list_versions(self, game_family: str, game_edition: str, game_software: str, channel: str = "stable") -> list[str]:
        return self._provider(game_family, game_edition, game_software).list_versions(channel)

    def get_download(self, game_family: str, game_edition: str, game_software: str, version: str, channel: str = "stable") -> DownloadInfo:
        return self._provider(game_family, game_edition, game_software).get_download(version, channel)

    def _provider(self, game_family: str, game_edition: str, game_software: str):
        provider = self._by_key.get((game_family.lower(), game_edition.lower(), game_software.lower()))
        if provider is None:
            raise CatalogError(f"No hay catalogo automatizado para {game_family}/{game_edition or '-'}/{game_software}")
        return provider


__all__ = ["ALL_ALLOWED_HOSTS", "CatalogError", "CatalogService", "DownloadInfo"]
