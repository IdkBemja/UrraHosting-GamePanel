"""HTTP client to the game-runtime container's control agent (plan.md
section 4.6). The dashboard never speaks RCON directly and never runs
`docker exec`: every console command goes through this single, token-
authenticated call, and the agent internally decides whether that means
RCON (Minecraft Java/Bedrock) or a stdin pipe write (Terraria/tModLoader).
The same channel also carries the two /lifecycle/* actions installer.py
uses to run Forge/NeoForge/BuildTools installers inside that container's own
memory budget instead of the dashboard's much smaller, fixed one.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

_TIMEOUT = 10
# graceful_stop() on the agent can block up to ~60s (50s grace period + a
# forced terminate/wait) before it answers - see runtime/game_control_agent.py.
_STOP_TIMEOUT = 75
# Matches _INSTALLER_TIMEOUT server-side (runtime/game_control_agent.py) with
# a little headroom, so the client never times out first and leaves the
# agent's install still running with no caller left to read the result.
_INSTALL_TIMEOUT = 920
_USER_AGENT = "UrraHosting-GamePanel/1.0 (+self-hosted)"


class GameControlError(RuntimeError):
    pass


@dataclass
class GameControlClient:
    base_url: str
    token: str

    def health(self) -> dict:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GameControlError(f"No se pudo consultar el agente de control: {exc}") from exc
        return response.json()

    def send_command(self, command: str) -> dict:
        return self._post("/command", {"command": command}, timeout=_TIMEOUT)

    def stop_game(self) -> dict:
        """Stops only the child game process - the agent/container stay up,
        unlike DockerClient.stop() (a full `docker stop`). Used before a
        Forge/NeoForge/BuildTools install so run_installer() below can reuse
        this same, still-alive agent right after."""
        return self._post("/lifecycle/stop", {}, timeout=_STOP_TIMEOUT)

    def run_installer(
        self, jar_name: str, minecraft_version: str, heap_mb: int, args: list[str], chmod_executable: str | None = None
    ) -> dict:
        """Runs a Forge/NeoForge/BuildTools installer jar the caller already
        downloaded and checksum-verified into the install/ bind mount both
        containers share (see installer.py) - inside the game-runtime
        container, under a heap sized off GAME_MEMORY_RESERVATION rather than
        the dashboard's own, much smaller DASHBOARD_MEMORY_LIMIT.

        `chmod_executable`, if given, is a filename the agent should chmod
        0o755 once the install finishes (e.g. "run.sh") - the dashboard's uid
        never owns files the installer wrote inside the game-runtime
        container, so it cannot chmod them itself; only the agent's own uid
        can."""
        return self._post(
            "/lifecycle/install",
            {
                "jar_name": jar_name,
                "minecraft_version": minecraft_version,
                "heap_mb": heap_mb,
                "args": args,
                "chmod_executable": chmod_executable,
            },
            timeout=_INSTALL_TIMEOUT,
        )

    def _post(self, path: str, body: dict, *, timeout: float) -> dict:
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=body,
                headers={"X-Control-Token": self.token, "User-Agent": _USER_AGENT},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise GameControlError(f"No se pudo contactar al agente de control: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code == 401:
            raise GameControlError("Token de control invalido")
        if response.status_code == 429:
            raise GameControlError("Demasiadas solicitudes al agente de control, espera unos segundos")
        if not response.ok:
            raise GameControlError(payload.get("error") or f"El agente de control respondio {response.status_code}")
        return payload
