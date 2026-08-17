"""Allowlisted, non-destructive upsert of `key=value` config files.

Shared by every adapter that writes this line-oriented format: Minecraft
Java's and Bedrock's `server.properties`, and Terraria/tModLoader's
`serverconfig.txt` all use the same `key=value` syntax. Only the managed
keys passed in `values` are ever touched; every other line (comments, blank
lines, keys the server itself writes on first boot) is preserved verbatim
and in its original position. Managed keys not yet present are appended at
the end. Direct port of UrraHosting-MinecraftServer's
config/server_properties.py, renamed because it is no longer Minecraft-only.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path


def upsert_properties(path: Path, values: Mapping[str, str], *, remove: Collection[str] = ()) -> None:
    """`remove` drops a key's existing line entirely instead of leaving it
    as-is, for keys the caller only ever wants present CONDITIONALLY (e.g.
    Terraria/tModLoader's `autocreate` - see runtime/adapters/terraria.py -
    which must be gone once a real world exists, not just skipped on the
    write that follows; a key also present in `values` is always written,
    `remove` never overrides that)."""
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    remove_keys = set(remove) - set(values)
    seen_keys: set[str] = set()
    output_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in values:
            output_lines.append(f"{key}={values[key]}")
            seen_keys.add(key)
        elif key in remove_keys:
            continue
        else:
            output_lines.append(line)

    for key, value in values.items():
        if key not in seen_keys:
            output_lines.append(f"{key}={value}")

    path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
