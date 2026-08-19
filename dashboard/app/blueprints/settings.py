"""Lets an admin edit a curated, SAFE subset of per-game gameplay settings
(MOTD, max players, difficulty, gamemode, online-mode, Terraria/tModLoader's
anticheat "secure" flag) from a "Configuracion" tab, without exposing the
real server.properties/serverconfig.txt (ports,
world/level name, RCON credentials - anything the panel itself manages or
that could orphan existing data stays out of reach here) and without any new
Docker privileges.

Reuses the exact mechanism the Software tab's reprovisioning already uses
(config/instance_state.py's override file, additive here via a disjoint key
set - see SETTINGS_KEYS) and the exact SAME validation the game-runtime
container itself runs at boot (config/game_config.py's validate() plus each
runtime/adapters/*.py's validate_extra()) - nothing that would fail to boot
can be saved here either.

No live-reload path exists anywhere in this codebase (see
runtime/game_control_agent.py - prepare() only ever runs once, at process
start): applying a saved change always needs a container restart, which this
endpoint never triggers itself - the admin does it from the existing
Reiniciar action (Resumen tab / POST /api/container), same as every other
config change in this panel.
"""

from __future__ import annotations

import os

from flask import Blueprint, current_app, g, jsonify, request

from config.game_config import validate as validate_contract
from config.instance_state import effective_environ, write_override

from .auth import admin_required, login_required

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

_BOOL_TRUE = {"true", "1", "yes", "on"}

_DIFFICULTY_OPTIONS = {
    "minecraft": ["peaceful", "easy", "normal", "hard"],
    "terraria": ["classic", "expert", "master", "journey"],
}
_DIFFICULTY_DEFAULT = {"minecraft": "easy", "terraria": "classic"}
_GAMEMODE_OPTIONS = {
    "java": ["survival", "creative", "adventure", "spectator"],
    "bedrock": ["survival", "creative", "adventure"],
}
_GAMEMODE_DEFAULT = "survival"


def _applicable_keys(config) -> tuple[str, ...]:
    """Which SETTINGS_KEYS actually apply to the instance's CURRENT
    game/edition - mirrors exactly what each runtime/adapters/*.py exposes
    (e.g. ONLINE_MODE only exists for Minecraft Java; Terraria has no MOTD
    concept at all)."""
    if config.game_family == "minecraft" and config.game_edition == "java":
        return ("MOTD", "MAX_PLAYERS", "DIFFICULTY", "GAMEMODE", "ONLINE_MODE")
    if config.game_family == "minecraft" and config.game_edition == "bedrock":
        return ("MOTD", "MAX_PLAYERS", "DIFFICULTY", "GAMEMODE")
    if config.game_family == "terraria":
        return ("MAX_PLAYERS", "DIFFICULTY", "SECURE")
    return ()


def _field_schema(key: str, config, env: dict) -> dict:
    if key == "MOTD":
        return {"key": key, "type": "string", "label": "MOTD (mensaje del dia)", "value": config.motd, "max_length": 200}
    if key == "MAX_PLAYERS":
        return {"key": key, "type": "int", "label": "Jugadores maximos", "value": config.max_players, "min": 1, "max": 100000}
    if key == "DIFFICULTY":
        family_key = "terraria" if config.game_family == "terraria" else "minecraft"
        options = _DIFFICULTY_OPTIONS[family_key]
        value = (env.get("DIFFICULTY") or _DIFFICULTY_DEFAULT[family_key]).strip().lower()
        return {"key": key, "type": "enum", "label": "Dificultad", "value": value, "options": options}
    if key == "GAMEMODE":
        options = _GAMEMODE_OPTIONS[config.game_edition]
        value = (env.get("GAMEMODE") or _GAMEMODE_DEFAULT).strip().lower()
        return {"key": key, "type": "enum", "label": "Modo de juego", "value": value, "options": options}
    if key == "ONLINE_MODE":
        value = (env.get("ONLINE_MODE") or "true").strip().lower() in _BOOL_TRUE
        return {"key": key, "type": "bool", "label": "Modo online (verificacion de cuentas Mojang)", "value": value}
    if key == "SECURE":
        value = (env.get("SECURE") or "true").strip().lower() in _BOOL_TRUE
        return {"key": key, "type": "bool", "label": "Modo seguro (anticheat)", "value": value}
    raise AssertionError(f"clave de ajuste desconocida: {key}")  # pragma: no cover - _applicable_keys() is the only caller


def _override_path():
    return current_app.config["INSTALL_DIR"] / "instance_override.json"


@bp.route("")
@login_required
def get_settings():
    config = g.config
    env = effective_environ(os.environ, path=_override_path())
    keys = _applicable_keys(config)
    return jsonify(
        {
            "game_family": config.game_family,
            "game_edition": config.game_edition,
            "game_software": config.game_software,
            "settings": [_field_schema(key, config, env) for key in keys],
        }
    )


@bp.route("", methods=["POST"])
@admin_required
def update_settings():
    config = g.config
    adapter = g.adapter
    allowed = set(_applicable_keys(config))
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return jsonify({"error": "No se recibio ningun ajuste para guardar"}), 400

    unknown = set(payload) - allowed
    if unknown:
        return jsonify({"error": f"Ajustes no editables para este juego: {sorted(unknown)}"}), 400

    updates: dict[str, str] = {}
    if "MOTD" in payload:
        updates["MOTD"] = str(payload["MOTD"])
    if "MAX_PLAYERS" in payload:
        updates["MAX_PLAYERS"] = str(payload["MAX_PLAYERS"]).strip()
    if "DIFFICULTY" in payload:
        updates["DIFFICULTY"] = str(payload["DIFFICULTY"]).strip().lower()
    if "GAMEMODE" in payload:
        updates["GAMEMODE"] = str(payload["GAMEMODE"]).strip().lower()
    if "ONLINE_MODE" in payload:
        updates["ONLINE_MODE"] = "true" if payload["ONLINE_MODE"] in (True, "true", "1", 1, "on", "yes") else "false"
    if "SECURE" in payload:
        updates["SECURE"] = "true" if payload["SECURE"] in (True, "true", "1", 1, "on", "yes") else "false"

    override_path = _override_path()
    # Same env the game-runtime container's own boot-time validation
    # (config/validate_cli.py) would see after a restart - proposed changes
    # are validated against it with the EXACT same rules, so a save that
    # succeeds here is guaranteed to also boot cleanly.
    candidate_env = effective_environ(os.environ, path=override_path)
    candidate_env.update(updates)

    errors = list(validate_contract(candidate_env).errors)
    errors.extend(adapter.validate_extra(candidate_env))
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    write_override(updates, path=override_path)
    current_app.config["ACTIVITY"].record("settings_update", {"changed": sorted(updates)})

    return jsonify(
        {
            "ok": True,
            "restart_required": True,
            "settings": [_field_schema(key, config, candidate_env) for key in _applicable_keys(config)],
        }
    )
