from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from ..services.docker_client import DockerControlError
from .auth import login_required

bp = Blueprint("overview", __name__, url_prefix="/api")


@bp.route("/overview")
@login_required
def overview():
    config = g.config
    storage = g.storage
    docker_client = current_app.config["DOCKER_CLIENT"]
    installer = current_app.config["INSTALLER"]

    try:
        state = docker_client.status()
    except DockerControlError as exc:
        state = {"status": "unknown", "running": False, "started_at": ""}
        docker_error = str(exc)
    else:
        docker_error = None

    installation = installer.read_manifest()

    usage = {category: storage.usage_bytes(category) for category in storage.categories}
    base_domain = current_app.config["PUBLIC_BASE_DOMAIN"]
    if base_domain:
        connection = {"kind": "domain", "address": f"{config.instance_id}.{base_domain}", "port": config.game_port}
    else:
        forwarded_host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0].strip()
        address = current_app.config["PUBLIC_SERVER_ADDRESS"] or forwarded_host.rsplit(":", 1)[0]
        connection = {"kind": "ip", "address": address, "port": config.game_port}

    return jsonify(
        {
            "instance_id": config.instance_id,
            "game_family": config.game_family,
            "game_edition": config.game_edition,
            "game_software": config.game_software,
            "game_version": config.game_version,
            "channel": config.channel,
            "protocol": g.adapter.protocol,
            "running": state["running"],
            "container_status": state["status"],
            "docker_error": docker_error,
            "installation": installation,
            "resources": {
                "game_cpu_limit": config.game_cpu_limit,
                "game_memory_limit": config.game_memory_limit,
                "game_storage_limit_gb": config.game_storage_limit_gb,
                "dashboard_cpu_limit": config.dashboard_cpu_limit,
                "dashboard_memory_limit": config.dashboard_memory_limit,
            },
            "storage": {
                "usage_by_category": usage,
                "usage_bytes": sum(usage.values()),
                "quota_bytes": storage.quota_bytes,
            },
            "connection": connection,
        }
    )


@bp.route("/config")
@login_required
def config_view():
    config = g.config
    return jsonify(
        {
            "instance_id": config.instance_id,
            "game_family": config.game_family,
            "game_edition": config.game_edition,
            "game_software": config.game_software,
            "game_version": config.game_version,
            "channel": config.channel,
            "game_port": config.game_port,
            "dashboard_port": config.dashboard_port,
            "world_name": config.world_name,
            "max_players": config.max_players,
            "motd": config.motd,
            "app_user": config.app_user,
            "max_upload_mb": config.max_upload_mb,
            "session_cookie_secure": config.session_cookie_secure,
            "debug": config.debug,
        }
    )


@bp.route("/activity")
@login_required
def activity():
    activity_log = current_app.config["ACTIVITY"]
    return jsonify({"entries": activity_log.recent(limit=200)})
