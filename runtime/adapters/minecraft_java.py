"""Minecraft Java Edition adapter: covers vanilla, paper, purpur, spigot,
bukkit, fabric, forge and neoforge (plan.md section 4). All of these launch
the same way from the runtime's point of view - either a plain jar or a
generated `run.sh` launcher (Forge/NeoForge) - which is exactly why one
adapter serves every GAME_SOFTWARE value in this family/edition instead of
one class per loader.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from config import runtime_matrix
from config.game_properties import upsert_properties

from .base import AdapterConfigError, GameRuntimeAdapter, memory_to_mb

_DIFFICULTIES = {"peaceful", "easy", "normal", "hard"}
_GAMEMODES = {"survival", "creative", "adventure", "spectator"}
_JAVA_VERSIONS = {"auto", *runtime_matrix.SUPPORTED_JAVA_VERSIONS}

_DEFAULT_JAVA_FLAGS = [
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
]


class MinecraftJavaAdapter(GameRuntimeAdapter):
    control_channel = "rcon"
    protocol = "tcp"
    default_port = 25565
    RCON_PORT = 25575

    def file_categories(self, config) -> list[str]:
        categories = ["game", "resourcepacks", "uploads", "backups"]
        if config.game_software in {"paper", "purpur", "spigot", "bukkit"}:
            categories.insert(1, "plugins")
        if config.game_software in {"fabric", "forge", "neoforge"}:
            categories.insert(1, "mods")
        return categories

    def validate_extra(self, env: Mapping[str, str]) -> list[str]:
        errors: list[str] = []
        java_version = (env.get("JAVA_VERSION") or "auto").lower()
        if java_version not in _JAVA_VERSIONS:
            errors.append(f"JAVA_VERSION: '{java_version}' invalido, debe ser uno de {sorted(_JAVA_VERSIONS)}")
        difficulty = (env.get("DIFFICULTY") or "easy").lower()
        if difficulty not in _DIFFICULTIES:
            errors.append(f"DIFFICULTY: '{difficulty}' invalido, debe ser uno de {sorted(_DIFFICULTIES)}")
        gamemode = (env.get("GAMEMODE") or "survival").lower()
        if gamemode not in _GAMEMODES:
            errors.append(f"GAMEMODE: '{gamemode}' invalido, debe ser uno de {sorted(_GAMEMODES)}")
        online_mode = (env.get("ONLINE_MODE") or "true").lower()
        if online_mode not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            errors.append(f"ONLINE_MODE: '{online_mode}' invalido")
        return errors

    def rcon_port(self) -> int | None:
        return self.RCON_PORT

    def stop_command(self) -> str | None:
        return "stop"

    def prepare(self, config, env: Mapping[str, str], server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "eula.txt").write_text(f"eula={'true' if config.license_accepted else 'false'}\n", encoding="utf-8")

        online_mode = (env.get("ONLINE_MODE") or "true").strip().lower() in {"true", "1", "yes", "on"}
        difficulty = (env.get("DIFFICULTY") or "easy").strip().lower()
        gamemode = (env.get("GAMEMODE") or "survival").strip().lower()

        upsert_properties(
            server_dir / "server.properties",
            {
                "server-port": str(config.game_port),
                "level-name": config.world_name,
                "motd": config.motd,
                "max-players": str(config.max_players),
                "online-mode": "true" if online_mode else "false",
                "difficulty": difficulty,
                "gamemode": gamemode,
                "enable-rcon": "true",
                "rcon.port": str(self.RCON_PORT),
                "rcon.password": config.rcon_password,
                "broadcast-rcon-to-ops": "false",
            },
        )

    def _resolved_java_bin(self, config, env: Mapping[str, str]) -> str:
        resolved, _warning = runtime_matrix.resolve(config.game_version, (env.get("JAVA_VERSION") or "auto").lower())
        return f"/opt/java/{resolved}/bin/java"

    def launch_command(self, config, env: Mapping[str, str], server_dir: Path) -> list[str]:
        manifest_path = server_dir / "installation.json"
        launch_mode = "jar"
        server_jar = "server.jar"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                launch_mode = manifest.get("launch_mode", "jar")
                server_jar = manifest.get("target_filename", "server.jar")
            except (OSError, ValueError):
                pass

        heap_mb = int(memory_to_mb(config.game_memory_limit) * 0.75)
        heap_mb = max(heap_mb, 512)

        if launch_mode == "script":
            run_sh = server_dir / "run.sh"
            if not run_sh.exists():
                raise AdapterConfigError("La instalacion modded no contiene run.sh; reinstala desde la pestana Software.")
            (server_dir / "user_jvm_args.txt").write_text(f"-Xmx{heap_mb}M\n-Xms{heap_mb}M\n", encoding="utf-8")
            return ["bash", str(run_sh), "nogui"]

        jar_path = (server_dir / server_jar).resolve()
        if server_dir not in jar_path.parents:
            raise AdapterConfigError("SERVER_JAR resuelve fuera de server_dir")
        if not jar_path.exists():
            raise AdapterConfigError(f"No se encontro {jar_path.name}; instala una distribucion desde la pestana Software.")

        java_bin = self._resolved_java_bin(config, env)
        return [java_bin, f"-Xmx{heap_mb}M", f"-Xms{heap_mb}M", *_DEFAULT_JAVA_FLAGS, "-jar", str(jar_path), "nogui"]
