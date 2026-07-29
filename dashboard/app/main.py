from __future__ import annotations

import os
import time
from pathlib import Path

from flask import Flask, g
from werkzeug.middleware.proxy_fix import ProxyFix

from config.game_config import load_from_environ
from config.instance_state import effective_environ
from runtime.adapters import get_adapter

from .blueprints.auth import bp as auth_bp
from .blueprints.backups import bp as backups_bp
from .blueprints.catalog import bp as catalog_bp
from .blueprints.console import bp as console_bp
from .blueprints.dashboard import bp as dashboard_bp
from .blueprints.files import bp as files_bp
from .blueprints.overview import bp as overview_bp
from .blueprints.users import bp as users_bp
from .extensions import csrf, limiter
from .services.activity import ActivityLog
from .services.backup import BackupService
from .services.catalog import CatalogService
from .services.docker_client import InstanceDockerClient
from .services.game_control import GameControlClient
from .services.installer import Installer
from .services.storage import InstanceStorage
from .services.users import UserStore

_DATA_ROOT = Path("/data")


def _category_roots(data_root: Path) -> dict[str, Path]:
    """Fixed compose volume layout (plan.md section 3): every category the
    Files tab can show maps to one of these mounts. "worlds"/"modconfigs"/
    "players" are subfolders of the SAME `game` mount (Terraria/tModLoader
    keep world saves and mod config alongside the binaries, exactly like
    Minecraft does), not separate volumes - see
    runtime/adapters/*.py's `prepare()`. Built from `data_root`
    (read from the module global at call time, not a stale constant) so
    tests can point it at a temp directory instead of the real `/data`."""
    return {
        "game": data_root / "game",
        "plugins": data_root / "plugins",
        "mods": data_root / "mods",
        "resourcepacks": data_root / "resourcepacks",
        "worlds": data_root / "game" / "worlds",
        "modconfigs": data_root / "game" / "modconfigs",
        "players": data_root / "game" / "players",
        "uploads": data_root / "uploads",
        "backups": data_root / "backups",
    }


def load_current_instance(app: Flask):
    """Resolves the CURRENT (config, adapter, storage) on every request
    instead of once at boot, so reprovisioning an instance to a different
    game/edition/software from the Software tab (see
    dashboard/app/blueprints/catalog.py's /install, and
    config/instance_state.py) takes effect immediately - without needing to
    restart the dashboard container, only game-runtime. Falls back to the
    boot-time config if the override file is ever unreadable/invalid so a
    corrupt override can't take the whole dashboard down."""
    override_path = _DATA_ROOT / "install" / "instance_override.json"
    env = effective_environ(os.environ, path=override_path)
    config, _result = load_from_environ(env)
    if config is None:
        return app.config["_BOOT_INSTANCE"], app.config["_BOOT_ADAPTER"]
    try:
        adapter = get_adapter(config.game_family, config.game_edition, config.game_software)
    except Exception:  # noqa: BLE001 - any resolution failure falls back to boot state
        return app.config["_BOOT_INSTANCE"], app.config["_BOOT_ADAPTER"]
    return config, adapter


def create_app() -> Flask:
    boot_env = effective_environ(os.environ)
    config, result = load_from_environ(boot_env)
    if config is None:
        raise RuntimeError("Configuracion de instancia invalida: " + "; ".join(result.errors))

    adapter = get_adapter(config.game_family, config.game_edition, config.game_software)
    extra_errors = adapter.validate_extra(boot_env)
    if extra_errors:
        raise RuntimeError("Configuracion de adaptador invalida: " + "; ".join(extra_errors))

    for warning in result.warnings:
        print(f"[dashboard][warn] {warning}")

    app = Flask(__name__, template_folder="templates", static_folder="static")

    trusted_proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "0") or "0")
    if trusted_proxy_count > 0:
        app.wsgi_app = ProxyFix(  # type: ignore[assignment]
            app.wsgi_app, x_for=trusted_proxy_count, x_proto=trusted_proxy_count, x_host=trusted_proxy_count
        )

    app.config["SECRET_KEY"] = config.app_secret
    app.config["APP_USER"] = config.app_user
    app.config["APP_PASSWORD"] = config.app_password
    app.config["_BOOT_INSTANCE"] = config
    app.config["_BOOT_ADAPTER"] = adapter
    app.config["PUBLIC_BASE_DOMAIN"] = os.environ.get("PUBLIC_BASE_DOMAIN", "").strip().strip(".")
    app.config["PUBLIC_SERVER_ADDRESS"] = os.environ.get("PUBLIC_SERVER_ADDRESS", "").strip()

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.session_cookie_secure
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
    app.config["MAX_CONTENT_LENGTH"] = config.max_upload_mb * 1024 * 1024

    app.config["GAME_CONTAINER_NAME"] = os.environ.get("GAME_CONTAINER_NAME", f"{config.instance_id}_game_runtime")

    csrf.init_app(app)
    limiter.init_app(app)

    @app.before_request
    def _refresh_instance_state():
        g.config, g.adapter = load_current_instance(app)
        category_roots = _category_roots(_DATA_ROOT)
        categories = g.adapter.file_categories(g.config)
        storage_roots = {name: category_roots[name] for name in categories if name in category_roots}
        g.storage = InstanceStorage(
            roots=storage_roots,
            max_upload_bytes=app.config["MAX_CONTENT_LENGTH"],
            quota_bytes=g.config.game_storage_limit_gb * 1024 * 1024 * 1024,
        )

    panel_dir = _DATA_ROOT / "panel"
    install_dir = _DATA_ROOT / "install"
    backups_dir = _DATA_ROOT / "backups"
    for directory in (panel_dir, install_dir, backups_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app.config["ACTIVITY"] = ActivityLog(panel_dir / "activity.log")
    app.config["USERS"] = UserStore(panel_dir / "panel-users.sqlite3", config.app_user, config.app_password)

    app.config["DOCKER_CLIENT"] = InstanceDockerClient(
        docker_host=os.environ["DOCKER_HOST"],
        container_name=app.config["GAME_CONTAINER_NAME"],
        instance_id=config.instance_id,
    )

    control_port = os.environ.get("GAME_CONTROL_PORT", "8081")
    app.config["GAME_CONTROL"] = GameControlClient(
        base_url=f"http://{os.environ.get('GAME_CONTROL_HOST', 'game-runtime')}:{control_port}",
        token=config.game_control_token,
    )

    catalog = CatalogService()
    app.config["CATALOG"] = catalog
    app.config["INSTALLER"] = Installer(
        catalog=catalog,
        game_dir=_DATA_ROOT / "game",
        install_dir=install_dir,
        max_bytes=app.config["MAX_CONTENT_LENGTH"],
    )
    app.config["INSTALL_DIR"] = install_dir
    app.config["BACKUPS_DIR"] = backups_dir
    app.config["BACKUPS"] = BackupService(game_dir=_DATA_ROOT / "game", backups_dir=backups_dir)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(console_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(backups_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(users_bp)

    _install_security_headers(app, config)

    @app.context_processor
    def inject_current_year():
        return {"current_year": time.strftime("%Y", time.gmtime())}

    return app


def _install_security_headers(app: Flask, config) -> None:
    # No external CDNs: bootstrap-icons is vendored under static/vendor and
    # there is no Google Fonts dependency, so 'self' is enough for every
    # directive - unlike a looser CSP that would need to allowlist CDN hosts.
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    @app.after_request
    def set_security_headers(response):
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        if config.session_cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


if __name__ == "__main__":
    app = create_app()
    instance = app.config["_BOOT_INSTANCE"]
    app.run(host="0.0.0.0", port=instance.dashboard_port, debug=instance.debug)
