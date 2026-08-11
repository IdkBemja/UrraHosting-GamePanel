"""Path-safe file management scoped to the mounted DATA_DIR subdirectories.
Categories are dynamic per adapter (plan.md section 3: Bedrock has no
`plugins`, vanilla Terraria has no `mods`, tModLoader adds `mods`/
`modconfigs`/`worlds`/`players`) - see `GameRuntimeAdapter.file_categories`.
Every operation resolves the final path and verifies it never escapes its
category root, refuses to follow or create symlinks, and enforces a logical
(application-level) storage quota before writes. Direct port of
UrraHosting-MinecraftServer's dashboard/app/services/storage.py.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# Module-level (not per-InstanceStorage-instance) on purpose: the dashboard
# rebuilds InstanceStorage on every request (see main.py's before_request
# hook, needed so a reprovisioned instance's categories update without a
# restart), so an instance attribute would never survive between requests.
#
# Walking every file to sum sizes is expensive for software that ships
# thousands of small files (Bedrock, tModLoader ship several thousand) -
# on some bind-mount backends (observed on Docker Desktop's Windows file
# sharing) a single such walk can take well over 30 seconds on its own, far
# past what an HTTP request should ever block on. A short TTL alone still
# means roughly one request in a few hits that slow path directly. Instead,
# `usage_bytes()` NEVER walks the filesystem inline: it returns the last
# known value immediately (0 the very first time) and kicks off a
# background thread to refresh it if the cache is missing/stale, so no
# request is ever slower than a dict lookup. This makes both `/api/overview`
# and `check_quota()`'s enforcement eventually-consistent rather than
# real-time - acceptable since this is a logical/soft quota to begin with
# (plan.md: bind mounts have no portable hard quota mechanism anyway).
_USAGE_CACHE_TTL_SECONDS = 20.0
_usage_cache: dict[Path, tuple[float, int]] = {}
_usage_refreshing: set[Path] = set()
_usage_lock = threading.Lock()


def _compute_usage(root: Path) -> int:
    total = 0
    if root.exists():
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                file_path = Path(dirpath) / name
                if file_path.is_symlink():
                    continue
                try:
                    total += file_path.stat().st_size
                except OSError:
                    continue
    return total


def _trigger_usage_refresh(root: Path) -> None:
    with _usage_lock:
        if root in _usage_refreshing:
            return  # already being recomputed by another thread
        _usage_refreshing.add(root)

    def worker() -> None:
        try:
            total = _compute_usage(root)
            _usage_cache[root] = (time.monotonic(), total)
        finally:
            with _usage_lock:
                _usage_refreshing.discard(root)

    threading.Thread(target=worker, daemon=True, name=f"usage-refresh-{root.name}").start()


class StorageError(ValueError):
    pass


class PathTraversalError(StorageError):
    pass


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_at: float


def _clear_directory_contents(root: Path) -> list[str]:
    """Deletes everything INSIDE `root` but never `root` itself - a bind
    mount's top-level directory (the mount point Docker creates for a
    `${DATA_DIR}/x:/data/x` volume entry) refuses `rmdir` from inside the
    container even when every file/subdirectory under it is perfectly
    writable (observed as `OSError: [Errno 30] Read-only file system` on
    Docker Desktop's Windows file sharing backend), while clearing its
    contents one entry at a time works fine. `shutil.rmtree(root)` followed
    by recreating `root` - the more obvious way to "empty a directory" -
    hits exactly that failure, since it tries to remove `root` too.

    Each top-level entry is removed independently and a failure on one
    (returned as a warning string) never blocks the rest: some leftover
    entries are owned by a DIFFERENT container's uid entirely (observed live
    - the game process itself can write into a mounted category directory
    at runtime), and `_chmod_and_retry` can only fix an overly-strict MODE,
    never a mismatched OWNER (chmod itself requires being the owner or
    root). Clearing what it can and warning about the rest beats aborting
    the whole (re)install over one stale entry."""
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
    """`shutil.rmtree(..., onexc=...)` handler - a previous install
    (possibly by a different container user) can leave files this process
    can't unlink outright. Try granting owner rwx once and retrying before
    giving up; a harmless no-op when the real cause is something else (the
    original exception propagates). Same helper as
    dashboard/app/services/installer.py's, duplicated rather than shared
    across modules for one six-line function."""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        raise exc from None


def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename.replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise StorageError("Nombre de archivo invalido")
    return name


class InstanceStorage:
    def __init__(self, roots: Mapping[str, Path], max_upload_bytes: int, quota_bytes: int | None = None):
        self._roots = {name: Path(root).resolve() for name, root in roots.items()}
        self.max_upload_bytes = max_upload_bytes
        self.quota_bytes = quota_bytes

    @property
    def categories(self) -> list[str]:
        return list(self._roots)

    def _root(self, category: str) -> Path:
        root = self._roots.get(category)
        if root is None:
            raise StorageError(f"Categoria de almacenamiento desconocida: {category}")
        return root

    def resolve(self, category: str, relative_path: str) -> Path:
        root = self._root(category)
        cleaned = (relative_path or "").replace("\\", "/").lstrip("/")
        candidate = (root / cleaned).resolve()
        if candidate != root and root not in candidate.parents:
            raise PathTraversalError(f"Ruta fuera de '{category}': {relative_path}")
        return candidate

    def shadowed_categories(self, category: str, relative_path: str = "") -> list[str]:
        """Category names that would collide, by name only, with a plain
        subdirectory at this exact listing - e.g. browsing "game" at its
        root and finding an entry named "mods" that is NOT the real "mods"
        category.

        mods/plugins/resourcepacks are separate bind mounts OVERLAID onto
        these exact subpaths of game/ for game-runtime (compose.yml:
        `${DATA_DIR}/mods:/data/game/mods`) - but the dashboard has no such
        overlay, only its own separate top-level mounts for those same
        categories. Browsing "game" at its root would otherwise show
        whatever plain, uncovered directory happens to physically exist on
        disk at e.g. game/mods (often root-owned cruft nobody ever
        explicitly created or manages, or a stale leftover) - a DIFFERENT,
        disconnected directory from the real "mods" category the running
        server actually reads from. Uploading there looks like it worked (or
        fails with a confusing permission error) either way, and the server
        never sees those files. list_dir() hides these entries outright;
        the Files tab uses this method's result to offer a "ir a la
        categoria real" shortcut instead of just leaving the admin to
        wonder where mods/plugins actually went.

        Comparing actual root PATHS (not just names) is what keeps this
        correct for worlds/modconfigs/players (Terraria/tModLoader): those
        categories genuinely resolve to game/worlds etc - the SAME
        directory, just reachable two ways - so they're never shadowed,
        unlike mods/plugins/resourcepacks which resolve somewhere else
        entirely. Only meaningful at a category's own root - a legitimately
        nested "mods" folder two levels down is unrelated to this.

        Only "game" is checked at all: it is the only category any OTHER
        category's mount gets overlaid onto a subpath of, in this
        deployment's compose.yml - comparing every category against every
        other would flag unrelated top-level directories (e.g. "backups" vs
        "mods") as colliding just because their roots differ, which they
        trivially always do. Also only returns names that actually have a
        same-named directory sitting there right now - a fresh instance
        with nothing under game/ yet has nothing to jump away from, and
        listing every structurally-possible collision regardless of whether
        anything is actually there would clutter the shortcut row with
        categories that were never a trap to begin with."""
        if category != "game":
            return []
        root = self._root(category)
        if self.resolve(category, relative_path) != root:
            return []
        candidates = (name for name, other_root in self._roots.items() if name != category and other_root != root / name)
        return sorted(name for name in candidates if (root / name).is_dir())

    def list_dir(self, category: str, relative_path: str = "") -> list[FileEntry]:
        target = self.resolve(category, relative_path)
        root = self._root(category)
        if not target.exists():
            return []
        if not target.is_dir():
            raise StorageError(f"'{relative_path}' no es un directorio")

        hidden_names = frozenset(self.shadowed_categories(category, relative_path))

        entries: list[FileEntry] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in hidden_names:
                continue
            if child.is_symlink():
                continue
            try:
                stat_result = child.stat()
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=child.name,
                    path=child.relative_to(root).as_posix(),
                    is_dir=child.is_dir(),
                    size=stat_result.st_size if child.is_file() else 0,
                    modified_at=stat_result.st_mtime,
                )
            )
        return entries

    def usage_bytes(self, category: str | None = None) -> int:
        """Never walks the filesystem inline (see the module docstring
        above `_usage_cache`) - returns the last known total immediately (0
        before it has ever been computed) and triggers a background
        refresh whenever the cached value is missing or stale."""
        categories = [category] if category else self.categories
        total = 0
        now = time.monotonic()
        for cat in categories:
            root = self._root(cat)
            cached = _usage_cache.get(root)
            if cached is None or now - cached[0] >= _USAGE_CACHE_TTL_SECONDS:
                _trigger_usage_refresh(root)
            if cached is not None:
                total += cached[1]
        return total

    def check_quota(self, additional_bytes: int) -> None:
        if self.quota_bytes is None:
            return
        if self.usage_bytes() + additional_bytes > self.quota_bytes:
            raise StorageError("Cuota de almacenamiento de la instancia excedida")

    def save_upload(
        self,
        category: str,
        relative_dir: str,
        filename: str,
        stream: BinaryIO,
        content_length: int | None,
        *,
        overwrite: bool = False,
    ) -> Path:
        if content_length is not None and content_length > self.max_upload_bytes:
            raise StorageError("El archivo excede el tamano maximo permitido (MAX_UPLOAD_MB)")

        safe_name = sanitize_filename(filename)
        target_dir = self.resolve(category, relative_dir)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Most commonly a permission mismatch on the bind mount (see
            # plan.md section 8: dashboard and game-runtime are different
            # uids with no shared ownership guarantee on every backend) or a
            # full disk - either way this must come back as a clean,
            # specific StorageError, not an unhandled OSError that would
            # otherwise 500 and show the frontend's generic upload-failed
            # fallback with no real cause visible.
            raise StorageError(f"No se pudo preparar la carpeta de destino: {exc}") from exc
        target = target_dir / safe_name

        if target.is_symlink():
            raise StorageError("No se permite escribir sobre un symlink")
        if target.exists() and not overwrite:
            raise StorageError(f"'{safe_name}' ya existe (confirma sobrescritura)")

        if content_length is not None:
            self.check_quota(content_length)

        tmp_path = target.with_name(f".{safe_name}.part")
        written = 0
        try:
            with open(tmp_path, "wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise StorageError("El archivo excede el tamano maximo permitido durante la subida")
                    handle.write(chunk)
            if content_length is None:
                self.check_quota(written)
            os.replace(tmp_path, target)
        except StorageError:
            tmp_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise StorageError(f"No se pudo guardar el archivo: {exc}") from exc
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    def delete(self, category: str, relative_path: str) -> None:
        target = self.resolve(category, relative_path)
        if target.is_symlink():
            raise StorageError("No se permite operar sobre symlinks")
        if not target.exists():
            raise StorageError("El elemento no existe")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def clear_category(self, category: str) -> list[str]:
        """Wipes a whole category root for a clean software (re)install
        (Software tab: a plugin/mod built for the OLD software is meaningless
        - or outright incompatible - with the new one, see
        dashboard/app/blueprints/catalog.py's /install). Only the root's
        CONTENTS are removed - see `_clear_directory_contents` for why the
        mount point itself is never touched, and why a failure on one entry
        (returned as a warning string) never blocks the rest."""
        return _clear_directory_contents(self._root(category))
