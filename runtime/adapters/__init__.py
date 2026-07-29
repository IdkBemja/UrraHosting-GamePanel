"""Registry: (GAME_FAMILY, GAME_EDITION) -> GameRuntimeAdapter instance.

This is the only place that maps the typed contract to a concrete adapter
class; everything else (blueprints, the control agent, the entrypoint)
calls `get_adapter(family, edition)` instead of importing an adapter module
directly.
"""

from __future__ import annotations

from .base import GameRuntimeAdapter
from .minecraft_bedrock import MinecraftBedrockAdapter
from .minecraft_java import MinecraftJavaAdapter
from .terraria import TerrariaAdapter
from .tmodloader import TModLoaderAdapter


class UnknownAdapterError(ValueError):
    pass


def get_adapter(game_family: str, game_edition: str, game_software: str) -> GameRuntimeAdapter:
    game_family = (game_family or "").lower()
    game_edition = (game_edition or "").lower()
    game_software = (game_software or "").lower()

    if game_family == "minecraft" and game_edition == "java":
        return MinecraftJavaAdapter()
    if game_family == "minecraft" and game_edition == "bedrock":
        return MinecraftBedrockAdapter()
    if game_family == "terraria" and game_software == "vanilla":
        return TerrariaAdapter()
    if game_family == "terraria" and game_software == "tmodloader":
        return TModLoaderAdapter()

    raise UnknownAdapterError(
        f"No hay adaptador para GAME_FAMILY={game_family!r} GAME_EDITION={game_edition!r} GAME_SOFTWARE={game_software!r}"
    )


__all__ = ["GameRuntimeAdapter", "UnknownAdapterError", "get_adapter"]
