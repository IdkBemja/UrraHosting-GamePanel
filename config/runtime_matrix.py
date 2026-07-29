"""Minecraft version -> required Java runtime matrix.

This module ONLY applies to the minecraft-java family (plan.md section 4.2:
"la matriz Java se aplica solo a Minecraft Java"; Bedrock/Terraria/tModLoader
never receive JAVA_VERSION and must never call `resolve()`). It is a direct
port of UrraHosting-MinecraftServer's config/java_matrix.py.

Matrix:
  Java 8  : up to 1.16.5
  Java 16 : 1.17.x
  Java 17 : 1.18 - 1.20.4
  Java 21 : 1.20.5 - 1.21.x
  Java 25 : any version outside the legacy "1.x" scheme (Mojang's newer
            year.month releases, e.g. "26.2") - always the newest runtime
            this image ships, since a non-"1.x" version is by construction
            newer than every "1.x" entry above it

Caveat: Minecraft/server-software Java requirements keep moving and this
module has no live source of truth to detect that automatically. An
explicit JAVA_VERSION override is never blocked when it differs from what
the matrix would have picked for JAVA_VERSION=auto - it is honored, with a
warning. Only a JAVA_VERSION value outside SUPPORTED_JAVA_VERSIONS (i.e. a
runtime this image doesn't actually ship) is rejected.
"""

from __future__ import annotations

SUPPORTED_JAVA_VERSIONS = ("8", "16", "17", "21", "25")


class UnsupportedJavaError(ValueError):
    pass


def applies_to(game_family: str, game_edition: str) -> bool:
    return game_family == "minecraft" and game_edition == "java"


def _parse_version(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    return tuple(int(p) for p in parts)


def _pad(version: tuple[int, ...], length: int = 3) -> tuple[int, ...]:
    return version + (0,) * (length - len(version))


def required_java_version(minecraft_version: str) -> str:
    parsed = _pad(_parse_version(minecraft_version))

    if parsed[0] != 1:
        # Not the legacy "1.x" scheme (e.g. "26.2") - unconditionally newer
        # than every "1.x" entry below, so it needs the newest runtime this
        # image ships rather than falling through to the "21" catch-all,
        # which was only ever meant for "1.20.5" and up.
        return "25"
    if parsed < (1, 17, 0):
        return "8"
    if parsed < (1, 18, 0):
        return "16"
    if parsed < (1, 20, 5):
        return "17"
    return "21"


def resolve(minecraft_version: str, java_version: str) -> tuple[str, str | None]:
    """Returns (resolved_java_version, warning_or_None)."""
    required = required_java_version(minecraft_version)

    if java_version == "auto":
        return required, None

    if java_version not in SUPPORTED_JAVA_VERSIONS:
        raise UnsupportedJavaError(
            f"JAVA_VERSION={java_version} no esta entre los runtimes que incluye esta imagen ({', '.join(SUPPORTED_JAVA_VERSIONS)})."
        )

    if java_version != required:
        warning = (
            f"JAVA_VERSION={java_version} fue forzado; segun la matriz conocida, Minecraft "
            f"{minecraft_version} esperaria Java {required}. Continuando con el valor forzado."
        )
        return java_version, warning

    return java_version, None
