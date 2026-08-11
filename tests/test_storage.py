import io
from pathlib import Path

import pytest
from app.services.storage import InstanceStorage, PathTraversalError, StorageError, sanitize_filename


def _storage(tmp_path, **kwargs):
    root = tmp_path / "game"
    root.mkdir()
    return InstanceStorage(roots={"game": root}, max_upload_bytes=10_000_000, **kwargs)


def test_sanitize_filename_rejects_dotdot():
    with pytest.raises(StorageError):
        sanitize_filename("..")


def test_sanitize_filename_strips_path_components():
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_resolve_rejects_traversal(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(PathTraversalError):
        storage.resolve("game", "../../../etc/passwd")


def test_resolve_unknown_category_rejected(tmp_path):
    storage = _storage(tmp_path)
    with pytest.raises(StorageError):
        storage.resolve("nope", "x")


def test_save_upload_and_list_dir(tmp_path):
    storage = _storage(tmp_path)
    storage.save_upload("game", "", "hello.txt", io.BytesIO(b"hi"), content_length=2)
    entries = storage.list_dir("game")
    assert len(entries) == 1
    assert entries[0].name == "hello.txt"


def test_save_upload_rejects_existing_without_overwrite(tmp_path):
    storage = _storage(tmp_path)
    storage.save_upload("game", "", "hello.txt", io.BytesIO(b"hi"), content_length=2)
    with pytest.raises(StorageError):
        storage.save_upload("game", "", "hello.txt", io.BytesIO(b"again"), content_length=5)


def test_save_upload_enforces_max_upload_bytes(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    storage = InstanceStorage(roots={"game": root}, max_upload_bytes=4)
    with pytest.raises(StorageError):
        storage.save_upload("game", "", "big.txt", io.BytesIO(b"way too big"), content_length=11)


def test_save_upload_wraps_mkdir_oserror_as_storage_error(tmp_path, monkeypatch):
    """A permission/disk failure preparing the destination directory must
    come back as a clean StorageError (files.py's existing `except
    StorageError` returns clean JSON for it) rather than an unhandled
    OSError - that used to fall through to Flask's default HTML error page,
    which app.js can't parse and shows a useless generic "No se pudo subir
    el archivo" with no real cause visible."""
    storage = _storage(tmp_path)

    def failing_mkdir(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    with pytest.raises(StorageError, match="No se pudo preparar la carpeta de destino"):
        storage.save_upload("game", "", "mod.jar", io.BytesIO(b"jar bytes"), content_length=9)


def test_save_upload_wraps_write_oserror_as_storage_error(tmp_path, monkeypatch):
    storage = _storage(tmp_path)

    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".part") and "w" in mode:
            raise OSError(28, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    with pytest.raises(StorageError, match="No se pudo guardar el archivo"):
        storage.save_upload("game", "", "mod.jar", io.BytesIO(b"jar bytes"), content_length=9)
    # The failed partial write must never linger as a stray ".part" file.
    assert list((tmp_path / "game").iterdir()) == []


def test_quota_enforced(tmp_path):
    storage = _storage(tmp_path, quota_bytes=5)
    with pytest.raises(StorageError):
        storage.save_upload("game", "", "big.txt", io.BytesIO(b"123456"), content_length=6)


def test_delete_rejects_symlink(tmp_path):
    storage = _storage(tmp_path)
    real_file = tmp_path / "outside.txt"
    real_file.write_text("secret")
    link = tmp_path / "game" / "link.txt"
    try:
        link.symlink_to(real_file)
    except OSError:
        pytest.skip("symlinks not supported in this environment")
    with pytest.raises(StorageError):
        storage.delete("game", "link.txt")
    assert real_file.exists()


def test_clear_category_wipes_contents_but_keeps_the_root(tmp_path, monkeypatch):
    """`clear_category` must never attempt to remove the category root
    itself - only bind-mounted DIRECTORY CONTENTS are writable from inside
    the container on some backends (observed as `OSError: [Errno 30]
    Read-only file system` when Docker Desktop's Windows file sharing was
    asked to rmdir the mount point itself, even though every file/
    subdirectory under it could be freely deleted)."""
    import shutil

    storage = _storage(tmp_path)
    root = storage._root("game")
    (root / "leftover.txt").write_text("stale")
    (root / "subdir").mkdir()
    (root / "subdir" / "nested.txt").write_text("stale nested")

    real_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        assert Path(path) != root, "clear_category must never rmtree the root itself"
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)
    storage.clear_category("game")

    assert root.exists()
    assert list(root.iterdir()) == []


def test_clear_category_skips_undeletable_entry_with_a_warning(tmp_path, monkeypatch):
    """One entry a different container's uid owns (so even chmod fails, not
    just the delete) must not block clearing the rest of the category, or
    abort the software (re)install that triggered this clear."""
    import shutil

    storage = _storage(tmp_path)
    root = storage._root("game")
    (root / "removable.txt").write_text("fine")
    stuck_dir = root / "stuck"
    stuck_dir.mkdir()

    real_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        if Path(path) == stuck_dir:
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)

    warnings = storage.clear_category("game")

    assert not (root / "removable.txt").exists()
    assert stuck_dir.exists()
    assert warnings and "stuck" in warnings[0]


def test_usage_bytes_never_blocks_and_refreshes_in_background(tmp_path):
    """usage_bytes() must never walk the filesystem on the calling thread
    (a real install can leave thousands of files behind it, and one such
    walk was observed to take 30+ seconds on some bind-mount backends) - it
    returns the last known value immediately (0 before anything has ever
    been computed) and lets a background thread fill the cache in."""
    import time as time_module

    import app.services.storage as storage_module

    storage_module._usage_cache.clear()
    storage = _storage(tmp_path)
    storage.save_upload("game", "", "a.txt", io.BytesIO(b"12345"), content_length=5)

    # Nothing has been computed yet - must be 0, not a blocking real walk.
    assert storage.usage_bytes("game") == 0

    root = storage._root("game")
    deadline = time_module.monotonic() + 2.0
    while root not in storage_module._usage_cache and time_module.monotonic() < deadline:
        time_module.sleep(0.02)
    assert root in storage_module._usage_cache, "background refresh never populated the cache"
    assert storage.usage_bytes("game") == 5

    # A second file appears, but within the TTL window the cached total
    # must still be returned immediately - this is what keeps repeated
    # Overview-tab polls from re-walking thousands of files every time.
    storage.save_upload("game", "", "b.txt", io.BytesIO(b"1234567890"), content_length=10)
    assert storage.usage_bytes("game") == 5

    # Once the cache entry is force-expired, a fresh background refresh
    # kicks in and eventually reflects the real (updated) total.
    cached_at, cached_value = storage_module._usage_cache[root]
    storage_module._usage_cache[root] = (cached_at - storage_module._USAGE_CACHE_TTL_SECONDS - 1, cached_value)
    storage.usage_bytes("game")  # triggers the refresh; still returns the stale value this call
    deadline = time_module.monotonic() + 2.0
    while storage_module._usage_cache[root][1] != 15 and time_module.monotonic() < deadline:
        time_module.sleep(0.02)
    assert storage.usage_bytes("game") == 15
