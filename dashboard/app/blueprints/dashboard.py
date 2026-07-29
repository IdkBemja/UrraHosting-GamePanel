from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for

from .auth import is_authenticated

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def root():
    if is_authenticated():
        return redirect(url_for("dashboard.index"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
def index():
    if not is_authenticated():
        return redirect(url_for("auth.login"))
    return render_template("dashboard.html")
