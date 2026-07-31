from __future__ import annotations

from flask import Blueprint, jsonify

# Unauthenticated on purpose: Docker's healthcheck (compose.yml) calls this
# from inside the container with no session/cookies. Deliberately does not
# touch Docker/game-control so a transient hiccup in either doesn't flip
# this container "unhealthy" - same scope as game_control_agent.py's own
# /health, which only confirms the process is alive.
bp = Blueprint("health", __name__)


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})
