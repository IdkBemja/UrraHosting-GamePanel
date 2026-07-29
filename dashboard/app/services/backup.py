"""Create/list/restore backups of the game/ directory (plan.md sections 4.7
and 5). Archives are `.tar.gz`, built by walking `game_dir` and refusing to
follow symlinks (the same rule storage.py enforces for browsing), hashed
with SHA-256, and capped by a configurable retention count. Restoring is
refused while the instance is running (plan.md section 9: "No permitir
restaurar mientras el servidor esta activo") - the caller (blueprint) must
check `docker_client.status()` before calling `restore()`.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


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


class BackupService:
    def __init__(self, game_dir: Path, backups_dir: Path, retention: int = 10):
        self._game_dir = Path(game_dir)
        self._backups_dir = Path(backups_dir)
        self._retention = retention

    def list(self) -> list[BackupInfo]:
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        infos: list[BackupInfo] = []
        for meta_path in sorted(self._backups_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                infos.append(BackupInfo(**data))
            except (OSError, ValueError, TypeError):
                continue
        return infos

    def create(self, actor: str) -> BackupInfo:
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        if not self._game_dir.exists():
            raise BackupError("No hay datos de juego para respaldar")

        backup_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        filename = f"backup-{backup_id}.tar.gz"
        archive_path = self._backups_dir / filename
        tmp_path = archive_path.with_suffix(".tmp")

        try:
            with tarfile.open(tmp_path, "w:gz") as archive:
                archive.add(self._game_dir, arcname="game", filter=_no_symlinks_filter)
            tmp_path.replace(archive_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        sha256 = _hash_file(archive_path)
        info = BackupInfo(
            id=backup_id,
            filename=filename,
            size=archive_path.stat().st_size,
            sha256=sha256,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            actor=actor,
        )
        (self._backups_dir / f"{filename}.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
        self._enforce_retention()
        return info

    def restore(self, backup_id: str) -> None:
        matches = [b for b in self.list() if b.id == backup_id]
        if not matches:
            raise BackupError("Backup no encontrado")
        info = matches[0]
        archive_path = self._backups_dir / info.filename
        if not archive_path.exists():
            raise BackupError("El archivo de backup no existe en disco")
        if _hash_file(archive_path) != info.sha256:
            raise BackupError("El hash del backup no coincide; se rechaza la restauracion")

        staging = self._game_dir.parent / f".restore-staging-{int(time.time())}"
        staging.mkdir(parents=True, exist_ok=True)
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

        if self._game_dir.exists():
            _rmtree_ignore(self._game_dir)
        restored_game.replace(self._game_dir)
        _rmtree_ignore(staging)

    def _enforce_retention(self) -> None:
        backups = self.list()
        for old in backups[self._retention :]:
            (self._backups_dir / old.filename).unlink(missing_ok=True)
            (self._backups_dir / f"{old.filename}.json").unlink(missing_ok=True)


def _no_symlinks_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if tarinfo.issym() or tarinfo.islnk():
        return None
    return tarinfo


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
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
