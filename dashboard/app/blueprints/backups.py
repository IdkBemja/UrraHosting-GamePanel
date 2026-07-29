from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request, send_file

from ..services.backup import BackupError
from ..services.docker_client import DockerControlError
from .auth import admin_required, login_required

bp = Blueprint("backups", __name__, url_prefix="/api/backups")


@bp.route("")
@login_required
def list_backups():
    service = current_app.config["BACKUPS"]
    return jsonify({"backups": [asdict(b) for b in service.list()]})


@bp.route("/create", methods=["POST"])
@admin_required
def create_backup():
    service = current_app.config["BACKUPS"]
    try:
        info = service.create(actor=current_app.config.get("CURRENT_USER", "admin"))
    except BackupError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("backup_created", {"id": info.id})
    return jsonify({"ok": True, "backup": asdict(info)})


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
