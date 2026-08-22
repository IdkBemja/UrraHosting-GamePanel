"""Create/list/restore backups of the game/ directory (plan.md sections 4.7
and 5). Archives are `.tar.gz`, built by walking `game_dir` ourselves (not
via `tarfile.add()`'s own recursion - see `_add_tree()`) and refusing to
follow symlinks (the same rule storage.py enforces for browsing), hashed
with SHA-256, and capped by a configurable retention count. Restoring is
refused while the instance is running (plan.md section 9: "No permitir
restaurar mientras el servidor esta activo") - the caller (blueprint) must
check `docker_client.status()` before calling `restore()`.

`create()` used to hand the whole tree to `tarfile.add()`, which aborts the
entire archive the moment ANY single file raises `PermissionError` (a real
production incident: one Minecraft world file the dashboard's unprivileged
user couldn't read turned "create a backup" into an unhandled 500 for the
whole instance, even though every other file was perfectly readable).
`_add_tree()` walks the tree itself so it can catch that per-file and skip
just the offending entry, recorded in `BackupInfo.skipped_files` /
`BackupProgress.skipped`, instead of losing the whole backup over one file.

The world file in that incident was denied specifically WHILE the game
process was autosaving it - a transient lock, not a permanent permission
problem - so `_add_tree()` also retries a file a few times (`_OPEN_RETRY_*`
below) before finally giving up on it, instead of skipping on the first
failure. `dashboard/app/blueprints/backups.py`'s create route pairs this
with `GameRuntimeAdapter.backup_pause_commands()` (Minecraft Java: `save-off`
around the copy) to avoid the race in the first place when the adapter
supports it; the retry here is what still protects every other game/edition
and whatever slips through anyway.

Backup creation can also run in a background thread (`start_create_async`)
so the caller (the `/api/backups/create` route) can return immediately and
let the frontend poll `get_progress()` for a real percentage instead of a
single generic "creating..." message - see `BackupProgress`.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import shutil
import tarfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

_DEFAULT_RETENTION = 10
_DEFAULT_AUTO_INTERVAL_HOURS = 24
# A file denied on the first try is retried a few times before being
# skipped for good - covers a game process holding/rewriting it mid-
# autosave for a moment, which is transient, not a real permission problem.
_OPEN_RETRY_ATTEMPTS = 5
_OPEN_RETRY_DELAY_SECONDS = 0.4


class BackupError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    id: str
    filename: str
    size: int
    sha256: str
    created_at: str
    actor: str
    skipped_files: list[str] = field(default_factory=list)


@dataclass
class BackupProgress:
    """Mutable, thread-safe-via-external-lock snapshot of the backup
    currently being created (if any). `status` is one of: idle, counting,
    running, done, error."""

    status: str = "idle"
    percent: int = 0
    processed_bytes: int = 0
    total_bytes: int = 0
    current_file: str = ""
    skipped: list[str] = field(default_factory=list)
    error: str | None = None
    backup: dict | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class BackupSettings:
    retention: int = _DEFAULT_RETENTION
    auto_enabled: bool = False
    auto_interval_hours: int = _DEFAULT_AUTO_INTERVAL_HOURS


OnDoneCallback = Callable[["BackupInfo | None", "str | None"], None]


class BackupService:
    def __init__(self, game_dir: Path, backups_dir: Path, retention: int = _DEFAULT_RETENTION):
        self._game_dir = Path(game_dir)
        self._backups_dir = Path(backups_dir)
        self._default_retention = retention
        self._settings_path = self._backups_dir / "settings.json"
        self._progress = BackupProgress()
        self._progress_lock = threading.Lock()
        self._busy_lock = threading.Lock()

    def list(self) -> list[BackupInfo]:
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        infos: list[BackupInfo] = []
        for meta_path in sorted(self._backups_dir.glob("*.json"), reverse=True):
            if meta_path.name == "settings.json":
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                infos.append(BackupInfo(**data))
            except (OSError, ValueError, TypeError):
                continue
        return infos

    def get_settings(self) -> BackupSettings:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return BackupSettings(
                retention=int(data.get("retention", self._default_retention)),
                auto_enabled=bool(data.get("auto_enabled", False)),
                auto_interval_hours=int(data.get("auto_interval_hours", _DEFAULT_AUTO_INTERVAL_HOURS)),
            )
        except (OSError, ValueError, TypeError):
            return BackupSettings(retention=self._default_retention)

    def update_settings(
        self,
        *,
        retention: int | None = None,
        auto_enabled: bool | None = None,
        auto_interval_hours: int | None = None,
    ) -> BackupSettings:
        settings = self.get_settings()
        if retention is not None:
            if not (1 <= retention <= 100):
                raise BackupError("La retencion debe estar entre 1 y 100 backups")
            settings.retention = retention
        if auto_enabled is not None:
            settings.auto_enabled = bool(auto_enabled)
        if auto_interval_hours is not None:
            if not (1 <= auto_interval_hours <= 720):
                raise BackupError("El intervalo de backups automaticos debe estar entre 1 y 720 horas")
            settings.auto_interval_hours = auto_interval_hours

        self._backups_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        return settings

    def get_progress(self) -> dict:
        with self._progress_lock:
            return asdict(self._progress)

    def is_busy(self) -> bool:
        return self._busy_lock.locked()

    def start_create_async(self, actor: str, on_done: OnDoneCallback | None = None) -> None:
        if not self._busy_lock.acquire(blocking=False):
            raise BackupError("Ya hay un backup en curso")
        thread = threading.Thread(target=self._run_create_async, args=(actor, on_done), daemon=True)
        thread.start()

    def _run_create_async(self, actor: str, on_done: OnDoneCallback | None) -> None:
        try:
            info = self.create(actor, progress=self._progress)
        except BackupError as exc:
            if on_done is not None:
                on_done(None, str(exc))
        except Exception as exc:  # noqa: BLE001 - background thread must never crash the process
            with self._progress_lock:
                self._progress.status = "error"
                self._progress.error = "Error interno al crear el backup"
                self._progress.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if on_done is not None:
                on_done(None, str(exc))
        else:
            if on_done is not None:
                on_done(info, None)
        finally:
            self._busy_lock.release()

    def maybe_run_scheduled(self, actor: str = "auto", on_done: OnDoneCallback | None = None) -> bool:
        """Called periodically (see main.py's background scheduler thread).
        Starts a backup if automatic backups are enabled and enough time has
        passed since the last one. Never blocks: skips this tick if a
        backup is already running."""
        settings = self.get_settings()
        if not settings.auto_enabled or self.is_busy():
            return False

        backups = self.list()
        if backups:
            try:
                last_epoch = calendar.timegm(time.strptime(backups[0].created_at, "%Y-%m-%dT%H:%M:%SZ"))
                elapsed_hours = (calendar.timegm(time.gmtime()) - last_epoch) / 3600.0
            except (ValueError, OverflowError):
                elapsed_hours = settings.auto_interval_hours
            if elapsed_hours < settings.auto_interval_hours:
                return False

        try:
            self.start_create_async(actor, on_done=on_done)
        except BackupError:
            return False
        return True

    def create(self, actor: str, progress: BackupProgress | None = None) -> BackupInfo:
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        if not self._game_dir.exists():
            if progress is not None:
                self._fail_progress(progress, "No hay datos de juego para respaldar")
            raise BackupError("No hay datos de juego para respaldar")

        if progress is not None:
            with self._progress_lock:
                progress.status = "counting"
                progress.percent = 0
                progress.processed_bytes = 0
                progress.total_bytes = 0
                progress.current_file = ""
                progress.skipped = []
                progress.error = None
                progress.backup = None
                progress.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                progress.finished_at = None
            total_bytes = _measure_tree(self._game_dir)
            with self._progress_lock:
                progress.total_bytes = total_bytes
                progress.status = "running"

        backup_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        filename = f"backup-{backup_id}.tar.gz"
        archive_path = self._backups_dir / filename
        tmp_path = archive_path.with_suffix(".tmp")

        skipped: list[str] = []
        try:
            with tarfile.open(tmp_path, "w:gz") as archive:
                added_any = _add_tree(self._game_dir, archive, "game", skipped, progress, self._progress_lock)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            if progress is not None:
                self._fail_progress(progress, str(exc))
            raise

        if not added_any:
            tmp_path.unlink(missing_ok=True)
            message = "No se pudo leer ningun archivo del directorio de juego (revisa los permisos)"
            if progress is not None:
                self._fail_progress(progress, message)
            raise BackupError(message)

        tmp_path.replace(archive_path)

        sha256 = _hash_file(archive_path)
        info = BackupInfo(
            id=backup_id,
            filename=filename,
            size=archive_path.stat().st_size,
            sha256=sha256,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            actor=actor,
            skipped_files=skipped,
        )
        (self._backups_dir / f"{filename}.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
        self._enforce_retention()

        if progress is not None:
            with self._progress_lock:
                progress.status = "done"
                progress.percent = 100
                progress.current_file = ""
                progress.backup = asdict(info)
                progress.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return info

    def _fail_progress(self, progress: BackupProgress, message: str) -> None:
        with self._progress_lock:
            progress.status = "error"
            progress.error = message
            progress.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def delete(self, backup_id: str) -> None:
        matches = [b for b in self.list() if b.id == backup_id]
        if not matches:
            raise BackupError("Backup no encontrado")
        info = matches[0]
        (self._backups_dir / info.filename).unlink(missing_ok=True)
        (self._backups_dir / f"{info.filename}.json").unlink(missing_ok=True)

    def restore(self, backup_id: str) -> None:
        matches = [b for b in self.list() if b.id == backup_id]
        if not matches:
            raise BackupError("Backup no encontrado")
        info = matches[0]
        archive_path = self._backups_dir / info.filename
        if not archive_path.exists():
            raise BackupError("El archivo de backup no existe en disco")
        try:
            digest_matches = _hash_file(archive_path) == info.sha256
        except OSError as exc:
            raise BackupError("No se pudo leer el archivo de backup") from exc
        if not digest_matches:
            raise BackupError("El hash del backup no coincide; se rechaza la restauracion")

        # Staged under _backups_dir - chowned to this container's uid in the
        # Dockerfile - rather than next to game_dir. game_dir's own PARENT
        # (DATA_DIR) is never chowned there, only game_dir's contents (via
        # game-runtime's entrypoint), so creating a new sibling directory
        # next to game_dir fails with a permission error the dashboard user
        # can't do anything about. This is the same constraint
        # installer.py's install/rollback flow stages around.
        staging = self._backups_dir / f".restore-staging-{int(time.time())}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError("No se pudo crear el directorio temporal de restauracion") from exc
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                _safe_extract_all(archive, staging)
        except (tarfile.TarError, OSError, BackupError) as exc:
            _rmtree_ignore(staging)
            raise BackupError("No se pudo extraer el backup") from exc

        restored_game = staging / "game"
        if not restored_game.exists():
            _rmtree_ignore(staging)
            raise BackupError("El backup no contiene un directorio 'game' valido")

        # game_dir itself is a bind-mount point: removing/recreating it (as
        # a plain rmtree+replace would) fails the same way installer.py's
        # _clear_directory_contents docstring describes, even though every
        # file/subdirectory under it is perfectly writable. Only its
        # contents are cleared, then the restored entries are moved in one
        # by one - same pattern as installer.py's rollback().
        try:
            _clear_directory_contents(self._game_dir)
            for entry in restored_game.iterdir():
                shutil.move(str(entry), str(self._game_dir / entry.name))
        except OSError as exc:
            _rmtree_ignore(staging)
            raise BackupError("No se pudo reemplazar los archivos del servidor con los del backup") from exc
        _rmtree_ignore(staging)

    def _enforce_retention(self) -> None:
        retention = self.get_settings().retention
        backups = self.list()
        for old in backups[retention:]:
            (self._backups_dir / old.filename).unlink(missing_ok=True)
            (self._backups_dir / f"{old.filename}.json").unlink(missing_ok=True)


def _measure_tree(base_dir: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(base_dir, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fname in files:
            fpath = os.path.join(root, fname)
            if os.path.islink(fpath):
                continue
            try:
                total += os.path.getsize(fpath)
            except OSError:
                pass
    return total


def _add_tree(
    base_dir: Path,
    archive: tarfile.TarFile,
    arcname: str,
    skipped: list[str],
    progress: BackupProgress | None,
    lock: threading.Lock,
) -> bool:
    """Walks `base_dir` and adds every file/dir to `archive` under `arcname`,
    tolerating per-file `PermissionError`/`OSError` (recorded in `skipped`
    and, if given, `progress.skipped`) instead of letting one unreadable
    file abort the whole archive. Returns True if at least one file was
    actually added."""
    added_any = False
    base_dir_str = str(base_dir)
    for root, dirs, files in os.walk(base_dir_str, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        rel_root = os.path.relpath(root, base_dir_str)
        arc_root = arcname if rel_root == "." else f"{arcname}/{rel_root.replace(os.sep, '/')}"
        try:
            archive.addfile(archive.gettarinfo(root, arcname=arc_root))
        except OSError:
            pass

        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            if os.path.islink(fpath):
                continue
            arcpath = f"{arc_root}/{fname}"

            last_exc: OSError | None = None
            for attempt in range(_OPEN_RETRY_ATTEMPTS):
                try:
                    tarinfo = archive.gettarinfo(fpath, arcname=arcpath)
                    with open(fpath, "rb") as handle:
                        archive.addfile(tarinfo, handle)
                    added_any = True
                    if progress is not None:
                        with lock:
                            progress.processed_bytes += max(tarinfo.size, 0)
                            progress.current_file = arcpath
                            if progress.total_bytes > 0:
                                progress.percent = min(99, int(progress.processed_bytes * 100 / progress.total_bytes))
                    last_exc = None
                    break
                except (PermissionError, OSError) as exc:
                    last_exc = exc
                    if attempt < _OPEN_RETRY_ATTEMPTS - 1:
                        time.sleep(_OPEN_RETRY_DELAY_SECONDS)

            if last_exc is not None:
                skipped.append(arcpath)
                if progress is not None:
                    with lock:
                        progress.skipped = list(skipped)
    return added_any


def _safe_extract_all(archive: tarfile.TarFile, dest_root: Path) -> None:
    dest_root = dest_root.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            continue
        target = (dest_root / member.name).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise BackupError(f"Entrada de backup fuera del destino: {member.name}")
    archive.extractall(dest_root)  # members already validated above (no symlinks/devices/traversal)


def _rmtree_ignore(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _clear_directory_contents(root: Path) -> list[str]:
    """Deletes everything INSIDE `root` but never `root` itself - a bind
    mount's top-level directory (the mount point Docker creates for a
    `${DATA_DIR}/x:/data/x` volume entry) refuses `rmdir` from inside the
    container even when every file/subdirectory under it is perfectly
    writable, while clearing its contents one entry at a time works fine.
    `shutil.rmtree(root)` followed by recreating `root` - what restore() used
    to do via `Path.replace()` - hits exactly that failure. Same helper as
    dashboard/app/services/installer.py's and storage.py's, duplicated
    rather than shared across modules for one short function."""
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return []
    warnings: list[str] = []
    for entry in root.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, onexc=_chmod_and_retry)
            else:
                entry.unlink()
        except OSError as exc:
            warnings.append(f"No se pudo borrar '{entry.name}': {exc}")
    return warnings


def _chmod_and_retry(func, path, exc) -> None:
    """`shutil.rmtree(..., onexc=...)` handler - a previous backup restore
    (or the game process itself) can leave files this process can't unlink
    outright. Try granting owner rwx once and retrying before giving up.
    Same helper as installer.py's/storage.py's, duplicated rather than
    shared across modules for one six-line function."""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        raise exc from None


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
