"""Lets an admin actually switch which game/edition/software an instance
runs from the dashboard's Software tab, without expanding the dashboard's
Docker permissions beyond start/stop/restart (plan.md section 8.6 - no
`docker exec`, no container recreation, no image references from the
dashboard).

The trick: GAME_FAMILY/GAME_EDITION/GAME_SOFTWARE/GAME_VERSION/CHANNEL are
still read from the process environment by default (whatever `.env` says),
but a small JSON file on the shared `install/` volume can override exactly
those five fields. It is written by the dashboard (which already has
read/write access to DATA_DIR/install) and read by everything that resolves
the instance's contract - config/validate_cli.py, the game-runtime
entrypoint/control agent, and the dashboard itself - so all three always
agree on which adapter is "current" without needing to touch the
container's actual Docker-level environment at all.

Because the override lives on the bind-mounted volume, it survives a plain
`docker compose up -d` recreate too (unlike anything that could only be set
via `docker create`'s Env list) - `.env`'s values become just the initial
defaults for a instance that has never reprovisioned yet.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

DEFAULT_OVERRIDE_PATH = Path("/data/install/instance_override.json")

# Reprovision identity - changing any of these is what the Software tab does.
# LICENSE_ACCEPTED rides along with these five: an instance created without a
# game chosen yet (see config/game_config.py's bootstrap state) never had a
# license to accept in its original .env, so the Software tab's install flow
# is what collects and persists that acceptance here, per install.
IDENTITY_KEYS = frozenset({"GAME_FAMILY", "GAME_EDITION", "GAME_SOFTWARE", "GAME_VERSION", "CHANNEL", "LICENSE_ACCEPTED"})
# Curated, safe-to-self-serve gameplay settings (dashboard/app/blueprints/
# settings.py's "Configuracion" tab) - deliberately NOT the whole of
# server.properties/serverconfig.txt: no ports, paths, world/level name or
# credentials here, only values each runtime/adapters/*.py implementation
# already treats as plain gameplay knobs in its own validate_extra(). Not
# every key applies to every adapter (e.g. ONLINE_MODE only exists for
# Minecraft Java) - settings.py's _applicable_keys() is the actual per-game
# whitelist; this frozenset is just the outer bound both read_override() and
# write_override() enforce.
SETTINGS_KEYS = frozenset({"MOTD", "MAX_PLAYERS", "DIFFICULTY", "GAMEMODE", "ONLINE_MODE"})
# Only these keys may ever come from the override file. Secrets, ports,
# resource limits, etc. always come from the real process environment -
# the override file cannot be used to smuggle a different secret in.
OVERRIDABLE_KEYS = IDENTITY_KEYS | SETTINGS_KEYS


def read_override(path: Path = DEFAULT_OVERRIDE_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in OVERRIDABLE_KEYS and isinstance(v, str)}


def write_override(values: Mapping[str, str], path: Path = DEFAULT_OVERRIDE_PATH, *, merge: bool = True) -> None:
    """Writes `values` into the override file. Merges with whatever is
    already there by default - reprovisioning (identity keys) and the
    Configuracion tab (settings keys) are two independent callers that must
    not clobber each other's fields; pass merge=False to replace the file
    outright (rarely needed - see remove_override_keys() for dropping keys
    without touching the rest)."""
    unknown = set(values) - OVERRIDABLE_KEYS
    if unknown:
        raise ValueError(f"Claves no permitidas en el override de instancia: {sorted(unknown)}")

    merged = dict(read_override(path)) if merge else {}
    merged.update({k: str(v) for k, v in values.items()})

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(merged, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".instance-override-", suffix=".json")
    try:
        # tempfile.mkstemp() creates the file 0600 (owner-only) by default.
        # This file is written by the dashboard container (uid 10001) but
        # MUST be readable by game-runtime's entrypoint/agent (uid 10000) -
        # a different container, different user, no shared group. It holds
        # none of OVERRIDABLE_KEYS' values are secrets (see the allowlist
        # above; APP_SECRET/RCON_PASSWORD/etc can never end up here), so
        # world-readable is fine and simpler than coordinating a shared gid.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def default_gameplay_settings(game_family: str, game_edition: str) -> dict[str, str]:
    """The DIFFICULTY/GAMEMODE/ONLINE_MODE values (see SETTINGS_KEYS) that
    are valid for a FRESH instance of this game/edition - mirrors each
    runtime/adapters/*.py's own "no value set" fallback.

    Used by the Software tab's reprovision path (dashboard/app/blueprints/
    catalog.py) to seed the override with values its NEW adapter actually
    accepts. Merely deleting the old override keys is not enough: the base
    process environment (whatever `.env` says) still supplies a value for
    an unset key, and that value was chosen for the OLD family - e.g. a
    Minecraft instance's DIFFICULTY=hard would otherwise resurface as the
    "effective" value for Terraria (whose adapter only accepts classic/
    expert/master/journey) and crash-loop the container on every restart."""
    if game_family == "terraria":
        return {"DIFFICULTY": "classic"}
    if game_family == "minecraft":
        settings = {"DIFFICULTY": "easy", "GAMEMODE": "survival"}
        if game_edition == "java":
            settings["ONLINE_MODE"] = "true"
        return settings
    return {}


def remove_override_keys(keys: frozenset[str] | set[str], path: Path = DEFAULT_OVERRIDE_PATH) -> None:
    """Drops `keys` from the override file, keeping every other field as-is.
    Used when reprovisioning to a different game/edition/software (see
    dashboard/app/blueprints/catalog.py): a saved DIFFICULTY/GAMEMODE value
    can be meaningless or outright invalid for the new adapter (Terraria's
    difficulty values aren't Minecraft's), so those must not silently carry
    over - MOTD/MAX_PLAYERS are universal and are left untouched."""
    current = read_override(path)
    remaining = {k: v for k, v in current.items() if k not in keys}
    if remaining == current:
        return
    write_override(remaining, path=path, merge=False)


def clear_override(path: Path = DEFAULT_OVERRIDE_PATH) -> None:
    path.unlink(missing_ok=True)


def effective_environ(base_env: Mapping[str, str], path: Path = DEFAULT_OVERRIDE_PATH) -> dict[str, str]:
    """The env every config/adapter loader should use: `base_env` (the real
    process environment) with the override file's values applied on top for
    the fields it's allowed to touch."""
    merged = dict(base_env)
    merged.update(read_override(path))
    return merged
