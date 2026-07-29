"""Software catalog + install/rollback/reprovision API (plan.md sections 5
and 6). The Software tab lets an admin pick Game -> Edition -> Software ->
Version as one cascading form. Installing a combination that differs from
the instance's CURRENT game_family/game_edition reprovisions it: the
override file in config/instance_state.py is updated (surviving even a
plain `docker compose up -d` recreate later, since it lives on the
DATA_DIR/install volume) and the game-runtime container is stopped before
installing and (re)started after, using only the start/stop/restart
permissions the dashboard's docker-proxy already had - no new Docker
privileges, no container recreation from here.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from config.game_config import catalog_structure, is_valid_combo
from config.instance_state import write_override

from ..services.catalog import CatalogError
from ..services.docker_client import DockerControlError
from ..services.installer import InstallError
from .auth import admin_required, login_required

bp = Blueprint("catalog", __name__, url_prefix="/api/catalog")


@bp.route("/games")
@login_required
def games():
    return jsonify({"games": catalog_structure()})


@bp.route("/<game>/<edition>/<software>/versions")
@login_required
def versions(game: str, edition: str, software: str):
    edition_norm = "" if edition in ("-", "none") else edition
    if not is_valid_combo(game, edition_norm, software):
        return jsonify({"error": "Combinacion de juego/edicion/software desconocida"}), 400

    channel = request.args.get("channel", "stable")
    try:
        version_list = current_app.config["CATALOG"].list_versions(game, edition_norm, software, channel)
    except CatalogError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"game_family": game, "game_edition": edition_norm, "software": software, "channel": channel, "versions": version_list})


@bp.route("/software")
@login_required
def software_options():
    catalog = current_app.config["CATALOG"]
    game_family = request.args.get("game_family", g.config.game_family)
    game_edition = request.args.get("game_edition", g.config.game_edition)
    options = catalog.software_options(game_family, game_edition)
    return jsonify({"game_family": game_family, "game_edition": game_edition, "options": options})


@bp.route("/install", methods=["POST"])
@admin_required
def install():
    payload = request.get_json(silent=True) or {}
    game_family = (payload.get("game_family") or g.config.game_family).lower()
    game_edition_raw = payload.get("game_edition", g.config.game_edition)
    game_edition = (game_edition_raw or "").lower()
    software = (payload.get("software") or "").lower()
    version = payload.get("version", "")
    channel = payload.get("channel", "stable")
    restart_after = bool(payload.get("restart", True))
    create_backup = bool(payload.get("backup", True))

    if not software or not version:
        return jsonify({"error": "software y version son obligatorios"}), 400
    if not is_valid_combo(game_family, game_edition, software):
        return jsonify({"error": "Combinacion de juego/edicion/software desconocida"}), 400

    config = g.config
    installer = current_app.config["INSTALLER"]
    progress = installer.progress

    if not config.license_accepted:
        return jsonify({"error": "Debes aceptar la licencia del software (LICENSE_ACCEPTED) antes de instalar"}), 400

    is_reprovision = (game_family, game_edition) != (config.game_family, config.game_edition)
    docker_client = current_app.config["DOCKER_CLIENT"]

    progress.update("preparing", "Preparando instalacion...")

    # Stop first: a live process of the OLD game/software must never keep
    # running against files an install is about to replace out from under it.
    try:
        status = docker_client.status()
        if status["running"]:
            progress.update("stopping", "Deteniendo el servidor actual...")
            docker_client.stop(timeout=60)
    except DockerControlError as exc:
        progress.finish(False, f"No se pudo detener la instancia antes de instalar: {exc}")
        return jsonify({"error": f"No se pudo detener la instancia antes de instalar: {exc}"}), 502

    # Every (re)install is a clean install now (by request: the Software tab
    # warns about this in red before confirming). installer.install() itself
    # snapshots and wipes game/; plugins/mods/resourcepacks are separate
    # DATA_DIR mounts it never touches, so they are cleared here instead -
    # g.storage/g.adapter still reflect the OLD software at this point (this
    # request's before_request hook resolved them before /install ran), so
    # this only ever clears what actually belonged to the software being
    # replaced.
    category_warnings: list[str] = []
    try:
        progress.update("cleaning", "Borrando datos existentes para una instalacion limpia...")
        for category in ("plugins", "mods", "resourcepacks"):
            if category in g.storage.categories:
                category_warnings.extend(g.storage.clear_category(category))

        result = installer.install(
            game_family, game_edition, software, version, channel, current_app.config.get("CURRENT_USER", "admin"), create_backup=create_backup
        )
    except (CatalogError, InstallError, OSError) as exc:
        current_app.config["ACTIVITY"].record(
            "install_failed",
            {"game_family": game_family, "game_edition": game_edition, "software": software, "version": version, "error": str(exc)},
        )
        progress.finish(False, str(exc))
        return jsonify({"error": str(exc)}), 400

    progress.update("writing_config", "Guardando configuracion...")
    # Written only after a successful install, so a failed install never
    # leaves the override pointing at software that isn't actually there.
    write_override(
        {
            "GAME_FAMILY": game_family,
            "GAME_EDITION": game_edition,
            "GAME_SOFTWARE": software,
            "GAME_VERSION": version,
            "CHANNEL": channel,
        },
        path=current_app.config["INSTALL_DIR"] / "instance_override.json",
    )

    current_app.config["ACTIVITY"].record(
        "install",
        {
            "game_family": game_family,
            "game_edition": game_edition,
            "software": software,
            "version": version,
            "channel": channel,
            "reprovision": is_reprovision,
            "backup": create_backup,
        },
    )
    all_warnings = category_warnings + result.warnings
    response = {"ok": True, "installation": _result_to_dict(result), "reprovisioned": is_reprovision, "warnings": all_warnings}

    # A reprovision MUST restart: the running control agent (if any)
    # resolved its adapter at process start and won't pick up a different
    # game/edition without one. Same-family software/version upgrades only
    # restart if the admin asked for it.
    if restart_after or is_reprovision:
        progress.update("restarting", "Reiniciando la instancia...")
        try:
            docker_client.start()
            response["restarted"] = True
        except DockerControlError as exc:
            response["restarted"] = False
            response["restart_error"] = str(exc)

    progress.finish(True, "Instalacion completada correctamente")
    return jsonify(response)


@bp.route("/install/status")
@login_required
def install_status():
    installer = current_app.config["INSTALLER"]
    return jsonify(installer.progress.snapshot())


@bp.route("/install/rollback", methods=["POST"])
@admin_required
def rollback():
    installer = current_app.config["INSTALLER"]
    try:
        data = installer.rollback()
    except InstallError as exc:
        installer.progress.finish(False, str(exc))
        return jsonify({"error": str(exc)}), 400
    installer.progress.finish(True, "Restauracion completada correctamente")
    current_app.config["ACTIVITY"].record("install_rollback", {})
    return jsonify({"ok": True, "installation": data})


@bp.route("/installation")
@login_required
def installation():
    installer = current_app.config["INSTALLER"]
    return jsonify({"installation": installer.read_manifest()})


def _result_to_dict(result) -> dict:
    return {
        "game_software": result.game_software,
        "game_version": result.game_version,
        "channel": result.channel,
        "url": result.url,
        "checksum": result.checksum,
        "installed_at": result.installed_at,
        "result": result.result,
        "launch_mode": result.launch_mode,
    }
