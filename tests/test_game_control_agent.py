"""Unit tests for game_control_agent.Supervisor's command dispatch and rate
limiting. `start()` (which actually spawns the game server subprocess) is
never called here - these tests build a Supervisor and swap in a fake
`process` object, exactly the seam the real HTTP handler goes through."""

import os

import runtime.game_control_agent as gca
from tests.conftest import base_env


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.written = []
        self._poll_value = None

        class _Stdin:
            def __init__(self, outer):
                self._outer = outer

            def write(self, data):
                self._outer.written.append(data)

            def flush(self):
                pass

        self.stdin = _Stdin(self)

    def poll(self):
        return self._poll_value

    def terminate(self):
        self.terminated = True
        self._poll_value = 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _make_supervisor(monkeypatch, **env_overrides):
    env = base_env(**env_overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return gca.Supervisor()


def test_rcon_dispatch_calls_rcon_execute(monkeypatch):
    supervisor = _make_supervisor(monkeypatch)
    supervisor.process = _FakeProcess()

    monkeypatch.setattr(gca.rcon, "execute", lambda host, port, password, command, timeout: "pong")
    result = supervisor.send_command("list")
    assert result == {"ok": True, "output": "pong"}


def test_rcon_connection_refused_returns_controlled_error(monkeypatch):
    """Regression test: while the child process is alive but its RCON
    listener isn't accepting connections yet (e.g. mid mod-loading on a
    modded server), socket.create_connection raises a plain OSError
    (ConnectionRefusedError). Before this fix, send_command() only caught
    rcon.RconError, so this OSError escaped uncaught up through the HTTP
    handler and got dumped as a full traceback to stderr on every poll -
    flooding the Console tab's log stream."""
    supervisor = _make_supervisor(monkeypatch)
    supervisor.process = _FakeProcess()

    def _raise(host, port, password, command, timeout):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(gca.rcon, "execute", _raise)
    result = supervisor.send_command("list")
    assert result["ok"] is False
    assert "error" in result


def test_fifo_dispatch_writes_to_stdin(monkeypatch):
    supervisor = _make_supervisor(
        monkeypatch,
        GAME_FAMILY="terraria",
        GAME_EDITION="",
        GAME_SOFTWARE="vanilla",
        GAME_PORT="7777",
        RCON_PASSWORD="",
        DIFFICULTY="classic",
    )
    supervisor.process = _FakeProcess()

    result = supervisor.send_command("save")
    assert result == {"ok": True, "submitted": True}
    assert supervisor.process.written == [b"save\n"]


def test_start_with_nothing_installed_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    supervisor = _make_supervisor(monkeypatch)

    supervisor.start()  # no server.jar in tmp_path yet - must not raise

    assert supervisor.process is None
    assert supervisor.is_running() is False


def test_fix_permissions_grants_group_read_on_owner_only_files(monkeypatch, tmp_path):
    """Regression test for a production incident: Minecraft writes
    world/level.dat (and level.dat_old, via its atomic-save rename) with
    an explicit 0600 mode regardless of the process umask, which the
    dashboard's uid (only a supplementary `gamedata` group member, never
    the owner) can never read - even right after a clean stop, since a
    boot-time-only chmod pass can't catch a save that happens afterward.
    fix_permissions() must grant group-read on exactly those cases."""
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    supervisor = _make_supervisor(monkeypatch)

    world_dir = tmp_path / "world"
    world_dir.mkdir()
    level_dat = world_dir / "level.dat"
    level_dat.write_bytes(b"nbt-data")
    level_dat.chmod(0o600)

    supervisor.fix_permissions()

    if os.name != "nt":  # chmod bits aren't meaningful on Windows dev machines
        assert level_dat.stat().st_mode & 0o070 == 0o040
        assert world_dir.stat().st_mode & 0o070 == 0o050


def test_fix_permissions_skips_symlinks(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    supervisor = _make_supervisor(monkeypatch)

    real_file = tmp_path / "real.txt"
    real_file.write_text("x")
    real_file.chmod(0o600)
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real_file)
    except OSError:
        import pytest

        pytest.skip("creating symlinks requires elevated privileges on this machine")

    supervisor.fix_permissions()  # must not raise, and must not follow the symlink

    if os.name != "nt":
        assert real_file.stat().st_mode & 0o070 == 0o040


def test_send_command_when_not_running(monkeypatch):
    supervisor = _make_supervisor(monkeypatch)
    supervisor.process = None
    result = supervisor.send_command("list")
    assert result["ok"] is False


def test_rate_limit_blocks_after_threshold(monkeypatch):
    supervisor = _make_supervisor(monkeypatch)
    allowed = [supervisor.check_rate_limit() for _ in range(gca._RATE_LIMIT_MAX + 5)]
    assert allowed.count(True) == gca._RATE_LIMIT_MAX
    assert allowed.count(False) == 5


def test_graceful_stop_sends_stop_command_then_waits(monkeypatch):
    supervisor = _make_supervisor(monkeypatch)
    fake = _FakeProcess()
    supervisor.process = fake

    sent = []

    def fake_send_command(cmd):
        sent.append(cmd)
        fake._poll_value = 0  # simulate the process reacting to the stop command
        return {"ok": True}

    monkeypatch.setattr(supervisor, "send_command", fake_send_command)

    supervisor.graceful_stop(grace_seconds=5)
    assert sent == ["stop"]
    assert fake.terminated is False  # stopped cleanly, no need to force-terminate


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_installer_jar_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    supervisor = _make_supervisor(monkeypatch)

    result = supervisor.run_installer_jar("../evil.jar", "1.21.1", 512, [])
    assert result["ok"] is False


def test_run_installer_jar_missing_file_reports_error(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    supervisor = _make_supervisor(monkeypatch)

    result = supervisor.run_installer_jar("missing.jar", "1.21.1", 512, [])
    assert result["ok"] is False
    assert "missing.jar" in result["error"]


def test_run_installer_jar_resolves_java_by_minecraft_version_and_sets_heap(monkeypatch, tmp_path):
    """NeoForge 21.1.x targets Minecraft 1.21.1, which config/runtime_matrix.py
    maps to Java 21 - this is the exact fix for "El instalador no genero
    run.sh correctamente": the installer must run under a Java new enough
    for the target Minecraft version, with an explicit heap sized from
    GAME_MEMORY_RESERVATION (see installer.py's _installer_heap_mb)."""
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    (tmp_path / "neoforge-installer.jar").write_bytes(b"fake jar")

    captured = {}

    def fake_run(command, cwd, capture_output, text, timeout, check):
        captured["command"] = command
        captured["cwd"] = cwd
        return _FakeCompletedProcess(returncode=0, stdout="Installed successfully")

    monkeypatch.setattr(gca.subprocess, "run", fake_run)
    supervisor = _make_supervisor(monkeypatch)

    result = supervisor.run_installer_jar("neoforge-installer.jar", "1.21.1", 1536, ["--installServer"])

    assert result == {"ok": True, "returncode": 0, "output": "Installed successfully"}
    assert captured["command"][0] == "/opt/java/21/bin/java"
    assert captured["command"][1] == "-Xmx1536m"
    assert captured["command"][-1] == "--installServer"
    assert captured["cwd"] == tmp_path


def test_run_installer_jar_reports_subprocess_launch_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    (tmp_path / "installer.jar").write_bytes(b"fake jar")

    def fake_run(*args, **kwargs):
        raise OSError("java: not found")

    monkeypatch.setattr(gca.subprocess, "run", fake_run)
    supervisor = _make_supervisor(monkeypatch)

    result = supervisor.run_installer_jar("installer.jar", "1.21.1", 512, [])
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_run_installer_jar_chmods_the_requested_output_after_success(monkeypatch, tmp_path):
    """The agent's own uid owns whatever the installer just wrote under
    SERVER_DIR - the dashboard's uid does not (no uid is shared between the
    two containers) - so chmod +x on e.g. run.sh must happen here, not on
    the dashboard side (that used to fail with "[Errno 1] Operation not
    permitted")."""
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    (tmp_path / "installer.jar").write_bytes(b"fake jar")
    run_sh = tmp_path / "run.sh"
    run_sh.write_text("#!/usr/bin/env sh\n")
    run_sh.chmod(0o644)

    monkeypatch.setattr(gca.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=0, stdout="ok"))
    supervisor = _make_supervisor(monkeypatch)

    result = supervisor.run_installer_jar("installer.jar", "1.21.1", 512, ["--installServer"], chmod_executable="run.sh")

    assert result["ok"] is True
    if os.name != "nt":  # chmod bits aren't meaningful on Windows dev machines
        assert run_sh.stat().st_mode & 0o777 == 0o755


def test_run_installer_jar_rejects_chmod_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(gca, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(gca, "SERVER_DIR", tmp_path)
    (tmp_path / "installer.jar").write_bytes(b"fake jar")

    supervisor = _make_supervisor(monkeypatch)
    result = supervisor.run_installer_jar("installer.jar", "1.21.1", 512, [], chmod_executable="../evil.sh")
    assert result["ok"] is False
