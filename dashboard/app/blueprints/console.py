from __future__ import annotations

import re
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context

from config.game_config import _memory_to_bytes

from ..services.docker_client import DockerControlError
from ..services.game_control import GameControlError
from .auth import is_authenticated, login_required

bp = Blueprint("console", __name__, url_prefix="/api")

_PLAYERS_RE = re.compile(r"There are\s+(\d+)\s+of a max of\s+(\d+)\s+players online")

# game-runtime's stdout/stderr is shared by three different writers:
# entrypoint.sh's own `log()` (bash, "[entrypoint] ..."), game_control_agent.py's
# own `log()` (Python, "[game-control-agent] ..." - includes its per-request
# HTTP access log), and the actual game server process (Minecraft/Terraria),
# whose stdout/stderr is inherited directly (see Supervisor.start()'s
# `stdout=None, stderr=None`) since it is not redirected anywhere else. The
# Console tab only wants the last one plus whatever the RCON client itself
# reports (already rendered client-side from the command response, never
# part of this stream) - the supervisor/wrapper's own bookkeeping lines are
# internal operational noise, not server output.
_INTERNAL_LOG_PREFIXES = ("[entrypoint]", "[game-control-agent]")


def _players_online() -> tuple[int | None, int | None]:
    adapter = g.adapter
    if adapter.control_channel != "rcon":
        # Terraria/tModLoader have no query protocol; the console can still
        # show a "list" style output, but we cannot parse a player count.
        return None, None
    client = current_app.config["GAME_CONTROL"]
    try:
        result = client.send_command("list")
    except GameControlError:
        return None, None
    match = _PLAYERS_RE.search(result.get("output", ""))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _uptime_seconds(started_at: str) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = int((datetime.now(timezone.utc) - started).total_seconds())
    return max(seconds, 0)


@bp.route("/status")
@login_required
def status():
    docker_client = current_app.config["DOCKER_CLIENT"]
    try:
        state = docker_client.status()
    except DockerControlError as exc:
        return jsonify({"error": str(exc)}), 502

    players_online = players_max = None
    uptime_seconds = None
    # Distinct from `running` above: the CONTAINER can be up/healthy (its
    # control agent answering HTTP) while the actual game child process
    # inside it isn't - e.g. nothing installed yet, or a crashed launch.
    # The Console tab needs this specifically to explain an empty log
    # stream and every command failing with 502, instead of leaving an
    # admin to wonder whether either of those is a bug.
    game_process_running = None
    if state["running"]:
        try:
            game_process_running = current_app.config["GAME_CONTROL"].health().get("running")
        except GameControlError:
            game_process_running = None
        players_online, players_max = _players_online()
        uptime_seconds = _uptime_seconds(state["started_at"])

    return jsonify(
        {
            "container": current_app.config["GAME_CONTAINER_NAME"],
            "running": state["running"],
            "game_process_running": game_process_running,
            "container_status": state["status"],
            "uptime_seconds": uptime_seconds,
            "players_online": players_online,
            "players_max": players_max,
        }
    )


@bp.route("/resources")
@login_required
def resources():
    """Polled every few seconds by the Resumen tab's live CPU/RAM/storage
    meters. Storage usage is cheap regardless of poll rate (storage.py
    caches it with a background refresh - see services/storage.py), but CPU/
    memory require an actual `docker stats` round trip, which only makes
    sense - and is only meaningful - while the container is running."""
    docker_client = current_app.config["DOCKER_CLIENT"]
    config = g.config
    storage = g.storage

    try:
        state = docker_client.status()
    except DockerControlError as exc:
        return jsonify({"error": str(exc)}), 502

    cpu_percent = memory_usage_bytes = None
    if state["running"]:
        try:
            live = docker_client.stats()
        except DockerControlError:
            live = {}
        cpu_percent = live.get("cpu_percent")
        memory_usage_bytes = live.get("memory_usage_bytes")

    return jsonify(
        {
            "running": state["running"],
            "cpu_percent": cpu_percent,
            "cpu_limit_vcpu": config.game_cpu_limit,
            "memory_usage_bytes": memory_usage_bytes,
            # From GAME_MEMORY_LIMIT (.env), not docker stats' own reported
            # cgroup limit: authoritative and always present, whereas the
            # runtime-reported limit is only meaningful once the container
            # is actually running and depends on the host's cgroup backend
            # correctly picking up compose's `deploy.resources.limits`.
            "memory_limit_bytes": _memory_to_bytes(config.game_memory_limit),
            "storage_usage_bytes": storage.usage_bytes(),
            "storage_quota_bytes": storage.quota_bytes,
        }
    )


@bp.route("/container", methods=["POST"])
@login_required
def container_action():
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "")
    docker_client = current_app.config["DOCKER_CLIENT"]

    try:
        if action == "start":
            docker_client.start()
        elif action == "stop":
            docker_client.stop(timeout=60)
        elif action == "restart":
            docker_client.restart(timeout=60)
        else:
            return jsonify({"error": "Accion no valida"}), 400
    except DockerControlError as exc:
        current_app.config["ACTIVITY"].record("container_action_failed", {"action": action, "error": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 502

    current_app.config["ACTIVITY"].record("container_action", {"action": action})
    return jsonify({"ok": True})


@bp.route("/command", methods=["POST"])
@login_required
def send_command():
    payload = request.get_json(silent=True) or {}
    command = (payload.get("command") or "").strip()
    if not command:
        return jsonify({"error": "Comando vacio"}), 400

    client = current_app.config["GAME_CONTROL"]
    try:
        result = client.send_command(command)
    except GameControlError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    current_app.config["ACTIVITY"].record("game_command", {"command": command})
    return jsonify(result)


@bp.route("/logs/stream")
def logs_stream():
    if not is_authenticated():
        return Response("event: error\ndata: No autorizado\n\n", status=401, mimetype="text/event-stream")

    docker_client = current_app.config["DOCKER_CLIENT"]

    def generate():
        try:
            # A larger backlog than before the internal-line filter above -
            # otherwise a container that just started (mostly entrypoint/
            # agent bookkeeping in its most recent lines) would show the
            # Console tab as near-empty even though the server itself has
            # plenty of real output further back.
            for chunk in docker_client.stream_logs(tail=200):
                text = chunk.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    safe_line = line.rstrip()
                    if safe_line and not safe_line.startswith(_INTERNAL_LOG_PREFIXES):
                        yield f"data: {safe_line}\n\n"
        except DockerControlError as exc:
            yield f"event: error\ndata: {exc}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)
