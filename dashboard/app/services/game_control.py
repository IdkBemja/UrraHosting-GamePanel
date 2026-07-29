"""HTTP client to the game-runtime container's control agent (plan.md
section 4.6). The dashboard never speaks RCON directly and never runs
`docker exec`: every console command goes through this single, token-
authenticated call, and the agent internally decides whether that means
RCON (Minecraft Java/Bedrock) or a stdin pipe write (Terraria/tModLoader).
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

_TIMEOUT = 10
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
        try:
            response = requests.post(
                f"{self.base_url}/command",
                json={"command": command},
                headers={"X-Control-Token": self.token, "User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
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
