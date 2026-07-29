"""Safe extraction for zip/tar-distributed server software (Bedrock,
Terraria, tModLoader - plan.md sections 4 and 8.5). Handles zip-slip (entries
that escape the destination via `..` or an absolute path), symlink entries,
device/FIFO entries and a total uncompressed-size cap. Direct port of
UrraHosting-WebPanel's dashboard/app/services/archive_extract.py.
"""

from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

_MAX_MEMBERS = 20_000

ProgressCallback = Callable[[int, int], None]


class ArchiveError(ValueError):
    pass


def _safe_target(dest_root: Path, member_name: str) -> Path:
    cleaned = member_name.replace("\\", "/")
    if cleaned.startswith(("/", "../")) or "/../" in cleaned or cleaned == ".." or cleaned.endswith("/.."):
        raise ArchiveError(f"Entrada de archivo invalida: {member_name}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveError(f"Entrada de archivo invalida: {member_name}")
    target = dest_root.joinpath(*parts) if parts else dest_root
    try:
        target.relative_to(dest_root)
    except ValueError as exc:
        raise ArchiveError(f"Entrada de archivo fuera del destino: {member_name}") from exc
    return target


def extract_zip(stream: BinaryIO, dest_root: Path, max_total_bytes: int, on_progress: ProgressCallback | None = None) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(stream) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_MEMBERS:
            raise ArchiveError("El archivo contiene demasiados elementos")
        total = 0
        for processed, info in enumerate(infos, start=1):
            total += info.file_size
            if total > max_total_bytes:
                raise ArchiveError("El archivo descomprimido excede el tamano maximo permitido")
            target = _safe_target(dest_root, info.filename)
            is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink:
                raise ArchiveError(f"No se permiten symlinks en el archivo: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as handle:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            if on_progress is not None:
                on_progress(processed, len(infos))


def extract_tar(stream: BinaryIO, dest_root: Path, max_total_bytes: int, on_progress: ProgressCallback | None = None) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=stream, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > _MAX_MEMBERS:
            raise ArchiveError("El archivo contiene demasiados elementos")
        total = 0
        for processed, member in enumerate(members, start=1):
            if member.issym() or member.islnk():
                raise ArchiveError(f"No se permiten symlinks/hardlinks en el archivo: {member.name}")
            if member.isdev():
                raise ArchiveError(f"No se permiten dispositivos en el archivo: {member.name}")
            if member.isfile():
                total += member.size
                if total > max_total_bytes:
                    raise ArchiveError("El archivo descomprimido excede el tamano maximo permitido")
            target = _safe_target(dest_root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is not None:
                    with open(target, "wb") as handle:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
            if on_progress is not None:
                on_progress(processed, len(members))


def extract_archive(
    filename: str, stream: BinaryIO, dest_root: Path, max_total_bytes: int, on_progress: ProgressCallback | None = None
) -> None:
    lower = filename.lower()
    if lower.endswith(".zip"):
        extract_zip(stream, dest_root, max_total_bytes, on_progress)
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        extract_tar(stream, dest_root, max_total_bytes, on_progress)
    else:
        raise ArchiveError("Formato no soportado; usa .zip, .tar.gz o .tar")
