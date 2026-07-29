import io
import tarfile
import zipfile

import pytest
from app.services.archive_extract import ArchiveError, extract_archive


def _make_zip(entries: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def test_extract_zip_normal(tmp_path):
    buf = _make_zip({"a.txt": b"hello", "sub/b.txt": b"world"})
    extract_archive("archive.zip", buf, tmp_path, max_total_bytes=10_000)
    assert (tmp_path / "a.txt").read_bytes() == b"hello"
    assert (tmp_path / "sub" / "b.txt").read_bytes() == b"world"


def test_extract_zip_slip_rejected(tmp_path):
    buf = _make_zip({"../../evil.txt": b"pwn"})
    with pytest.raises(ArchiveError):
        extract_archive("archive.zip", buf, tmp_path, max_total_bytes=10_000)
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_extract_zip_absolute_path_rejected(tmp_path):
    buf = _make_zip({"/etc/passwd": b"pwn"})
    with pytest.raises(ArchiveError):
        extract_archive("archive.zip", buf, tmp_path, max_total_bytes=10_000)


def test_extract_zip_oversized_rejected(tmp_path):
    buf = _make_zip({"big.txt": b"x" * 1000})
    with pytest.raises(ArchiveError):
        extract_archive("archive.zip", buf, tmp_path, max_total_bytes=10)


def test_extract_tar_slip_rejected(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="../evil.txt")
        data = b"pwn"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    with pytest.raises(ArchiveError):
        extract_archive("archive.tar.gz", buf, tmp_path, max_total_bytes=10_000)


def test_extract_unsupported_format_rejected(tmp_path):
    with pytest.raises(ArchiveError):
        extract_archive("archive.rar", io.BytesIO(b"whatever"), tmp_path, max_total_bytes=10_000)
