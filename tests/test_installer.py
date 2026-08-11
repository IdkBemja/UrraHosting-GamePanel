from pathlib import Path

import pytest
from app.services import installer as inst
from app.services.catalog import DownloadInfo
from app.services.docker_client import DockerControlError
from app.services.game_control import GameControlError


class _FakeStreamResponse:
    def __init__(self, content: bytes):
        self._content = content

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class _FakeCatalog:
    def __init__(self, download: DownloadInfo):
        self._download = download

    def get_download(self, game_family, game_edition, game_software, version, channel):
        return self._download


def _patch_download(monkeypatch, content: bytes):
    monkeypatch.setattr(inst, "request_allowlisted", lambda url, hosts, **kw: _FakeStreamResponse(content))


def test_jar_install_writes_server_jar_and_manifest(tmp_path, monkeypatch):
    content = b"fake jar bytes"
    _patch_download(monkeypatch, content)
    download = DownloadInfo(url="https://example.test/server.jar", filename="server.jar", install_kind="jar")
    catalog = _FakeCatalog(download)

    game_dir = tmp_path / "game"
    install_dir = tmp_path / "install"
    installer = inst.Installer(catalog, game_dir, install_dir, max_bytes=10_000)

    result = installer.install("minecraft", "java", "vanilla", "1.21.1", "stable", actor="tester")
    assert result.launch_mode == "jar"
    assert (game_dir / "server.jar").read_bytes() == content
    manifest = installer.read_manifest()
    assert manifest["game_software"] == "vanilla"


def test_jar_install_rejects_checksum_mismatch(tmp_path, monkeypatch):
    _patch_download(monkeypatch, b"actual content")
    download = DownloadInfo(url="https://example.test/server.jar", filename="server.jar", install_kind="jar", sha256="0" * 64)
    catalog = _FakeCatalog(download)

    installer = inst.Installer(catalog, tmp_path / "game", tmp_path / "install", max_bytes=10_000)
    with pytest.raises(inst.InstallError):
        installer.install("minecraft", "java", "vanilla", "1.21.1", "stable", actor="tester")


def test_jar_install_enforces_max_bytes(tmp_path, monkeypatch):
    _patch_download(monkeypatch, b"x" * 100)
    download = DownloadInfo(url="https://example.test/server.jar", filename="server.jar", install_kind="jar")
    catalog = _FakeCatalog(download)
    installer = inst.Installer(catalog, tmp_path / "game", tmp_path / "install", max_bytes=10)
    with pytest.raises(inst.InstallError):
        installer.install("minecraft", "java", "vanilla", "1.21.1", "stable", actor="tester")


def test_jar_reinstall_wipes_previous_data(tmp_path, monkeypatch):
    """Every (re)install is a clean install now (by request): a jar-kind
    reinstall must wipe whatever was in game/ before (an old world, stray
    files left by the previous software) - not just silently replace
    server.jar and leave everything else untouched like the old design did."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "server.jar").write_bytes(b"old jar")
    (game_dir / "world").mkdir()
    (game_dir / "world" / "level.dat").write_bytes(b"old world data")

    _patch_download(monkeypatch, b"new jar bytes")
    download = DownloadInfo(url="https://example.test/server.jar", filename="server.jar", install_kind="jar")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=10_000)

    result = installer.install("minecraft", "java", "vanilla", "1.21.1", "stable", actor="tester")

    assert (game_dir / "server.jar").read_bytes() == b"new jar bytes"
    assert not (game_dir / "world").exists()
    assert result.snapshot_path is not None
    assert Path(result.snapshot_path, "world", "level.dat").read_bytes() == b"old world data"


def test_zip_install_first_time_extracts_directly(tmp_path, monkeypatch):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "binary-content")
        zf.writestr("server.properties", "motd=hi\n")
    content = buf.getvalue()
    _patch_download(monkeypatch, content)

    download = DownloadInfo(
        url="https://example.test/bedrock.zip",
        filename="bedrock.zip",
        install_kind="zip",
        expected_entrypoint="bedrock_server",
    )
    catalog = _FakeCatalog(download)
    game_dir = tmp_path / "game"
    installer = inst.Installer(catalog, game_dir, tmp_path / "install", max_bytes=1_000_000)

    result = installer.install("minecraft", "bedrock", "bedrock", "1.21.50.7", "stable", actor="tester")
    assert result.launch_mode == "binary"
    assert result.snapshot_path is None  # nothing existed yet, so nothing to snapshot
    assert (game_dir / "bedrock_server").exists()


def test_zip_reinstall_wipes_existing_data(tmp_path, monkeypatch):
    """By request, a reinstall no longer preserves worlds/config across a
    zip-kind reinstall - it snapshots the previous game/ (so "Revertir" can
    still restore it) and then deletes it entirely for a clean install."""
    import io
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "worlds").mkdir()
    (game_dir / "worlds" / "world.wld").write_bytes(b"precious world data")
    (game_dir / "bedrock_server").write_bytes(b"old binary")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "new-binary-content")
    content = buf.getvalue()
    _patch_download(monkeypatch, content)

    download = DownloadInfo(
        url="https://example.test/bedrock.zip",
        filename="bedrock.zip",
        install_kind="zip",
        expected_entrypoint="bedrock_server",
    )
    catalog = _FakeCatalog(download)
    installer = inst.Installer(catalog, game_dir, tmp_path / "install", max_bytes=1_000_000)

    result = installer.install("minecraft", "bedrock", "bedrock", "1.21.60.1", "stable", actor="tester")

    assert not (game_dir / "worlds").exists()
    assert (game_dir / "bedrock_server").read_bytes() == b"new-binary-content"
    # The old data still exists in a snapshot - Revertir remains available.
    assert result.snapshot_path is not None
    assert Path(result.snapshot_path, "worlds", "world.wld").read_bytes() == b"precious world data"


def test_reinstall_never_rmtrees_game_dir_itself(tmp_path, monkeypatch):
    """game_dir is a bind mount's top-level directory (`${DATA_DIR}/game:
    /data/game`); some backends (observed on Docker Desktop's Windows file
    sharing) refuse to rmdir that exact directory from inside the container
    - `OSError: [Errno 30] Read-only file system` - even though every file/
    subdirectory under it is freely deletable. The clean-install wipe must
    only ever clear game_dir's CONTENTS, never attempt to remove game_dir
    itself."""
    import io
    import shutil
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "bedrock_server").write_bytes(b"old binary")
    (game_dir / "worlds").mkdir()
    (game_dir / "worlds" / "world.wld").write_bytes(b"old world")

    real_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        assert Path(path) != game_dir, "the clean-install wipe must never rmtree game_dir itself"
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "new-binary-content")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)

    installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")

    assert (game_dir / "bedrock_server").read_bytes() == b"new-binary-content"
    assert not (game_dir / "worlds").exists()


def test_zip_install_missing_entrypoint_rejected(tmp_path, monkeypatch):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no binary here")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    catalog = _FakeCatalog(download)
    installer = inst.Installer(catalog, tmp_path / "game", tmp_path / "install", max_bytes=1_000_000)
    with pytest.raises(inst.InstallError):
        installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")


def test_rollback_without_snapshot_raises(tmp_path):
    installer = inst.Installer(_FakeCatalog(None), tmp_path / "game", tmp_path / "install", max_bytes=1000)
    with pytest.raises(inst.InstallError):
        installer.rollback()


def test_rollback_restores_snapshot(tmp_path, monkeypatch):
    import io
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "worlds").mkdir()
    (game_dir / "worlds" / "world.wld").write_bytes(b"original world")
    (game_dir / "bedrock_server").write_bytes(b"original binary")
    (game_dir / "server.properties").write_text("motd=original\n")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "broken update")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)
    installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")
    assert (game_dir / "bedrock_server").read_bytes() == b"broken update"

    installer.rollback()
    assert (game_dir / "bedrock_server").read_bytes() == b"original binary"
    assert (game_dir / "worlds" / "world.wld").read_bytes() == b"original world"


def test_zip_install_reports_extraction_progress(tmp_path, monkeypatch):
    """The install progress modal (Software tab, "Instalar") polls
    `Installer.progress` while a zip-kind install blocks - extraction is the
    slow, thousands-of-files phase (observed multi-minute on some bind-mount
    backends), so it's the one phase that must report real current/total
    counts rather than just an indeterminate phase label."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "binary-content")
        zf.writestr("server.properties", "motd=hi\n")
        zf.writestr("worlds/README.txt", "placeholder\n")
    content = buf.getvalue()
    _patch_download(monkeypatch, content)

    download = DownloadInfo(
        url="https://example.test/bedrock.zip", filename="bedrock.zip", install_kind="zip", expected_entrypoint="bedrock_server"
    )
    catalog = _FakeCatalog(download)
    game_dir = tmp_path / "game"
    installer = inst.Installer(catalog, game_dir, tmp_path / "install", max_bytes=1_000_000)

    seen_extracting_phases = []
    real_update = installer.progress.update

    def spying_update(phase, message, current=None, total=None):
        if phase == "extracting":
            seen_extracting_phases.append((current, total))
        real_update(phase, message, current=current, total=total)

    monkeypatch.setattr(installer.progress, "update", spying_update)

    installer.install("minecraft", "bedrock", "bedrock", "1.21.50.7", "stable", actor="tester")

    # At least one progress callback with real current/total counts (not
    # just the initial indeterminate "extracting" call with no counts yet).
    assert any(current is not None and total == 3 for current, total in seen_extracting_phases)
    # Installer.install() never calls .finish() itself - only the catalog
    # blueprint does, once the whole flow (stop/install/write_override/
    # restart) completes - so the tracker is still "active" here.
    assert installer.progress.snapshot()["active"] is True


def test_install_progress_snapshot_is_idle_before_any_install(tmp_path):
    installer = inst.Installer(_FakeCatalog(None), tmp_path / "game", tmp_path / "install", max_bytes=1000)
    snapshot = installer.progress.snapshot()
    assert snapshot == {"active": False, "phase": "idle", "message": "", "current": None, "total": None, "ok": None}


def test_rollback_never_rmtrees_game_dir_itself(tmp_path, monkeypatch):
    """Same bind-mount constraint as the clean-install wipe (see
    `test_reinstall_never_rmtrees_game_dir_itself`) applies to rollback,
    which also used to `shutil.rmtree(game_dir)` wholesale before swapping
    the snapshot in."""
    import io
    import shutil
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "bedrock_server").write_bytes(b"original binary")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "broken update")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)
    installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")

    real_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        assert Path(path) != game_dir, "rollback must never rmtree game_dir itself"
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)

    installer.rollback()
    assert (game_dir / "bedrock_server").read_bytes() == b"original binary"


def test_snapshot_partial_failure_does_not_abort_install(tmp_path, monkeypatch):
    """Some files in game/ can be genuinely unreadable by the dashboard's own
    uid (observed live: the running game process owns server.jar/world/
    level.dat with a restrictive mode on some bind-mount backends, and the
    dashboard intentionally runs as a different, lower-privilege user).
    `shutil.copytree` still copies everything it CAN and only raises
    `shutil.Error` (a list of per-file failures) at the very end - that must
    produce a best-effort snapshot and a warning, never abort the reinstall."""
    import io
    import shutil
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "server.jar").write_bytes(b"old jar")
    (game_dir / "bedrock_server").write_bytes(b"old binary")

    real_copytree = shutil.copytree

    def failing_copytree(*args, **kwargs):
        real_copytree(*args, **kwargs)
        raise shutil.Error([("server.jar", "snapshot/server.jar", "[Errno 13] Permission denied")])

    monkeypatch.setattr(shutil, "copytree", failing_copytree)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "new-binary-content")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)

    result = installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")

    assert (game_dir / "bedrock_server").read_bytes() == b"new-binary-content"
    assert result.snapshot_path is not None


def test_clean_wipe_skips_permanently_undeletable_entry_with_a_warning(tmp_path, monkeypatch):
    """A leftover directory can be owned by a DIFFERENT container's uid
    entirely (observed live: Bedrock's own server process writes versioned
    `behavior_packs/vanilla_X.Y.Z` copies as game-runtime's uid, not the
    dashboard's) - `_chmod_and_retry` can only fix an overly-strict MODE,
    never a mismatched OWNER, so chmod itself also fails there. The clean
    wipe must skip that one entry (with a warning) and still finish the
    install, rather than aborting the whole reinstall over it."""
    import io
    import shutil
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "bedrock_server").write_bytes(b"old binary")
    stuck_dir = game_dir / "behavior_packs"
    stuck_dir.mkdir()
    (stuck_dir / "vanilla_1.21.130").mkdir()

    real_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        if Path(path) == stuck_dir:
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "new-binary-content")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)

    result = installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")

    assert (game_dir / "bedrock_server").read_bytes() == b"new-binary-content"
    assert stuck_dir.exists()  # left behind, but the install still succeeded
    assert result.warnings
    assert "behavior_packs" in result.warnings[0]


def test_clean_wipe_recovers_from_readonly_leftover_file(tmp_path, monkeypatch):
    """A leftover read-only file from a previous install (e.g. left behind
    by a different container user on some bind-mount backends) must not
    abort the clean wipe - `_chmod_and_retry` grants owner rwx and retries
    before giving up."""
    import io
    import zipfile

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "bedrock_server").write_bytes(b"old binary")
    stale_file = game_dir / "behavior_packs" / "some_file.json"
    stale_file.parent.mkdir()
    stale_file.write_text("{}")
    stale_file.chmod(0o400)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", "new-binary-content")
    _patch_download(monkeypatch, buf.getvalue())

    download = DownloadInfo(url="https://example.test/x.zip", filename="x.zip", install_kind="zip", expected_entrypoint="bedrock_server")
    installer = inst.Installer(_FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=1_000_000)

    result = installer.install("minecraft", "bedrock", "bedrock", "1.0.0", "stable", actor="tester")

    assert (game_dir / "bedrock_server").read_bytes() == b"new-binary-content"
    assert not (game_dir / "behavior_packs").exists()
    assert result.snapshot_path is not None


# -- Forge/NeoForge via the game-runtime control agent -----------------------


class _FakeDockerClient:
    def __init__(self, running: bool):
        self.running = running
        self.calls: list[str] = []

    def status(self):
        self.calls.append("status")
        return {"status": "running" if self.running else "exited", "running": self.running, "started_at": ""}

    def start(self):
        self.calls.append("start")
        self.running = True

    def stop(self, timeout=60):
        self.calls.append("stop")
        self.running = False

    def restart(self, timeout=60):
        self.calls.append("restart")
        self.running = True


class _FakeGameControl:
    def __init__(self, game_dir: Path, child_running: bool, run_result: dict | None = None):
        self.game_dir = game_dir
        self.child_running = child_running
        self.run_result = run_result
        self.calls: list[str] = []

    def health(self):
        self.calls.append("health")
        return {"status": "ok", "running": self.child_running}

    def stop_game(self):
        self.calls.append("stop_game")
        self.child_running = False
        return {"ok": True, "running": False}

    def run_installer(self, jar_name, minecraft_version, heap_mb, args, chmod_executable=None):
        self.calls.append("run_installer")
        self.last_call = {
            "jar_name": jar_name,
            "minecraft_version": minecraft_version,
            "heap_mb": heap_mb,
            "args": args,
            "chmod_executable": chmod_executable,
        }
        if self.run_result is not None:
            return self.run_result
        (self.game_dir / "run.sh").write_text("#!/usr/bin/env sh\njava @user_jvm_args.txt ...\n")
        return {"ok": True, "returncode": 0, "output": "Installed successfully"}


def test_forge_install_stops_child_and_runs_via_agent(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(
        url="https://maven.neoforged.net/x-installer.jar",
        filename="neoforge-installer.jar",
        install_kind="installer",
        minecraft_version="1.21.1",
    )
    docker_client = _FakeDockerClient(running=True)
    game_control = _FakeGameControl(game_dir, child_running=True)
    installer = inst.Installer(
        _FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=10_000_000, docker_client=docker_client, game_control=game_control
    )

    result = installer.install(
        "minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester", game_memory_reservation="1G"
    )

    assert result.launch_mode == "script"
    assert (game_dir / "run.sh").exists()
    # Was running -> agent's child gets stopped, container itself never does.
    assert "stop" not in docker_client.calls
    assert game_control.calls == ["health", "stop_game", "run_installer"]
    # chmod +x happens agent-side (its uid owns run.sh, the dashboard's does
    # not) - never a local chmod() on the dashboard side, which would fail
    # with EPERM against a file owned by the other container's uid.
    assert game_control.last_call["chmod_executable"] == "run.sh"
    assert game_control.last_call["minecraft_version"] == "1.21.1"
    # 0.75 * 1024MB (1G), matching the ratio runtime/adapters/minecraft_java.py
    # uses for the game server's own heap relative to GAME_MEMORY_LIMIT.
    assert game_control.last_call["heap_mb"] == int(1024 * 0.75)


def test_forge_install_starts_stopped_container_before_installing(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(url="https://x/installer.jar", filename="x.jar", install_kind="installer", minecraft_version="1.21.1")
    docker_client = _FakeDockerClient(running=False)
    game_control = _FakeGameControl(game_dir, child_running=False)
    installer = inst.Installer(
        _FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=10_000_000, docker_client=docker_client, game_control=game_control
    )

    installer.install("minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester")

    assert docker_client.calls == ["status", "start"]
    assert game_control.calls == ["health", "health", "run_installer"]  # one from the post-start wait, one from the running check


def test_forge_install_raises_when_run_sh_missing(tmp_path, monkeypatch):
    """Reproduces the reported bug's failure mode: the agent's installer run
    finished (ok=True) but never produced run.sh (e.g. a real-world
    OutOfMemoryError inside the installer's own post-processing) - this must
    surface as InstallError with the installer's captured output, not a
    silent 'success'."""
    game_dir = tmp_path / "game"
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(url="https://x/installer.jar", filename="x.jar", install_kind="installer", minecraft_version="1.21.1")
    docker_client = _FakeDockerClient(running=True)
    game_control = _FakeGameControl(
        game_dir,
        child_running=False,
        run_result={"ok": True, "returncode": 1, "output": "OutOfMemoryError: Java heap space"},
    )
    installer = inst.Installer(
        _FakeCatalog(download), game_dir, tmp_path / "install", max_bytes=10_000_000, docker_client=docker_client, game_control=game_control
    )

    with pytest.raises(inst.InstallError, match="OutOfMemoryError"):
        installer.install("minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester")


def test_forge_install_without_agent_deps_raises_clearly(tmp_path, monkeypatch):
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(url="https://x/installer.jar", filename="x.jar", install_kind="installer", minecraft_version="1.21.1")
    installer = inst.Installer(_FakeCatalog(download), tmp_path / "game", tmp_path / "install", max_bytes=10_000_000)

    with pytest.raises(inst.InstallError):
        installer.install("minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester")


def test_forge_install_surfaces_docker_control_error(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(url="https://x/installer.jar", filename="x.jar", install_kind="installer", minecraft_version="1.21.1")

    class _FailingDockerClient(_FakeDockerClient):
        def status(self):
            raise DockerControlError("docker-proxy no disponible")

    installer = inst.Installer(
        _FakeCatalog(download),
        game_dir,
        tmp_path / "install",
        max_bytes=10_000_000,
        docker_client=_FailingDockerClient(running=True),
        game_control=_FakeGameControl(game_dir, child_running=False),
    )

    with pytest.raises(inst.InstallError, match="docker-proxy no disponible"):
        installer.install("minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester")


def test_forge_install_surfaces_game_control_error(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    _patch_download(monkeypatch, b"fake installer jar bytes")
    download = DownloadInfo(url="https://x/installer.jar", filename="x.jar", install_kind="installer", minecraft_version="1.21.1")

    class _FailingGameControl(_FakeGameControl):
        def run_installer(self, jar_name, minecraft_version, heap_mb, args, chmod_executable=None):
            raise GameControlError("token invalido")

    installer = inst.Installer(
        _FakeCatalog(download),
        game_dir,
        tmp_path / "install",
        max_bytes=10_000_000,
        docker_client=_FakeDockerClient(running=True),
        game_control=_FailingGameControl(game_dir, child_running=False),
    )

    with pytest.raises(inst.InstallError, match="token invalido"):
        installer.install("minecraft", "java", "neoforge", "21.1.248", "stable", actor="tester")
