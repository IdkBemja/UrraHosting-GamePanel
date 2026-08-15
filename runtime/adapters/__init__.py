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
from .null_adapter import NullAdapter
from .terraria import TerrariaAdapter
from .tmodloader import TModLoaderAdapter


class UnknownAdapterError(ValueError):
    pass


def get_adapter(game_family: str, game_edition: str, game_software: str) -> GameRuntimeAdapter:
    game_family = (game_family or "").lower()
    game_edition = (game_edition or "").lower()
    game_software = (game_software or "").lower()

    # Bootstrap state (config.game_config.GameConfig.is_configured == False):
    # a freshly created instance with no game chosen yet is a valid, expected
    # state, not an error - see runtime/adapters/null_adapter.py.
    if not game_family:
        return NullAdapter()

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


__all__ = ["GameRuntimeAdapter", "NullAdapter", "UnknownAdapterError", "get_adapter"]
