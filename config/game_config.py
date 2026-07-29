"""Shared, dependency-free validation for the per-instance .env contract of
UrraHosting GamePanel.

This module is the single source of truth for the CORE contract that is
common to every game/edition/software combination: instance identity,
network, secrets, resources and the family/edition/software selection
itself. It intentionally does NOT know about per-game settings (difficulty,
gamemode, world seed, PvP, etc.) - those are validated by each
`runtime.adapters` implementation, which is the only place that knows what
a given piece of server software actually accepts. This mirrors
instance_config.py from UrraHosting-MinecraftServer (same
`load_from_environ(env) -> (config | None, ValidationResult)` shape) but
generalizes SERVER_TYPE into the GAME_FAMILY / GAME_EDITION / GAME_SOFTWARE
triple described in plan.md section 4.

Zero third-party dependencies so it can run unmodified inside the minimal
game-runtime image (entrypoint.sh calls `python3 -m config.validate_cli`
before starting anything) and inside the dashboard.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

_WEAK_SECRETS = {
    "",
    "password123",
    "change_this_secret",
    "change_this_token",
    "admin",
    "changeme",
    "change-me",
    "secreto-unico",
    "secreto-largo-aleatorio",
    "cambia-esta-contrasena-unica",
    "cambia-este-secreto-largo-y-aleatorio",
    "cambia-este-token-largo-y-aleatorio",
}

_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}

# Adapter catalog (plan.md section 4). Every (family, edition) pair maps to
# the set of GAME_SOFTWARE values it accepts; no blueprint or service
# hardcodes this elsewhere.
_FAMILY_EDITIONS = {
    "minecraft": {"java", "bedrock"},
    "terraria": {""},
}
_SOFTWARE_BY_FAMILY_EDITION = {
    ("minecraft", "java"): {
        "vanilla",
        "paper",
        "purpur",
        "spigot",
        "bukkit",
        "fabric",
        "forge",
        "neoforge",
    },
    ("minecraft", "bedrock"): {"bedrock"},
    ("terraria", ""): {"vanilla", "tmodloader"},
}
_CHANNELS = {"stable", "preview"}
_MEMORY_RE = re.compile(r"^\d+[KMG]$")
_VERSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MOTD_MAX_LEN = 200


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


@dataclass
class GameConfig:
    instance_id: str
    data_dir: str
    game_family: str
    game_edition: str
    game_software: str
    game_version: str
    channel: str
    license_accepted: bool
    world_name: str
    max_players: int
    motd: str
    game_port: int
    dashboard_port: int
    rcon_password: str
    game_control_token: str
    app_user: str
    app_password: str
    app_secret: str
    session_cookie_secure: bool
    debug: bool
    max_upload_mb: int
    game_cpu_limit: float
    game_cpu_reservation: float
    game_memory_limit: str
    game_memory_reservation: str
    game_storage_limit_gb: int
    dashboard_cpu_limit: float
    dashboard_memory_limit: str

    @property
    def adapter_id(self) -> str:
        """Matches plan.md section 4's Adapter ID column exactly, e.g.
        'minecraft-java/vanilla', 'minecraft-bedrock/bedrock',
        'terraria/vanilla', 'terraria/tmodloader'."""
        family = f"{self.game_family}-{self.game_edition}" if self.game_edition else self.game_family
        return f"{family}/{self.game_software}"

    @property
    def supports_rcon(self) -> bool:
        return self.game_family == "minecraft"


def _get(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    value = env.get(key)
    if value is None or value == "":
        return default if default is not None else ""
    return value


def _parse_bool(value: str, result: ValidationResult, key: str, *, default: bool) -> bool:
    if value == "":
        return default
    lowered = value.strip().lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    result.error(f"{key}: valor booleano invalido '{value}' (usa true/false)")
    return default


def _parse_int(value: str, result: ValidationResult, key: str, *, min_value: int | None = None, max_value: int | None = None) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        result.error(f"{key}: '{value}' no es un entero valido")
        return None
    if min_value is not None and parsed < min_value:
        result.error(f"{key}: debe ser >= {min_value} (recibido {parsed})")
        return None
    if max_value is not None and parsed > max_value:
        result.error(f"{key}: debe ser <= {max_value} (recibido {parsed})")
        return None
    return parsed


def _parse_float(value: str, result: ValidationResult, key: str, *, min_value: float = 0.0) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        result.error(f"{key}: '{value}' no es un numero valido")
        return None
    if parsed < min_value:
        result.error(f"{key}: debe ser >= {min_value} (recibido {parsed})")
        return None
    return parsed


def _check_secret(value: str, result: ValidationResult, key: str, *, min_length: int) -> None:
    if len(value) < min_length:
        result.error(f"{key}: debe tener al menos {min_length} caracteres")
        return
    if value.strip().lower() in _WEAK_SECRETS:
        result.error(f"{key}: usa un valor por defecto/inseguro conocido, cambialo")


def _memory_to_bytes(value: str) -> int:
    unit = value[-1]
    number = int(value[:-1])
    multiplier = {"K": 1024, "M": 1024**2, "G": 1024**3}[unit]
    return number * multiplier


def validate(env: Mapping[str, str]) -> ValidationResult:
    result = ValidationResult()

    instance_id = _get(env, "INSTANCE_ID")
    try:
        uuid.UUID(instance_id)
    except (ValueError, AttributeError):
        result.error(f"INSTANCE_ID: '{instance_id}' no es un UUID valido")

    data_dir = _get(env, "DATA_DIR")
    if not data_dir:
        result.error("DATA_DIR: no puede estar vacio")
    elif "\x00" in data_dir:
        result.error("DATA_DIR: contiene caracteres invalidos")

    game_family = _get(env, "GAME_FAMILY").lower()
    if game_family not in _FAMILY_EDITIONS:
        result.error(f"GAME_FAMILY: '{game_family}' invalido, debe ser uno de {sorted(_FAMILY_EDITIONS)}")
        game_family = ""

    game_edition = _get(env, "GAME_EDITION").lower()
    if game_family and game_edition not in _FAMILY_EDITIONS[game_family]:
        allowed = sorted(e for e in _FAMILY_EDITIONS[game_family] if e) or ["(vacio)"]
        result.error(f"GAME_EDITION: '{game_edition or '(vacio)'}' invalido para GAME_FAMILY={game_family}, debe ser uno de {allowed}")

    game_software = _get(env, "GAME_SOFTWARE").lower()
    if game_family and (game_family, game_edition) in _SOFTWARE_BY_FAMILY_EDITION:
        allowed_software = _SOFTWARE_BY_FAMILY_EDITION[(game_family, game_edition)]
        if game_software not in allowed_software:
            result.error(
                f"GAME_SOFTWARE: '{game_software}' invalido para {game_family}/{game_edition or '-'}, debe ser uno de {sorted(allowed_software)}"
            )

    game_version = _get(env, "GAME_VERSION")
    if not _VERSION_TOKEN_RE.match(game_version):
        result.error(f"GAME_VERSION: '{game_version}' no tiene un formato de version valido")

    channel = _get(env, "CHANNEL", "stable").lower()
    if channel not in _CHANNELS:
        result.error(f"CHANNEL: '{channel}' invalido, debe ser uno de {sorted(_CHANNELS)}")

    license_accepted = _parse_bool(_get(env, "LICENSE_ACCEPTED", "false"), result, "LICENSE_ACCEPTED", default=False)
    if not license_accepted:
        result.error("LICENSE_ACCEPTED: debes aceptar explicitamente la licencia del software oficial antes de iniciar el servidor")

    world_name = _get(env, "WORLD_NAME", "world")
    if not _WORLD_NAME_RE.match(world_name):
        result.error(f"WORLD_NAME: '{world_name}' contiene caracteres no permitidos o rutas")

    _max_players = _parse_int(_get(env, "MAX_PLAYERS", "20"), result, "MAX_PLAYERS", min_value=1, max_value=100000)

    motd = _get(env, "MOTD", "Servidor de juego")
    if len(motd) > _MOTD_MAX_LEN:
        result.error(f"MOTD: demasiado largo (max {_MOTD_MAX_LEN} caracteres)")

    game_port = _parse_int(_get(env, "GAME_PORT"), result, "GAME_PORT", min_value=1, max_value=65535)
    dashboard_port = _parse_int(_get(env, "DASHBOARD_PORT"), result, "DASHBOARD_PORT", min_value=1, max_value=65535)
    if game_port is not None and dashboard_port is not None and game_port == dashboard_port:
        result.error("GAME_PORT y DASHBOARD_PORT no pueden ser el mismo puerto")

    rcon_password = _get(env, "RCON_PASSWORD")
    if game_family == "minecraft":
        _check_secret(rcon_password, result, "RCON_PASSWORD", min_length=8)

    game_control_token = _get(env, "GAME_CONTROL_TOKEN")
    _check_secret(game_control_token, result, "GAME_CONTROL_TOKEN", min_length=24)

    app_user = _get(env, "APP_USER")
    if not app_user:
        result.error("APP_USER: no puede estar vacio")

    app_password = _get(env, "APP_PASSWORD")
    _check_secret(app_password, result, "APP_PASSWORD", min_length=8)
    if app_user and app_password and app_user.lower() == app_password.lower():
        result.error("APP_PASSWORD: no puede ser igual a APP_USER")

    app_secret = _get(env, "APP_SECRET")
    _check_secret(app_secret, result, "APP_SECRET", min_length=32)

    session_cookie_secure = _parse_bool(_get(env, "SESSION_COOKIE_SECURE", "true"), result, "SESSION_COOKIE_SECURE", default=True)
    debug = _parse_bool(_get(env, "DEBUG", "false"), result, "DEBUG", default=False)
    if debug:
        result.warn("DEBUG=true: no uses esto en produccion, expone el debugger interactivo de Flask")
    if debug and session_cookie_secure:
        result.warn("DEBUG=true junto a SESSION_COOKIE_SECURE=true puede impedir el login en http local (sin TLS)")

    _max_upload_mb = _parse_int(_get(env, "MAX_UPLOAD_MB", "2048"), result, "MAX_UPLOAD_MB", min_value=1, max_value=20000)

    game_cpu_limit = _parse_float(_get(env, "GAME_CPU_LIMIT", "2.0"), result, "GAME_CPU_LIMIT", min_value=0.1)
    game_cpu_reservation = _parse_float(_get(env, "GAME_CPU_RESERVATION", "0.5"), result, "GAME_CPU_RESERVATION", min_value=0.0)
    if game_cpu_limit is not None and game_cpu_reservation is not None and game_cpu_reservation > game_cpu_limit:
        result.error("GAME_CPU_RESERVATION no puede ser mayor que GAME_CPU_LIMIT")

    game_memory_limit = _get(env, "GAME_MEMORY_LIMIT", "6G")
    game_memory_reservation = _get(env, "GAME_MEMORY_RESERVATION", "1G")
    if not _MEMORY_RE.match(game_memory_limit):
        result.error(f"GAME_MEMORY_LIMIT: '{game_memory_limit}' invalido (formato: numero + K/M/G, ej. 6G)")
    if not _MEMORY_RE.match(game_memory_reservation):
        result.error(f"GAME_MEMORY_RESERVATION: '{game_memory_reservation}' invalido (formato: numero + K/M/G)")
    if (
        _MEMORY_RE.match(game_memory_limit)
        and _MEMORY_RE.match(game_memory_reservation)
        and _memory_to_bytes(game_memory_reservation) > _memory_to_bytes(game_memory_limit)
    ):
        result.error("GAME_MEMORY_RESERVATION no puede ser mayor que GAME_MEMORY_LIMIT")

    _game_storage_limit_gb = _parse_int(
        _get(env, "GAME_STORAGE_LIMIT_GB", "20"), result, "GAME_STORAGE_LIMIT_GB", min_value=1, max_value=100000
    )

    _dashboard_cpu_limit = _parse_float(_get(env, "DASHBOARD_CPU_LIMIT", "0.5"), result, "DASHBOARD_CPU_LIMIT", min_value=0.05)
    dashboard_memory_limit = _get(env, "DASHBOARD_MEMORY_LIMIT", "256M")
    if not _MEMORY_RE.match(dashboard_memory_limit):
        result.error(f"DASHBOARD_MEMORY_LIMIT: '{dashboard_memory_limit}' invalido (formato: numero + K/M/G)")

    return result


def load_from_environ(env: Mapping[str, str]) -> tuple[GameConfig | None, ValidationResult]:
    """Validate env and, if valid, build a typed GameConfig.

    Returns (None, result) when validation fails; callers must check
    result.ok before using the config. Per-adapter settings (difficulty,
    gamemode, pvp, etc.) are validated separately by
    `runtime.adapters.get_adapter(...).validate_extra(env)` - see
    config/validate_cli.py, which runs both.
    """
    result = validate(env)
    if not result.ok:
        return None, result

    config = GameConfig(
        instance_id=_get(env, "INSTANCE_ID"),
        data_dir=_get(env, "DATA_DIR"),
        game_family=_get(env, "GAME_FAMILY").lower(),
        game_edition=_get(env, "GAME_EDITION").lower(),
        game_software=_get(env, "GAME_SOFTWARE").lower(),
        game_version=_get(env, "GAME_VERSION"),
        channel=_get(env, "CHANNEL", "stable").lower(),
        license_accepted=_get(env, "LICENSE_ACCEPTED", "false").strip().lower() in _BOOL_TRUE,
        world_name=_get(env, "WORLD_NAME", "world"),
        max_players=int(_get(env, "MAX_PLAYERS", "20")),
        motd=_get(env, "MOTD", "Servidor de juego"),
        game_port=int(_get(env, "GAME_PORT")),
        dashboard_port=int(_get(env, "DASHBOARD_PORT")),
        rcon_password=_get(env, "RCON_PASSWORD"),
        game_control_token=_get(env, "GAME_CONTROL_TOKEN"),
        app_user=_get(env, "APP_USER"),
        app_password=_get(env, "APP_PASSWORD"),
        app_secret=_get(env, "APP_SECRET"),
        session_cookie_secure=_get(env, "SESSION_COOKIE_SECURE", "true").strip().lower() in _BOOL_TRUE,
        debug=_get(env, "DEBUG", "false").strip().lower() in _BOOL_TRUE,
        max_upload_mb=int(_get(env, "MAX_UPLOAD_MB", "2048")),
        game_cpu_limit=float(_get(env, "GAME_CPU_LIMIT", "2.0")),
        game_cpu_reservation=float(_get(env, "GAME_CPU_RESERVATION", "0.5")),
        game_memory_limit=_get(env, "GAME_MEMORY_LIMIT", "6G"),
        game_memory_reservation=_get(env, "GAME_MEMORY_RESERVATION", "1G"),
        game_storage_limit_gb=int(_get(env, "GAME_STORAGE_LIMIT_GB", "20")),
        dashboard_cpu_limit=float(_get(env, "DASHBOARD_CPU_LIMIT", "0.5")),
        dashboard_memory_limit=_get(env, "DASHBOARD_MEMORY_LIMIT", "256M"),
    )
    return config, result


def known_adapter_ids() -> list[str]:
    ids = []
    for (family, edition), softwares in _SOFTWARE_BY_FAMILY_EDITION.items():
        prefix = f"{family}-{edition}" if edition else family
        for software in sorted(softwares):
            ids.append(f"{prefix}/{software}")
    return sorted(ids)


_GAME_LABELS = {"minecraft": "Minecraft", "terraria": "Terraria"}
_EDITION_LABELS = {"java": "Java Edition", "bedrock": "Bedrock Dedicated Server", "": "Dedicated Server"}


def catalog_structure() -> list[dict]:
    """The full known games/editions/software tree, for the Software tab's
    cascading Game -> Edition -> Software selector (plan.md section 5). Built
    from the same `_SOFTWARE_BY_FAMILY_EDITION` table `validate()` uses, so
    the UI and the validator can never disagree about what's a valid combo."""
    by_family: dict[str, dict] = {}
    for (family, edition), softwares in _SOFTWARE_BY_FAMILY_EDITION.items():
        entry = by_family.setdefault(family, {"family": family, "label": _GAME_LABELS.get(family, family.title()), "editions": []})
        entry["editions"].append(
            {
                "edition": edition,
                "label": _EDITION_LABELS.get(edition, edition.title()),
                "software": sorted(softwares),
            }
        )
    return [by_family[family] for family in sorted(by_family)]


def is_valid_combo(game_family: str, game_edition: str, game_software: str) -> bool:
    key = (game_family.lower(), game_edition.lower())
    return key in _SOFTWARE_BY_FAMILY_EDITION and game_software.lower() in _SOFTWARE_BY_FAMILY_EDITION[key]
