"""Abstraction every runtime adapter implements (plan.md section 4:
`GameRuntimeAdapter`). No blueprint, service or the control agent knows a
launch command, a config file format or a port number directly - they only
call through this interface, resolved once per instance from
`GAME_FAMILY`/`GAME_EDITION` via `runtime.adapters.get_adapter`.

Adapters intentionally do not know about Docker, RCON sockets or HTTP: they
return data (argv lists, file contents, port numbers) and the caller
(game_control_agent.py) is the only place that touches the network/process
APIs. This keeps adapters trivially unit-testable without a running server.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from config.game_config import GameConfig


class AdapterConfigError(ValueError):
    pass


class GameRuntimeAdapter(ABC):
    #: "rcon" (Minecraft Java/Bedrock) or "fifo" (Terraria/tModLoader stdin).
    control_channel: str = "rcon"
    #: "tcp" or "udp".
    protocol: str = "tcp"
    default_port: int = 0

    @abstractmethod
    def file_categories(self, config: GameConfig) -> list[str]:
        """Storage categories exposed in the Files tab for this runtime."""

    @abstractmethod
    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        """Adapter-specific env validation; returns a list of error strings
        (empty when valid). Called by config/validate_cli.py in addition to
        config.game_config.validate (the universal contract)."""

    @abstractmethod
    def prepare(self, config: GameConfig, env: Mapping[str, str], server_dir: Path) -> None:
        """Write eula.txt / server.properties / serverconfig.txt / etc. from
        typed templates. Never interpolates untrusted values into shell."""

    @abstractmethod
    def launch_command(self, config: GameConfig, env: Mapping[str, str], server_dir: Path) -> list[str]:
        """Return argv (no shell=True, ever) to start the server process."""

    def stop_command(self) -> str | None:
        """Command text sent through the control channel to ask for a clean
        save+shutdown before SIGTERM. None means "no such command exists for
        this control channel", i.e. the supervisor must rely on SIGTERM."""
        return None

    def rcon_port(self) -> int | None:
        return None

    def backup_pause_commands(self) -> tuple[str, ...] | None:
        """Commands sent through the control channel, in order, right
        before a Backups-tab archive starts copying `game/` while the
        server is running - lets a game that supports it flush + hold
        autosave for the duration of the copy so the backup doesn't race a
        write in progress (see dashboard/app/services/backup.py's docstring
        for the production incident this avoids: a Minecraft world file
        mid-autosave briefly denying reads). None means this control
        channel has no such mechanism; the backup still proceeds, just
        without pausing autosave first - services/backup.py's own per-file
        retry is the fallback for that case."""
        return None

    def backup_resume_commands(self) -> tuple[str, ...] | None:
        """Commands to undo backup_pause_commands() once the archive copy
        finishes (success or failure) - always attempted if the pause
        commands were, so autosave is never left disabled."""
        return None

    def launch_env(self, config: GameConfig, env: Mapping[str, str], server_dir: Path) -> dict[str, str]:
        """Extra environment variables the subprocess needs (e.g.
        LD_LIBRARY_PATH for Bedrock's bundled shared libraries). Merged on
        top of a minimal inherited environment by the control agent."""
        return {}


def memory_to_mb(value: str) -> int:
    unit = value[-1].upper()
    number = int(value[:-1])
    if unit == "G":
        return number * 1024
    if unit == "M":
        return number
    if unit == "K":
        return max(1, number // 1024)
    raise AdapterConfigError(f"Formato de memoria invalido: {value}")
