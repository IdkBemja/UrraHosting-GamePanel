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

# Only these keys may ever come from the override file. Secrets, ports,
# resource limits, etc. always come from the real process environment -
# the override file cannot be used to smuggle a different secret in.
OVERRIDABLE_KEYS = frozenset({"GAME_FAMILY", "GAME_EDITION", "GAME_SOFTWARE", "GAME_VERSION", "CHANNEL"})


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


def write_override(values: Mapping[str, str], path: Path = DEFAULT_OVERRIDE_PATH) -> None:
    unknown = set(values) - OVERRIDABLE_KEYS
    if unknown:
        raise ValueError(f"Claves no permitidas en el override de instancia: {sorted(unknown)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: str(v) for k, v in values.items()}, indent=2)
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


def clear_override(path: Path = DEFAULT_OVERRIDE_PATH) -> None:
    path.unlink(missing_ok=True)


def effective_environ(base_env: Mapping[str, str], path: Path = DEFAULT_OVERRIDE_PATH) -> dict[str, str]:
    """The env every config/adapter loader should use: `base_env` (the real
    process environment) with the override file's values applied on top for
    the fields it's allowed to touch."""
    merged = dict(base_env)
    merged.update(read_override(path))
    return merged
