"""Minecraft Bedrock Dedicated Server adapter (plan.md section 4). Bedrock
ships as a self-contained Linux binary (`bedrock_server`) with its shared
libraries alongside it, so it must be run with the extraction directory as
both the working directory and LD_LIBRARY_PATH - unlike Java, there is no
JVM to point at a jar anywhere on disk.

Bedrock added RCON support (enable-rcon / rcon.port / rcon.password in
server.properties) in recent server versions, so this adapter can reuse the
same rcon.py client as Java once enabled.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from config.game_properties import upsert_properties

from .base import AdapterConfigError, GameRuntimeAdapter

_DIFFICULTIES = {"peaceful", "easy", "normal", "hard"}
_GAMEMODES = {"survival", "creative", "adventure"}


class MinecraftBedrockAdapter(GameRuntimeAdapter):
    control_channel = "rcon"
    protocol = "udp"
    default_port = 19132
    RCON_PORT = 25575

    def file_categories(self, config) -> list[str]:
        return ["game", "uploads", "backups"]

    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        errors: list[str] = []
        difficulty = (env.get("DIFFICULTY") or "easy").lower()
        if difficulty not in _DIFFICULTIES:
            errors.append(f"DIFFICULTY: '{difficulty}' invalido, debe ser uno de {sorted(_DIFFICULTIES)}")
        gamemode = (env.get("GAMEMODE") or "survival").lower()
        if gamemode not in _GAMEMODES:
            errors.append(f"GAMEMODE: '{gamemode}' invalido, debe ser uno de {sorted(_GAMEMODES)}")
        return errors

    def rcon_port(self) -> int | None:
        return self.RCON_PORT

    def stop_command(self) -> str | None:
        return "stop"

    def prepare(self, config, env: Mapping[str, str], server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)
        difficulty = (env.get("DIFFICULTY") or "easy").strip().lower()
        gamemode = (env.get("GAMEMODE") or "survival").strip().lower()

        upsert_properties(
            server_dir / "server.properties",
            {
                "server-name": config.motd,
                "gamemode": gamemode,
                "difficulty": difficulty,
                "allow-cheats": "false",
                "max-players": str(config.max_players),
                "online-mode": "true",
                "level-name": config.world_name,
                "server-port": str(config.game_port),
                "server-portv6": str(config.game_port + 1),
                "enable-rcon": "true",
                "rcon.port": str(self.RCON_PORT),
                "rcon.password": config.rcon_password,
            },
        )

    def launch_command(self, config, env: Mapping[str, str], server_dir: Path) -> list[str]:
        binary = server_dir / "bedrock_server"
        if not binary.exists():
            raise AdapterConfigError("No se encontro bedrock_server; instala el software desde la pestana Software.")
        # The dashboard's zip extraction (archive_extract.py) does not
        # preserve the archive's Unix permission bits, so the binary lands
        # without the executable bit even though it was +x inside the
        # official zip - matches the same fix already applied in
        # terraria.py/tmodloader.py's launch_command().
        binary.chmod(0o755)
        return ["./bedrock_server"]

    def launch_env(self, config, env: Mapping[str, str], server_dir: Path) -> dict[str, str]:
        return {"LD_LIBRARY_PATH": "."}
