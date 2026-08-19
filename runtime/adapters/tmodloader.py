"""tModLoader (modded Terraria) adapter (plan.md section 4). Same
serverconfig.txt format and FIFO control channel as vanilla Terraria, but a
different launcher (tModLoader ships its own `start-tModLoaderServer.sh`
that resolves its bundled, self-contained .NET runtime) and an extra
`mods`/`ModConfigs`/`Players` set of directories.

The documentation must warn operators that every connecting player needs
the same mod set/order as the server (plan.md section 4, tModLoader wiki) -
this adapter does not and cannot enforce that; see the Files tab warning
wired in dashboard/app/templates/dashboard.html.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from config.game_properties import upsert_properties

from .base import AdapterConfigError, GameRuntimeAdapter

_DIFFICULTY_MAP = {"classic": "0", "expert": "1", "master": "2", "journey": "3"}


class TModLoaderAdapter(GameRuntimeAdapter):
    control_channel = "fifo"
    protocol = "tcp"
    default_port = 7777

    def file_categories(self, config) -> list[str]:
        return ["game", "mods", "modconfigs", "worlds", "players", "uploads", "backups"]

    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        errors: list[str] = []
        difficulty = (env.get("DIFFICULTY") or "classic").lower()
        if difficulty not in _DIFFICULTY_MAP:
            errors.append(f"DIFFICULTY: '{difficulty}' invalido, debe ser uno de {sorted(_DIFFICULTY_MAP)}")
        secure = (env.get("SECURE") or "true").lower()
        if secure not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            errors.append(f"SECURE: '{secure}' invalido")
        return errors

    def stop_command(self) -> str | None:
        return "exit"

    def _world_path(self, config, server_dir: Path) -> Path:
        return server_dir / "worlds" / f"{config.world_name}.wld"

    def prepare(self, config, env: Mapping[str, str], server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("worlds", "mods", "modconfigs", "players"):
            (server_dir / sub).mkdir(parents=True, exist_ok=True)

        difficulty = (env.get("DIFFICULTY") or "classic").strip().lower()
        secure = (env.get("SECURE") or "true").strip().lower() in {"true", "1", "yes", "on"}
        world_path = self._world_path(config, server_dir)

        values = {
            "world": str(world_path),
            "worldname": config.world_name,
            "difficulty": _DIFFICULTY_MAP[difficulty],
            "maxplayers": str(config.max_players),
            "port": str(config.game_port),
            "password": "",
            "secure": "1" if secure else "0",
            "upnp": "0",
            "banlist": "banlist.txt",
        }
        if not world_path.exists():
            values["autocreate"] = "2"

        # autocreate must be GONE (not merely unset) once a real world
        # exists - see runtime/adapters/terraria.py's prepare() for why a
        # stale value left behind after the world is created corrupts the
        # server's state on every subsequent boot.
        upsert_properties(server_dir / "serverconfig.txt", values, remove={"autocreate"})

    def launch_command(self, config, env: Mapping[str, str], server_dir: Path) -> list[str]:
        launcher = server_dir / "start-tModLoaderServer.sh"
        if not launcher.exists():
            raise AdapterConfigError("No se encontro start-tModLoaderServer.sh; instala el software desde la pestana Software.")
        launcher.chmod(0o755)
        return [
            "bash",
            "./start-tModLoaderServer.sh",
            "-config",
            "serverconfig.txt",
            "-savedirectory",
            str(server_dir),
        ]
