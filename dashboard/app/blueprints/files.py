from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..services.storage import PathTraversalError, StorageError
from .auth import login_required

bp = Blueprint("files", __name__, url_prefix="/api/files")


@bp.route("")
@login_required
def categories():
    storage = g.storage
    return jsonify({"categories": storage.categories})


@bp.route("/<category>")
@login_required
def list_files(category: str):
    storage = g.storage
    relative_path = request.args.get("path", "")
    try:
        entries = storage.list_dir(category, relative_path)
    except PathTraversalError as exc:
        return jsonify({"error": str(exc)}), 400
    except StorageError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(
        {
            "category": category,
            "path": relative_path,
            "entries": [asdict(entry) for entry in entries],
            "usage_bytes": storage.usage_bytes(),
            "quota_bytes": storage.quota_bytes,
        }
    )


@bp.route("/<category>/upload", methods=["POST"])
@login_required
def upload_file(category: str):
    storage = g.storage
    if "file" not in request.files:
        return jsonify({"error": "Falta el archivo"}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    relative_dir = request.form.get("path", "")
    overwrite = request.form.get("overwrite", "false").lower() == "true"

    try:
        saved_path = storage.save_upload(
            category,
            relative_dir,
            upload.filename,
            upload.stream,
            request.content_length,
            overwrite=overwrite,
        )
    except PathTraversalError as exc:
        return jsonify({"error": str(exc)}), 400
    except StorageError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("file_upload", {"category": category, "name": saved_path.name})
    return jsonify({"ok": True, "path": saved_path.name})


@bp.route("/<category>/download")
@login_required
def download_file(category: str):
    storage = g.storage
    relative_path = request.args.get("path", "")
    try:
        target = storage.resolve(category, relative_path)
    except PathTraversalError as exc:
        return jsonify({"error": str(exc)}), 400

    if not target.exists() or not target.is_file() or target.is_symlink():
        return jsonify({"error": "Archivo no encontrado"}), 404

    return send_file(target, as_attachment=True, download_name=target.name)


@bp.route("/<category>/delete", methods=["POST"])
@login_required
def delete_file(category: str):
    storage = g.storage
    payload = request.get_json(silent=True) or {}
    relative_path = payload.get("path", "")

    try:
        storage.delete(category, relative_path)
    except PathTraversalError as exc:
        return jsonify({"error": str(exc)}), 400
    except StorageError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("file_delete", {"category": category, "path": relative_path})
    return jsonify({"ok": True})
