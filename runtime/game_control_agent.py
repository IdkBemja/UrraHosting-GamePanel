"""Supervisor process that runs as PID 1's direct child inside game-runtime
(plan.md section 3.4 / 4.6). Responsibilities:

  1. Resolve the adapter for GAME_FAMILY/GAME_EDITION/GAME_SOFTWARE and
     call `adapter.prepare(...)` to (re)write eula.txt/server.properties/
     serverconfig.txt from typed templates.
  2. Spawn the actual game server as a child process (never via shell=True)
     with the adapter's argv/env, cwd'd into /data/game.
  3. Expose a tiny, token-authenticated HTTP API on the internal network
     only (never published in compose) so the dashboard can send console
     commands without `docker exec`: RCON for Minecraft Java/Bedrock, or a
     write to the child's stdin pipe for Terraria/tModLoader. Python's
     `subprocess.PIPE` for stdin *is* an anonymous, kernel-level pipe wired
     directly to the child's fd 0 - a stricter version of the FIFO-to-stdin
     design in plan.md section 4.6, since it is never exposed as a
     filesystem path any other process could open (no 0600 perms to get
     wrong).
  4. Forward SIGTERM/SIGINT into a clean in-game save+shutdown before the
     container's `stop_grace_period` forces a SIGKILL.

Zero third-party dependencies: only stdlib, so the minimal game-runtime
image never needs pip.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, "/opt/urrahosting")

from config import runtime_matrix
from config.game_config import load_from_environ
from config.instance_state import effective_environ
from runtime import rcon
from runtime.adapters import get_adapter
from runtime.adapters.base import AdapterConfigError

SERVER_DIR = Path("/data/game")
# Read-only mount shared with the dashboard's own /data/install (see
# compose.yml) - the dashboard downloads and checksum-verifies the Forge/
# NeoForge/BuildTools installer jar and drops it here (world-readable, same
# convention as config/instance_state.py's write_override()) so this agent
# can run it locally without the jar ever crossing the network a second
# time.
INSTALL_DIR = Path("/data/install")
CONTROL_PORT = int(os.environ.get("GAME_CONTROL_PORT", "8081"))
MAX_COMMAND_LENGTH = 1500
_RATE_LIMIT_WINDOW = 10.0
_RATE_LIMIT_MAX = 20
_INSTALLER_TIMEOUT = 900.0
_MAX_INSTALLER_OUTPUT = 4000


def log(message: str) -> None:
    print(f"[game-control-agent] {message}", flush=True)


class Supervisor:
    def __init__(self) -> None:
        # `effective_environ` applies instance_state.py's override file (if
        # the dashboard reprovisioned this instance to a different
        # game/edition/software) on top of the real process environment -
        # see config/instance_state.py for why this exists.
        self.env = effective_environ(os.environ)
        config, result = load_from_environ(self.env)
        if config is None:
            log("Configuracion invalida, abortando: " + "; ".join(result.errors))
            raise SystemExit(1)
        for warning in result.warnings:
            log(f"advertencia: {warning}")

        self.config = config
        self.adapter = get_adapter(config.game_family, config.game_edition, config.game_software)
        extra_errors = self.adapter.validate_extra(self.env)
        if extra_errors:
            log("Configuracion de adaptador invalida, abortando: " + "; ".join(extra_errors))
            raise SystemExit(1)

        self.process: subprocess.Popen | None = None
        self._stop_requested = threading.Event()
        self._rate_lock = threading.Lock()
        self._rate_hits: list[float] = []

    def start(self) -> None:
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        self.adapter.prepare(self.config, self.env, SERVER_DIR)

        # A brand new instance (or one just reprovisioned to a different
        # game/edition) has nothing installed yet (no jar/binary in
        # SERVER_DIR): launch_command() raises AdapterConfigError for that,
        # by design (see runtime/adapters/*.py). That must NOT crash the
        # agent - there would be no way to reach the dashboard's Software
        # tab to install anything if it did, since Docker would just
        # restart the container into the same failure forever. Instead,
        # stay up with no child process; the control HTTP API and /health
        # keep answering, `is_running()` reports False, and an admin
        # install + container restart (via the dashboard) starts the game
        # for real on the next attempt.
        try:
            argv = self.adapter.launch_command(self.config, self.env, SERVER_DIR)
        except AdapterConfigError as exc:
            log(f"No se pudo iniciar el proceso del juego todavia: {exc}")
            log("El agente de control seguira activo; instala software desde el panel y reinicia la instancia.")
            self.process = None
            return

        env = dict(os.environ)
        env.update(self.adapter.launch_env(self.config, self.env, SERVER_DIR))

        stdin_mode = subprocess.PIPE if self.adapter.control_channel == "fifo" else subprocess.DEVNULL
        log(f"Iniciando proceso del juego (adapter={self.config.adapter_id}): {' '.join(argv)}")
        self.process = subprocess.Popen(
            argv,
            cwd=SERVER_DIR,
            env=env,
            stdin=stdin_mode,
            stdout=None,
            stderr=None,
        )

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def send_command(self, command: str) -> dict:
        if not self.is_running():
            return {"ok": False, "error": "El proceso del juego no esta en ejecucion"}

        if self.adapter.control_channel == "rcon":
            port = self.adapter.rcon_port()
            try:
                output = rcon.execute("127.0.0.1", port, self.config.rcon_password, command, timeout=10.0)
            except rcon.RconError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "output": output}

        # fifo: write straight to the child's stdin pipe; there is no
        # response protocol, so we can only confirm the write succeeded.
        try:
            assert self.process is not None and self.process.stdin is not None
            self.process.stdin.write((command + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            return {"ok": False, "error": f"No se pudo escribir el comando: {exc}"}
        return {"ok": True, "submitted": True}

    def graceful_stop(self, grace_seconds: float = 50.0) -> None:
        if not self.is_running():
            return
        stop_cmd = self.adapter.stop_command()
        if stop_cmd:
            log(f"Enviando comando de apagado limpio: {stop_cmd}")
            self.send_command(stop_cmd)

        deadline = time.monotonic() + grace_seconds
        while self.is_running() and time.monotonic() < deadline:
            time.sleep(0.5)

        if self.is_running():
            log("El proceso no se detuvo a tiempo, forzando terminacion")
            assert self.process is not None
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def run_installer_jar(
        self, jar_name: str, minecraft_version: str, heap_mb: int, args: list[str], chmod_executable: str | None = None
    ) -> dict:
        """Runs a Forge/NeoForge/BuildTools installer jar as a subprocess of
        THIS container, not the dashboard's - so it gets the instance's own
        game plan memory budget (GAME_MEMORY_LIMIT/GAME_MEMORY_RESERVATION)
        instead of the dashboard's much smaller, fixed DASHBOARD_MEMORY_LIMIT
        (a plain -Xmx alone can't fix that: a cgroup memory limit caps the
        whole container regardless of what heap size the JVM inside it asks
        for). The caller (dashboard/app/services/installer.py, over the
        /lifecycle/install route below) already downloaded and checksum-
        verified the jar into the install/ bind mount both containers share -
        this only ever reads it back from there, never over the network.

        `chmod_executable`, if given, is a filename relative to SERVER_DIR
        (e.g. "run.sh") to chmod 0o755 once the subprocess finishes - this
        agent's own uid owns whatever the installer just wrote there, while
        the dashboard's uid does not (no uid is shared between the two
        containers), so only this side can actually do that chmod."""
        if not jar_name or jar_name in (".", "..") or "/" in jar_name or "\\" in jar_name:
            return {"ok": False, "error": "jar_name invalido"}
        jar_path = INSTALL_DIR / jar_name
        if not jar_path.is_file():
            return {"ok": False, "error": f"'{jar_name}' no existe en {INSTALL_DIR}"}
        if chmod_executable is not None and (
            not chmod_executable or chmod_executable in (".", "..") or "/" in chmod_executable or "\\" in chmod_executable
        ):
            return {"ok": False, "error": "chmod_executable invalido"}

        java_version = (self.env.get("JAVA_VERSION") or "auto").lower()
        try:
            resolved, _warning = runtime_matrix.resolve(minecraft_version, java_version)
        except runtime_matrix.UnsupportedJavaError as exc:
            return {"ok": False, "error": str(exc)}
        java_bin = f"/opt/java/{resolved}/bin/java"

        command = [java_bin, f"-Xmx{heap_mb}m", "-jar", str(jar_path), *args]
        log(f"Ejecutando instalador: {' '.join(command)}")
        try:
            completed = subprocess.run(
                command, cwd=SERVER_DIR, capture_output=True, text=True, timeout=_INSTALLER_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"No se pudo ejecutar el instalador: {exc}"}

        if chmod_executable:
            target = SERVER_DIR / chmod_executable
            if target.is_file():
                try:
                    target.chmod(0o755)
                except OSError as exc:
                    log(f"advertencia: no se pudo chmod +x '{chmod_executable}': {exc}")

        output = (completed.stderr or completed.stdout or "").strip()
        return {"ok": True, "returncode": completed.returncode, "output": output[-_MAX_INSTALLER_OUTPUT:]}

    def check_rate_limit(self) -> bool:
        now = time.monotonic()
        with self._rate_lock:
            self._rate_hits = [t for t in self._rate_hits if now - t < _RATE_LIMIT_WINDOW]
            if len(self._rate_hits) >= _RATE_LIMIT_MAX:
                return False
            self._rate_hits.append(now)
            return True


def _make_handler(supervisor: Supervisor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "UrraHostingControlAgent/1.0"

        def log_message(self, fmt, *args):
            log("http: " + (fmt % args))

        def _unauthorized(self) -> bool:
            token = self.headers.get("X-Control-Token", "")
            if not token or token != supervisor.config.game_control_token:
                self.send_response(401)
                self.end_headers()
                return True
            return False

        def _write_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._write_json(200, {"status": "ok", "running": supervisor.is_running()})
                return
            self._write_json(404, {"error": "no encontrado"})

        def do_POST(self):
            if self.path == "/command":
                self._handle_command()
            elif self.path == "/lifecycle/stop":
                self._handle_lifecycle_stop()
            elif self.path == "/lifecycle/install":
                self._handle_lifecycle_install()
            else:
                self._write_json(404, {"error": "no encontrado"})

        def _handle_command(self) -> None:
            if self._unauthorized():
                return
            if not supervisor.check_rate_limit():
                self._write_json(429, {"error": "demasiadas solicitudes, espera unos segundos"})
                return

            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 8192:
                self._write_json(400, {"error": "cuerpo invalido"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._write_json(400, {"error": "JSON invalido"})
                return

            command = str(payload.get("command", "")).strip()
            if not command or len(command) > MAX_COMMAND_LENGTH:
                self._write_json(400, {"error": "comando vacio o demasiado largo"})
                return

            result = supervisor.send_command(command)
            self._write_json(200 if result.get("ok") else 502, result)

        def _handle_lifecycle_stop(self) -> None:
            # Stops only the child game process - the agent/container stay
            # up, unlike a `docker stop` of the whole container (see
            # Supervisor's module docstring: the main loop exits once the
            # child exits, which a full container stop also triggers via
            # SIGTERM). Used by the dashboard before a Forge/NeoForge/
            # BuildTools install so it can then call /lifecycle/install
            # against this same, still-alive agent.
            if self._unauthorized():
                return
            supervisor.graceful_stop()
            self._write_json(200, {"ok": True, "running": supervisor.is_running()})

        def _handle_lifecycle_install(self) -> None:
            if self._unauthorized():
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 4096:
                self._write_json(400, {"error": "cuerpo invalido"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._write_json(400, {"error": "JSON invalido"})
                return

            jar_name = str(payload.get("jar_name", ""))
            minecraft_version = str(payload.get("minecraft_version", ""))
            heap_mb = payload.get("heap_mb")
            args = payload.get("args") or []
            chmod_executable = payload.get("chmod_executable")
            valid_args = isinstance(args, list) and all(isinstance(item, str) for item in args)
            valid_chmod = chmod_executable is None or isinstance(chmod_executable, str)
            if not minecraft_version or not isinstance(heap_mb, int) or heap_mb <= 0 or not valid_args or not valid_chmod:
                self._write_json(400, {"error": "parametros invalidos"})
                return

            result = supervisor.run_installer_jar(jar_name, minecraft_version, heap_mb, args, chmod_executable)
            self._write_json(200 if result.get("ok") else 502, result)

    return Handler


def main() -> int:
    supervisor = Supervisor()
    supervisor.start()

    httpd = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), _make_handler(supervisor))
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    log(f"Agente de control escuchando en el puerto interno {CONTROL_PORT}")

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        log(f"Senal {signum} recibida, iniciando apagado limpio...")
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stop_event.is_set():
        if supervisor.process is not None and supervisor.process.poll() is not None:
            log(f"El proceso del juego finalizo con codigo {supervisor.process.returncode}")
            break
        stop_event.wait(timeout=1.0)

    supervisor.graceful_stop()
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
