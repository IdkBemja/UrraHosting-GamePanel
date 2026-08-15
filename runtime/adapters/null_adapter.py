"""Adapter used while an instance has no game installed yet (GAME_FAMILY
empty - see config.game_config.GameConfig.is_configured). Returned by
get_adapter() instead of raising UnknownAdapterError, so game_control_agent.py
and the dashboard's create_app()/load_current_instance() can boot normally
against a freshly created, still-unconfigured instance.

launch_command() always raises AdapterConfigError, which
Supervisor.start() (runtime/game_control_agent.py) already treats as
"nothing installed yet, stay up and wait" for any adapter - this class just
makes that the day-one state instead of something only reachable after a
family/edition was chosen but its software wasn't installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from config.game_config import GameConfig

from .base import AdapterConfigError, GameRuntimeAdapter


class NullAdapter(GameRuntimeAdapter):
    control_channel = "fifo"
    protocol = "tcp"
    default_port = 0

    def file_categories(self, config: GameConfig) -> list[str]:
        return []

    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        return []

    def prepare(self, config: GameConfig, env: Mapping[str, str], server_dir: Path) -> None:
        return None

    def launch_command(self, config: GameConfig, env: Mapping[str, str], server_dir: Path) -> list[str]:
        raise AdapterConfigError(
            "Ningun juego instalado todavia - instala uno desde la pestana Software del panel."
        )
