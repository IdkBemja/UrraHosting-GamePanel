"""Terraria Dedicated Server (vanilla) adapter (plan.md section 4). Terraria
has no standard RCON protocol, so control goes through a FIFO that is wired
to the process's stdin by the control agent; this adapter only describes
*what* to write, never touches sockets/pipes directly (see
runtime/game_control_agent.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from config.game_properties import upsert_properties

from .base import AdapterConfigError, GameRuntimeAdapter

_DIFFICULTY_MAP = {"classic": "0", "expert": "1", "master": "2", "journey": "3"}


class TerrariaAdapter(GameRuntimeAdapter):
    control_channel = "fifo"
    protocol = "tcp"
    default_port = 7777

    def file_categories(self, config) -> list[str]:
        return ["game", "worlds", "uploads", "backups"]

    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        errors: list[str] = []
        difficulty = (env.get("DIFFICULTY") or "classic").lower()
        if difficulty not in _DIFFICULTY_MAP:
            errors.append(f"DIFFICULTY: '{difficulty}' invalido, debe ser uno de {sorted(_DIFFICULTY_MAP)}")
        return errors

    def stop_command(self) -> str | None:
        return "exit"

    def _world_path(self, config, server_dir: Path) -> Path:
        return server_dir / "worlds" / f"{config.world_name}.wld"

    def prepare(self, config, env: Mapping[str, str], server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "worlds").mkdir(parents=True, exist_ok=True)

        difficulty = (env.get("DIFFICULTY") or "classic").strip().lower()
        world_path = self._world_path(config, server_dir)

        values = {
            "world": str(world_path),
            "worldname": config.world_name,
            "difficulty": _DIFFICULTY_MAP[difficulty],
            "maxplayers": str(config.max_players),
            "port": str(config.game_port),
            "password": "",
            "secure": "1",
            "upnp": "0",
            "banlist": "banlist.txt",
        }
        if not world_path.exists():
            # autocreate=2 -> medium world; the server generates it on first
            # boot instead of failing because `world` points to a file that
            # does not exist yet.
            values["autocreate"] = "2"

        upsert_properties(server_dir / "serverconfig.txt", values)

    def launch_command(self, config, env: Mapping[str, str], server_dir: Path) -> list[str]:
        binary = server_dir / "TerrariaServer.bin.x86_64"
        if not binary.exists():
            raise AdapterConfigError("No se encontro TerrariaServer.bin.x86_64; instala el software desde la pestana Software.")
        binary.chmod(0o755)
        return ["./TerrariaServer.bin.x86_64", "-config", "serverconfig.txt"]
