"""Thin wrapper around the Docker SDK, talking to the instance's scoped
docker-socket-proxy instead of /var/run/docker.sock directly. Every
operation re-fetches the container and checks its
`com.urrahosting.instance` label matches our own INSTANCE_ID before acting,
so a misconfigured DOCKER_HOST can never make this dashboard control a
container belonging to a different instance. Direct port of
UrraHosting-MinecraftServer's dashboard/app/services/docker_client.py, plus
a `health()` accessor used by the installer's health-check-gated activation
(plan.md section 4.4/9).
"""

from __future__ import annotations

from collections.abc import Iterator

import docker
import docker.errors


class DockerControlError(RuntimeError):
    pass


class InstanceDockerClient:
    def __init__(self, docker_host: str, container_name: str, instance_id: str):
        # docker.DockerClient(...) is NOT lazy: it connects immediately to
        # negotiate the API version. Building it eagerly here would crash
        # the whole app factory if the docker-proxy service isn't reachable
        # yet on a cold start, which `depends_on` alone doesn't guarantee.
        # The real client is built lazily on first use instead.
        self._docker_host = docker_host
        self._client: docker.DockerClient | None = None
        self._container_name = container_name
        self._instance_id = instance_id

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.DockerClient(base_url=self._docker_host)
            except docker.errors.DockerException as exc:
                raise DockerControlError(f"No se pudo conectar al docker-proxy: {exc}") from exc
        return self._client

    def _get_container(self):
        try:
            container = self._get_client().containers.get(self._container_name)
        except docker.errors.NotFound as exc:
            raise DockerControlError(f"Contenedor '{self._container_name}' no encontrado") from exc
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"Error de Docker: {exc}") from exc

        label = (container.labels or {}).get("com.urrahosting.instance")
        if label != self._instance_id:
            raise DockerControlError("El contenedor encontrado no pertenece a esta instancia")
        return container

    def status(self) -> dict:
        container = self._get_container()
        container.reload()
        state = container.attrs.get("State", {})
        return {
            "status": state.get("Status", "unknown"),
            "running": bool(state.get("Running")),
            "started_at": state.get("StartedAt", ""),
        }

    def health(self) -> str | None:
        """Returns the container's Docker HEALTHCHECK status ("healthy",
        "unhealthy", "starting") or None if no healthcheck is defined."""
        container = self._get_container()
        container.reload()
        health = container.attrs.get("State", {}).get("Health")
        return health.get("Status") if health else None

    def start(self) -> None:
        try:
            self._get_container().start()
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo iniciar: {exc}") from exc

    def stop(self, timeout: int = 60) -> None:
        try:
            self._get_container().stop(timeout=timeout)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo detener: {exc}") from exc

    def restart(self, timeout: int = 60) -> None:
        try:
            self._get_container().restart(timeout=timeout)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo reiniciar: {exc}") from exc

    def stream_logs(self, tail: int = 50) -> Iterator[bytes]:
        container = self._get_container()
        try:
            yield from container.logs(stream=True, follow=True, tail=tail, timestamps=False)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo leer logs: {exc}") from exc

    def stats(self) -> dict:
        """One-shot CPU/memory snapshot for the live resource meters (Resumen
        tab). `stream=False` still returns a meaningful `precpu_stats` -
        dockerd itself waits ~1s between the two internal samples before
        answering, the same single call `docker stats --no-stream` makes, so
        this blocks the calling thread for about that long (acceptable here:
        gunicorn runs `--threads 8`, and this is polled at a multi-second
        interval, not per-request)."""
        container = self._get_container()
        try:
            raw = container.stats(stream=False)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudieron obtener las metricas: {exc}") from exc

        memory_stats = raw.get("memory_stats", {}) or {}
        memory_usage = memory_stats.get("usage")
        if memory_usage is not None:
            # Docker's raw "usage" includes the page cache, which balloons
            # with disk I/O (world saves, log writes) without reflecting
            # what the game process actually holds - subtract it so the
            # meter tracks real pressure against the limit, same adjustment
            # `docker stats`' own CLI output makes.
            detail = memory_stats.get("stats", {}) or {}
            cache = detail.get("cache", detail.get("inactive_file", 0))
            memory_usage = max(memory_usage - cache, 0)

        return {
            "cpu_percent": _cpu_percent(raw),
            "memory_usage_bytes": memory_usage,
            "memory_limit_bytes": memory_stats.get("limit"),
        }


def _cpu_percent(raw: dict) -> float | None:
    """Same formula `docker stats` itself uses: CPU-time delta over two
    samples, scaled by the number of CPUs, as a percentage - 100% means one
    full core saturated, so a 2-vCPU limit tops out at 200%, not 100%."""
    cpu_stats = raw.get("cpu_stats", {}) or {}
    precpu_stats = raw.get("precpu_stats", {}) or {}
    cpu_total = cpu_stats.get("cpu_usage", {}).get("total_usage")
    precpu_total = precpu_stats.get("cpu_usage", {}).get("total_usage")
    system_cpu = cpu_stats.get("system_cpu_usage")
    presystem_cpu = precpu_stats.get("system_cpu_usage")
    if None in (cpu_total, precpu_total, system_cpu, presystem_cpu):
        return None

    cpu_delta = cpu_total - precpu_total
    system_delta = system_cpu - presystem_cpu
    if system_delta <= 0 or cpu_delta < 0:
        return 0.0

    online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or []) or 1
    return (cpu_delta / system_delta) * online_cpus * 100.0
