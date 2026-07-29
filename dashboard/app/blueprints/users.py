from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session

from .auth import admin_required

bp = Blueprint("users", __name__, url_prefix="/api/users")


@bp.route("", methods=["GET"])
@admin_required
def list_users():
    return jsonify({"users": current_app.config["USERS"].list()})


@bp.route("", methods=["POST"])
@admin_required
def create_user():
    payload = request.get_json(silent=True) or {}
    try:
        current_app.config["USERS"].create(
            payload.get("username", "").strip(), payload.get("password", ""), payload.get("role", "operator")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("user_created", {"user": payload.get("username"), "by": session["username"]})
    return jsonify({"ok": True}), 201


@bp.route("/<username>", methods=["PATCH"])
@admin_required
def update_user(username: str):
    payload = request.get_json(silent=True) or {}
    if username.lower() == session["username"].lower() and (payload.get("active") is False or payload.get("role") == "operator"):
        return jsonify({"error": "No puedes quitarte tus propios permisos de administrador ni desactivarte"}), 400
    try:
        current_app.config["USERS"].update(
            username, password=payload.get("password"), role=payload.get("role"), active=payload.get("active")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("user_updated", {"user": username, "by": session["username"]})
    return jsonify({"ok": True})
