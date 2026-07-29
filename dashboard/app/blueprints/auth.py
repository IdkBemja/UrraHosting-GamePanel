from __future__ import annotations

from functools import wraps

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..extensions import limiter

bp = Blueprint("auth", __name__)


def is_authenticated() -> bool:
    if not (session.get("authenticated") and session.get("username")):
        return False
    user = current_app.config["USERS"].get(session["username"])
    return bool(user and user["active"] and user["role"] == session.get("role"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autorizado"}), 401
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": "No autorizado"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Se requieren permisos de administrador"}), 403
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        user = current_app.config["USERS"].authenticate(username, password)
        if user:
            session.clear()
            session["authenticated"] = True
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = True
            current_app.config["ACTIVITY"].record("login", {"user": username})
            return redirect(url_for("dashboard.index"))

        current_app.config["ACTIVITY"].record("login_failed", {"user": username})
        flash("Credenciales invalidas", "error")

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if is_authenticated():
        current_app.config["ACTIVITY"].record("logout")
    session.clear()
    return redirect(url_for("auth.login"))
