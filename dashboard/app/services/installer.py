"""Orchestrates catalog installs for every family/edition (plan.md sections
4.4 and 9). Generalizes UrraHosting-MinecraftServer's installer.py to four
install kinds:

  "jar"        - Vanilla/Paper/Purpur/Fabric: download straight to a temp
                 file inside game/ and os.replace into server.jar.
  "buildtools" - Spigot/CraftBukkit: run Spigot's official BuildTools.jar.
  "installer"  - Forge/NeoForge: run the official installer jar
                 (--installServer), expect a generated run.sh.
  "zip"        - Bedrock/Terraria/tModLoader: extract a full server
                 distribution.

Every (re)install is a CLEAN install: whatever is currently in game/ is
snapshotted first (so "Revertir" can still restore it) and then deleted
entirely before the new software is put in place - by request, replacing an
older design that tried to preserve worlds/config across a reinstall. The
Software tab warns the admin about this in red before confirming, and the
same wipe applies to every install kind, not just zip-kind reinstalls.

Installs are serialized with a process-local lock - the dashboard runs as a
single gunicorn worker (multiple threads), so this lock is meaningful.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from .archive_extract import ArchiveError, extract_archive
from .catalog import ALL_ALLOWED_HOSTS, CatalogError, CatalogService, DownloadInfo
from .catalog.base import request_allowlisted

_USER_AGENT = "UrraHosting-GamePanel/1.0 (+self-hosted)"
_DOWNLOAD_TIMEOUT = 30
_CHUNK_SIZE = 1024 * 1024
_BUILDTOOLS_JAVA = "/usr/bin/java"

_install_lock = threading.Lock()


class InstallError(RuntimeError):
    pass


class InstallProgress:
    """Thread-safe install-phase tracker polled by the frontend's progress
    modal (`GET /api/catalog/install/status`) while the install itself blocks
    a gunicorn worker thread for however long the clean/download/extract
    takes - several minutes observed for Bedrock/tModLoader's thousands of
    files. Gunicorn runs `--threads 8` (see dashboard/Dockerfile), so this
    polling GET is served by a different thread than the one blocked inside
    `Installer.install()`/the catalog blueprint - no request ever waits on
    another. One instance lives for the lifetime of the app (see
    `Installer.progress`), shared with the catalog blueprint so it can also
    report its own phases (stopping the server, writing config, restarting)
    around the install call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {"active": False, "phase": "idle", "message": "", "current": None, "total": None, "ok": None}

    def update(self, phase: str, message: str, current: int | None = None, total: int | None = None) -> None:
        with self._lock:
            self._state = {"active": True, "phase": phase, "message": message, "current": current, "total": total, "ok": None}

    def finish(self, ok: bool, message: str) -> None:
        with self._lock:
            self._state = {
                "active": False,
                "phase": "done" if ok else "error",
                "message": message,
                "current": None,
                "total": None,
                "ok": ok,
            }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


@dataclass
class InstallResult:
    game_software: str
    game_version: str
    channel: str
    url: str
    checksum: str | None
    installed_at: str
    actor: str
    result: str = "success"
    launch_mode: str = "jar"
    target_filename: str | None = None
    snapshot_path: str | None = None
    warnings: list[str] = field(default_factory=list)


class Installer:
    def __init__(self, catalog: CatalogService, game_dir: Path, install_dir: Path, max_bytes: int):
        self._catalog = catalog
        self._game_dir = Path(game_dir).resolve()
        self._install_dir = Path(install_dir).resolve()
        self._max_bytes = max_bytes
        self.progress = InstallProgress()

    def install(
        self,
        game_family: str,
        game_edition: str,
        game_software: str,
        version: str,
        channel: str,
        actor: str,
        create_backup: bool = True,
    ) -> InstallResult:
        if not _install_lock.acquire(blocking=False):
            raise InstallError("Ya hay una instalacion en curso para esta instancia, espera a que termine")
        try:
            return self._install_locked(game_family, game_edition, game_software, version, channel, actor, create_backup)
        finally:
            _install_lock.release()

    # -- dispatch -----------------------------------------------------------

    def _install_locked(
        self, game_family: str, game_edition: str, game_software: str, version: str, channel: str, actor: str, create_backup: bool
    ) -> InstallResult:
        self.progress.update("resolving", f"Buscando la descarga de {game_software} {version}...")
        download = self._catalog.get_download(game_family, game_edition, game_software, version, channel)
        if download.size and download.size > self._max_bytes:
            raise InstallError("La descarga supera el tamano maximo permitido (MAX_UPLOAD_MB)")

        self._game_dir.mkdir(parents=True, exist_ok=True)
        self._install_dir.mkdir(parents=True, exist_ok=True)

        snapshot_path, wipe_warnings = self._snapshot_and_clean_game_dir(create_backup)

        if download.install_kind == "jar":
            result = self._install_jar(download, game_software, version, channel)
        elif download.install_kind == "buildtools":
            result = self._install_buildtools(download, game_software, version, channel)
        elif download.install_kind == "installer":
            result = self._install_forge(download, game_software, version, channel)
        elif download.install_kind == "zip":
            result = self._install_zip(download, game_software, version, channel)
        else:
            raise InstallError(f"Metodo de instalacion desconocido: {download.install_kind}")

        result.actor = actor
        result.snapshot_path = str(snapshot_path) if snapshot_path else None
        result.warnings = wipe_warnings
        self._write_manifest(result)
        return result

    def _snapshot_and_clean_game_dir(self, create_backup: bool) -> tuple[Path | None, list[str]]:
        """Every (re)install is a clean install now: whatever is in game/
        gets wiped entirely before the new software is installed. When
        `create_backup` is true (the modal's default), the current contents
        are snapshotted first so "Revertir" can restore them - by request,
        this replaced the previous design that tried to preserve worlds/
        config across a reinstall, since the Software tab now warns the
        admin in red that existing data will be lost before they can
        confirm. When the admin unchecks the backup option, that copy is
        skipped entirely and the wipe runs directly - this is also what
        fixes installs that used to sit on "Creando copia de seguridad..."
        for a long time with no feedback: large world/ directories (many
        hundreds of MB to GBs of chunk files) made a silent, unprogressed
        copytree() look hung even though it was still working; both the
        skip option and the per-file progress below address that."""
        if not self._game_dir.exists() or not any(self._game_dir.iterdir()):
            return None, []

        snapshot_path = None
        if create_backup:
            snapshot_path = self._install_dir / "snapshots" / f"{int(time.time())}"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            total_files = _count_files(self._game_dir)
            copied = 0
            self.progress.update(
                "snapshotting",
                f"Creando copia de seguridad antes de borrar los datos actuales (0/{total_files} archivos)..."
                if total_files
                else "Creando copia de seguridad antes de borrar los datos actuales...",
                current=0,
                total=total_files or None,
            )

            def _copy_with_progress(src: str, dst: str, *, follow_symlinks: bool = True) -> None:
                nonlocal copied
                shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
                copied += 1
                self.progress.update(
                    "snapshotting",
                    f"Creando copia de seguridad antes de borrar los datos actuales ({copied}/{total_files} archivos)...",
                    current=copied,
                    total=total_files or None,
                )

            try:
                shutil.copytree(self._game_dir, snapshot_path, symlinks=False, copy_function=_copy_with_progress)
            except shutil.Error as exc:
                # copytree still copies everything it CAN and only raises this
                # (a list of per-file failures) at the very end - some files can
                # be genuinely unreadable by the dashboard's own uid (observed:
                # the running game process owns e.g. server.jar/world/level.dat
                # with a restrictive mode on some bind-mount backends, and the
                # dashboard is intentionally a different, lower-privilege user -
                # plan.md section 8: no shared uid between dashboard and
                # game-runtime). A snapshot missing a few such files is still a
                # far better outcome than aborting the entire reinstall over it;
                # Revertir simply won't be able to restore whichever specific
                # files errored.
                print(f"[installer] advertencia: la copia de seguridad quedo incompleta: {exc}", flush=True)
        else:
            self.progress.update("snapshotting", "Copia de seguridad omitida por el administrador, borrando datos existentes...")

        def _on_clean_progress(current: int, total: int) -> None:
            self.progress.update(
                "cleaning", f"Borrando los datos existentes para una instalacion limpia ({current}/{total})...", current=current, total=total
            )

        self.progress.update("cleaning", "Borrando los datos existentes para una instalacion limpia...")
        warnings = _clear_directory_contents(self._game_dir, progress_cb=_on_clean_progress)
        return snapshot_path, warnings

    # -- jar ------------------------------------------------------------

    def _install_jar(self, download: DownloadInfo, game_software: str, version: str, channel: str) -> InstallResult:
        target = self._game_dir / "server.jar"
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=self._game_dir, prefix=".install-", suffix=".jar")
        tmp_path = Path(tmp_path_str)
        try:
            checksum = self._download_and_hash(download, tmp_fd, tmp_path)
            self.progress.update("placing", "Colocando el nuevo server.jar...")
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url=download.url,
            checksum=checksum,
            installed_at=_now(),
            actor="",
            launch_mode="jar",
            target_filename="server.jar",
        )

    # -- buildtools (Spigot/CraftBukkit) --------------------------------

    def _install_buildtools(self, download: DownloadInfo, game_software: str, version: str, channel: str) -> InstallResult:
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=self._install_dir, prefix=".buildtools-", suffix=".jar")
        tmp_path = Path(tmp_path_str)
        try:
            self._download_and_hash(download, tmp_fd, tmp_path)
            command = [
                _BUILDTOOLS_JAVA,
                "-jar",
                str(tmp_path),
                "--rev",
                version,
                "--compile",
                "spigot" if game_software == "spigot" else "craftbukkit",
            ]
            self.progress.update("compiling", f"Compilando {game_software} con BuildTools (puede tardar varios minutos)...")
            try:
                completed = subprocess.run(command, cwd=self._install_dir, capture_output=True, text=True, timeout=1800, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise InstallError(f"BuildTools no pudo ejecutarse: {exc}") from exc
            if completed.returncode != 0:
                raise InstallError("BuildTools fallo al compilar. Revisa que la version exista y los logs del dashboard.")
            pattern = "spigot-*.jar" if game_software == "spigot" else "craftbukkit-*.jar"
            outputs = sorted(self._install_dir.glob(pattern), key=lambda item: item.stat().st_mtime)
            if not outputs:
                raise InstallError("BuildTools termino pero no genero el jar esperado")
            target = self._game_dir / "server.jar"
            self.progress.update("placing", "Colocando el jar compilado...")
            shutil.copy2(outputs[-1], target)
        finally:
            tmp_path.unlink(missing_ok=True)

        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url=download.url,
            checksum=None,
            installed_at=_now(),
            actor="",
            launch_mode="jar",
            target_filename="server.jar",
        )

    # -- forge/neoforge installer ---------------------------------------

    def _install_forge(self, download: DownloadInfo, game_software: str, version: str, channel: str) -> InstallResult:
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=self._install_dir, prefix=".installer-", suffix=".jar")
        tmp_path = Path(tmp_path_str)
        try:
            checksum = self._download_and_hash(download, tmp_fd, tmp_path)
            self.progress.update("running_installer", "Ejecutando el instalador oficial...")
            try:
                completed = subprocess.run(
                    [_BUILDTOOLS_JAVA, "-jar", str(tmp_path), "--installServer"],
                    cwd=self._game_dir,
                    capture_output=True,
                    text=True,
                    timeout=900,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise InstallError(f"El instalador no pudo ejecutarse: {exc}") from exc
            if completed.returncode != 0 or not (self._game_dir / "run.sh").exists():
                raise InstallError("El instalador no genero run.sh correctamente")
            (self._game_dir / "run.sh").chmod(0o755)
        finally:
            tmp_path.unlink(missing_ok=True)

        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url=download.url,
            checksum=checksum,
            installed_at=_now(),
            actor="",
            launch_mode="script",
            target_filename=None,
        )

    # -- zip (Bedrock/Terraria/tModLoader) -------------------------------

    def _install_zip(self, download: DownloadInfo, game_software: str, version: str, channel: str) -> InstallResult:
        # game_dir is always empty here - _snapshot_and_clean_game_dir()
        # already wiped it. Staged INSIDE game_dir's own mount (not
        # install_dir's - a separate bind mount) on purpose: Bedrock/
        # tModLoader ship thousands of small files, and the staging ->
        # game_dir move only stays a fast, atomic os.rename() per top-level
        # entry when both sides are the same filesystem. Across mounts,
        # shutil.move() silently falls back to a full recursive copy+delete
        # for every one of those files, which is dramatically slower - the
        # actual content is identical either way, only the move's cost
        # changes.
        staging = self._game_dir / f".install-staging-{int(time.time())}-{os.urandom(4).hex()}"
        staging.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=self._install_dir, prefix=".zip-", suffix=".zip")
        tmp_path = Path(tmp_path_str)
        try:
            checksum = self._download_and_hash(download, tmp_fd, tmp_path)
            self.progress.update("extracting", "Extrayendo archivos...")

            def _on_extract_progress(current: int, total: int) -> None:
                self.progress.update("extracting", f"Extrayendo archivos ({current}/{total})...", current=current, total=total)

            try:
                with open(tmp_path, "rb") as handle:
                    extract_archive(download.filename, handle, staging, self._max_bytes * 4, _on_extract_progress)
            except ArchiveError as exc:
                raise InstallError(f"No se pudo extraer el archivo: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        if download.expected_entrypoint and not (staging / download.expected_entrypoint).exists():
            shutil.rmtree(staging, ignore_errors=True)
            raise InstallError(f"El archivo descomprimido no contiene '{download.expected_entrypoint}'")

        self.progress.update("placing", "Colocando los archivos instalados...")
        for entry in staging.iterdir():
            shutil.move(str(entry), str(self._game_dir / entry.name))
        shutil.rmtree(staging, ignore_errors=True)

        if download.expected_entrypoint and not (self._game_dir / download.expected_entrypoint).exists():
            raise InstallError(f"La instalacion no dejo '{download.expected_entrypoint}' en su lugar")

        return InstallResult(
            game_software=game_software,
            game_version=version,
            channel=channel,
            url=download.url,
            checksum=checksum,
            installed_at=_now(),
            actor="",
            launch_mode="binary",
            target_filename=download.expected_entrypoint,
        )

    def rollback(self) -> dict:
        """Restore the most recent pre-install snapshot (plan.md section
        4.4/9: rollback when a post-install health check fails, or simply to
        undo a clean (re)install - every install kind snapshots game/ before
        wiping it, see `_snapshot_and_clean_game_dir()`)."""
        if not _install_lock.acquire(blocking=False):
            raise InstallError("Ya hay una instalacion en curso para esta instancia, espera a que termine")
        try:
            self.progress.update("rolling_back", "Restaurando la copia de seguridad anterior...")
            snapshots_dir = self._install_dir / "snapshots"
            snapshots = sorted(snapshots_dir.iterdir(), key=lambda p: p.name, reverse=True) if snapshots_dir.exists() else []
            if not snapshots:
                raise InstallError("No hay una instalacion anterior para restaurar")
            latest = snapshots[0]

            def _on_clean_progress(current: int, total: int) -> None:
                self.progress.update(
                    "rolling_back", f"Borrando datos actuales antes de restaurar ({current}/{total})...", current=current, total=total
                )

            # game_dir's mount point itself is never touched (see
            # `_clear_directory_contents`) - only its contents are cleared,
            # then the snapshot's entries are moved in one by one.
            clear_warnings = _clear_directory_contents(self._game_dir, progress_cb=_on_clean_progress)
            for entry in latest.iterdir():
                shutil.move(str(entry), str(self._game_dir / entry.name))
            shutil.rmtree(latest, ignore_errors=True)

            manifest_path = self._install_dir / "installation.json"
            data: dict = {}
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
            if clear_warnings:
                data["warnings"] = clear_warnings
            data["result"] = "rolled_back"
            manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
        finally:
            _install_lock.release()

    # -- shared helpers ---------------------------------------------------

    def _download_and_hash(self, download: DownloadInfo, tmp_fd: int, tmp_path: Path) -> str | None:
        hasher_sha256 = hashlib.sha256()
        hasher_sha1 = hashlib.sha1()
        hasher_md5 = hashlib.md5()
        written = 0

        self.progress.update("downloading", f"Descargando {download.filename}...", current=0, total=download.size or None)

        try:
            response = request_allowlisted(download.url, ALL_ALLOWED_HOSTS, stream=True)
        except CatalogError as exc:
            os.close(tmp_fd)
            raise InstallError(f"No se pudo descargar: {exc}") from exc

        try:
            with os.fdopen(tmp_fd, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self._max_bytes:
                        raise InstallError("La descarga supero el tamano maximo permitido")
                    handle.write(chunk)
                    hasher_sha256.update(chunk)
                    hasher_sha1.update(chunk)
                    hasher_md5.update(chunk)
                    self.progress.update("downloading", f"Descargando {download.filename}...", current=written, total=download.size or None)
        except requests.RequestException as exc:
            raise InstallError(f"Fallo de red durante la descarga: {exc}") from exc

        sha256, sha1, md5 = hasher_sha256.hexdigest(), hasher_sha1.hexdigest(), hasher_md5.hexdigest()
        if download.sha256 and download.sha256.lower() != sha256:
            raise InstallError("El checksum sha256 no coincide, descarga descartada")
        if download.sha1 and download.sha1.lower() != sha1:
            raise InstallError("El checksum sha1 no coincide, descarga descartada")
        if download.md5 and download.md5.lower() != md5:
            raise InstallError("El checksum md5 no coincide, descarga descartada")

        return download.sha256 or download.sha1 or download.md5 or sha256

    def _write_manifest(self, result: InstallResult) -> None:
        manifest_path = self._install_dir / "installation.json"
        manifest_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

    def read_manifest(self) -> dict | None:
        manifest_path = self._install_dir / "installation.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _count_files(root: Path) -> int:
    total = 0
    for _, _, files in os.walk(root):
        total += len(files)
    return total


def _clear_directory_contents(root: Path, progress_cb=None) -> list[str]:
    """Deletes everything INSIDE `root` but never `root` itself - a bind
    mount's top-level directory (the mount point Docker creates for a
    `${DATA_DIR}/x:/data/x` volume entry) refuses `rmdir` from inside the
    container even when every file/subdirectory under it is perfectly
    writable (observed as `OSError: [Errno 30] Read-only file system` on
    Docker Desktop's Windows file sharing backend), while clearing its
    contents one entry at a time works fine. `shutil.rmtree(root)` followed
    by recreating `root` - the more obvious way to "empty a directory" - hits
    exactly that failure, since it tries to remove `root` too.

    Each top-level entry is removed independently and a failure on one
    (returned as a warning string) never blocks the rest: some leftover
    directories are owned by a DIFFERENT container's uid entirely (observed
    live - Bedrock's own server process writes versioned `behavior_packs/
    vanilla_X.Y.Z` copies as game-runtime's uid, not the dashboard's, and
    `_chmod_and_retry` can only fix an overly-strict MODE, never a mismatched
    OWNER - chmod itself requires being the owner or root). A clean install
    that leaves one truly undeletable stale directory behind (with a
    warning) is a far better outcome than the whole reinstall failing over
    it. Same helper as dashboard/app/services/storage.py's, duplicated
    rather than shared across modules for one short function.

    `progress_cb(current, total)`, if given, is called after each top-level
    entry is processed (whether it succeeded or produced a warning) so the
    install-progress modal can show real (current/total) numbers for this
    phase instead of sitting on an indeterminate spinner."""
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return []
    warnings: list[str] = []
    entries = list(root.iterdir())
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, onexc=_chmod_and_retry)
            else:
                entry.unlink()
        except OSError as exc:
            warnings.append(f"No se pudo borrar '{entry.name}': {exc}")
        if progress_cb:
            progress_cb(index, total)
    return warnings


def _chmod_and_retry(func, path, exc) -> None:
    """`shutil.rmtree(..., onexc=...)` handler (Python 3.12+: `exc` is the
    exception instance itself, not a `(type, value, tb)` tuple like the
    older `onerror` callback used). A previous install (possibly by a
    different container user, or interrupted mid-write) can leave files this
    process can't unlink outright. Try granting owner rwx once and retrying
    the failed operation before giving up - the standard workaround for
    permission-locked entries, and a harmless no-op when the real cause is
    something else (the retry just fails again and the original exception
    propagates to `_snapshot_and_clean_game_dir()`'s caller, aborting the
    install - a wipe that can't actually finish should fail loudly rather
    than silently leave stale data behind)."""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        raise exc from None


__all__ = ["CatalogError", "InstallError", "InstallProgress", "InstallResult", "Installer"]
