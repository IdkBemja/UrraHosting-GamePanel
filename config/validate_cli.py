"""CLI entrypoint: `python3 -m config.validate_cli`. Validates the full
contract (core + adapter-specific) from the effective environment (real
process environment + instance_state.py's override file, if the instance
was reprovisioned to a different game/edition/software from the dashboard)
and exits non-zero without ever printing secret values, so
runtime/entrypoint.sh can fail fast before starting anything.
"""

from __future__ import annotations

import os
import sys

from config.game_config import validate as validate_core
from config.instance_state import effective_environ


def main() -> int:
    env = effective_environ(os.environ)
    result = validate_core(env)

    try:
        from runtime.adapters import UnknownAdapterError, get_adapter

        if result.ok:
            adapter = get_adapter(
                env.get("GAME_FAMILY", ""),
                env.get("GAME_EDITION", ""),
                env.get("GAME_SOFTWARE", ""),
            )
            for error in adapter.validate_extra(env):
                result.error(error)
    except UnknownAdapterError as exc:
        result.error(str(exc))
    except ImportError:
        # The dashboard container does not ship runtime/adapters; only
        # validate the universal contract there.
        pass

    for warning in result.warnings:
        print(f"[config] advertencia: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"[config] error: {error}", file=sys.stderr)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
