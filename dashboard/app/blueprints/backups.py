from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..services.backup import BackupError
from ..services.docker_client import DockerControlError
from ..services.game_control import GameControlError
from .auth import admin_required, login_required

bp = Blueprint("backups", __name__, url_prefix="/api/backups")


@bp.route("")
@login_required
def list_backups():
    service = current_app.config["BACKUPS"]
    return jsonify({"backups": [asdict(b) for b in service.list()]})


@bp.route("/progress")
@login_required
def backup_progress():
    service = current_app.config["BACKUPS"]
    return jsonify(service.get_progress())


def _run_backup_commands(game_control, commands: tuple[str, ...] | None) -> None:
    """Best-effort: sent through the same control channel the Console tab
    uses (dashboard/app/services/game_control.py) - `game_control` is the
    actual client object, not the `current_app` proxy, since this also runs
    from `_on_done` below, on a background thread with no Flask app/request
    context. Silently no-ops if the adapter doesn't support this
    (`commands` is None) or the server isn't running/reachable
    (GameControlError) - services/backup.py's per-file retry is what
    covers the backup in that case instead."""
    if not commands:
        return
    for command in commands:
        try:
            game_control.send_command(command)
        except GameControlError:
            return  # later commands in the sequence would fail the same way


@bp.route("/create", methods=["POST"])
@admin_required
def create_backup():
    service = current_app.config["BACKUPS"]
    activity = current_app.config["ACTIVITY"]
    game_control = current_app.config["GAME_CONTROL"]
    actor = current_app.config.get("CURRENT_USER", "admin")
    pause_commands = g.adapter.backup_pause_commands()
    resume_commands = g.adapter.backup_resume_commands()

    def _on_done(info, error):
        # Runs on the background thread, after the request that triggered
        # it has already returned - ActivityLog.record() and
        # GameControlClient.send_command() are both plain I/O with no
        # Flask app/request context dependency, so this is safe. Resuming
        # autosave here (rather than only synchronously after the request)
        # is what guarantees it always runs exactly once, regardless of how
        # long the archive copy takes or whether it errors out.
        _run_backup_commands(game_control, resume_commands)
        if info is not None:
            activity.record("backup_created", {"id": info.id, "skipped": len(info.skipped_files)})
        elif error:
            activity.record("backup_failed", {"error": error})

    _run_backup_commands(game_control, pause_commands)
    try:
        service.start_create_async(actor=actor, on_done=_on_done)
    except BackupError as exc:
        _run_backup_commands(game_control, resume_commands)
        return jsonify({"error": str(exc)}), 409
    return jsonify({"ok": True, "started": True}), 202


@bp.route("/<backup_id>/download")
@admin_required
def download_backup(backup_id: str):
    service = current_app.config["BACKUPS"]
    match = next((b for b in service.list() if b.id == backup_id), None)
    if match is None:
        return jsonify({"error": "Backup no encontrado"}), 404
    path = current_app.config["BACKUPS_DIR"] / match.filename
    if not path.exists():
        return jsonify({"error": "Archivo de backup no encontrado"}), 404
    return send_file(path, as_attachment=True, download_name=match.filename)


@bp.route("/<backup_id>", methods=["DELETE"])
@admin_required
def delete_backup(backup_id: str):
    service = current_app.config["BACKUPS"]
    try:
        service.delete(backup_id)
    except BackupError as exc:
        return jsonify({"error": str(exc)}), 404
    current_app.config["ACTIVITY"].record("backup_deleted", {"id": backup_id})
    return jsonify({"ok": True})


@bp.route("/<backup_id>/restore", methods=["POST"])
@admin_required
def restore_backup(backup_id: str):
    docker_client = current_app.config["DOCKER_CLIENT"]
    try:
        state = docker_client.status()
    except DockerControlError as exc:
        return jsonify({"error": str(exc)}), 502
    if state["running"]:
        return jsonify({"error": "Detén el servidor antes de restaurar un backup"}), 409

    confirm = (request.get_json(silent=True) or {}).get("confirm", False)
    if not confirm:
        return jsonify({"error": "Confirmacion explicita requerida (confirm: true)"}), 400

    service = current_app.config["BACKUPS"]
    try:
        service.restore(backup_id)
    except BackupError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("backup_restored", {"id": backup_id})
    return jsonify({"ok": True})


@bp.route("/settings")
@login_required
def get_backup_settings():
    service = current_app.config["BACKUPS"]
    return jsonify({"settings": asdict(service.get_settings())})


@bp.route("/settings", methods=["POST"])
@admin_required
def update_backup_settings():
    service = current_app.config["BACKUPS"]
    payload = request.get_json(silent=True) or {}

    kwargs = {}
    if "retention" in payload:
        try:
            kwargs["retention"] = int(payload["retention"])
        except (TypeError, ValueError):
            return jsonify({"error": "Retencion invalida"}), 400
    if "auto_enabled" in payload:
        kwargs["auto_enabled"] = bool(payload["auto_enabled"])
    if "auto_interval_hours" in payload:
        try:
            kwargs["auto_interval_hours"] = int(payload["auto_interval_hours"])
        except (TypeError, ValueError):
            return jsonify({"error": "Intervalo invalido"}), 400

    try:
        settings = service.update_settings(**kwargs)
    except BackupError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("backup_settings_update", {"changed": sorted(kwargs)})
    return jsonify({"ok": True, "settings": asdict(settings)})
