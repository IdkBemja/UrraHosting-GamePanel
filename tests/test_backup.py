import tarfile
import time

import pytest
from app.services.backup import BackupError, BackupProgress, BackupService


def _make_game_dir(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "server.properties").write_text("motd=hi\n")
    (game_dir / "world").mkdir()
    (game_dir / "world" / "level.dat").write_bytes(b"world-data")
    return game_dir


def test_create_and_list_backup(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    backups_dir = tmp_path / "backups"
    service = BackupService(game_dir=game_dir, backups_dir=backups_dir, retention=10)

    info = service.create(actor="tester")
    assert info.sha256
    listed = service.list()
    assert len(listed) == 1
    assert listed[0].id == info.id


def test_restore_replaces_game_dir(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    backups_dir = tmp_path / "backups"
    service = BackupService(game_dir=game_dir, backups_dir=backups_dir, retention=10)
    info = service.create(actor="tester")

    (game_dir / "world" / "level.dat").write_bytes(b"corrupted!!")
    (game_dir / "new-file-after-backup.txt").write_text("should be gone after restore")

    service.restore(info.id)
    assert (game_dir / "world" / "level.dat").read_bytes() == b"world-data"
    assert not (game_dir / "new-file-after-backup.txt").exists()


def test_restore_unknown_id_raises(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    with pytest.raises(BackupError):
        service.restore("does-not-exist")


def test_retention_prunes_oldest(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=2)
    for _ in range(4):
        service.create(actor="tester")
    assert len(service.list()) <= 2


def test_restore_rejects_tampered_archive(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    backups_dir = tmp_path / "backups"
    service = BackupService(game_dir=game_dir, backups_dir=backups_dir, retention=10)
    info = service.create(actor="tester")

    archive_path = backups_dir / info.filename
    with open(archive_path, "ab") as handle:
        handle.write(b"tampered-bytes")

    with pytest.raises(BackupError):
        service.restore(info.id)


def test_create_skips_unreadable_file_but_still_succeeds(tmp_path, monkeypatch):
    """Regression test for a production incident: a single Minecraft world
    file the dashboard process couldn't read (PermissionError) used to
    abort tarfile.add() and crash the whole /api/backups/create request
    with an unhandled 500. Backup creation must now skip just that file and
    still produce a usable archive with everything else."""
    game_dir = _make_game_dir(tmp_path)
    backups_dir = tmp_path / "backups"
    service = BackupService(game_dir=game_dir, backups_dir=backups_dir, retention=10)

    blocked_path = str(game_dir / "world" / "level.dat")
    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path) == blocked_path:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    info = service.create(actor="tester")
    assert info.skipped_files == ["game/world/level.dat"]

    with tarfile.open(backups_dir / info.filename, "r:gz") as archive:
        names = archive.getnames()
    assert "game/server.properties" in names
    assert "game/world/level.dat" not in names


def test_create_raises_when_everything_unreadable(tmp_path, monkeypatch):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)

    real_open = open
    game_dir_str = str(game_dir)

    def fake_open(path, *args, **kwargs):
        if str(path).startswith(game_dir_str):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    with pytest.raises(BackupError):
        service.create(actor="tester")


def test_create_reports_progress(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    progress = BackupProgress()

    info = service.create(actor="tester", progress=progress)

    assert progress.status == "done"
    assert progress.percent == 100
    assert progress.backup["id"] == info.id


def test_start_create_async_updates_progress_and_calls_on_done(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)

    results = []
    service.start_create_async(actor="tester", on_done=lambda info, error: results.append((info, error)))

    for _ in range(100):
        if not service.is_busy():
            break
        time.sleep(0.05)
    else:
        pytest.fail("backup never finished")

    assert len(results) == 1
    info, error = results[0]
    assert error is None
    assert info is not None
    assert service.get_progress()["status"] == "done"


def test_start_create_async_rejects_concurrent_run(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    service._busy_lock.acquire()
    try:
        with pytest.raises(BackupError):
            service.start_create_async(actor="tester")
    finally:
        service._busy_lock.release()


def test_delete_removes_backup_files(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    backups_dir = tmp_path / "backups"
    service = BackupService(game_dir=game_dir, backups_dir=backups_dir, retention=10)
    info = service.create(actor="tester")

    service.delete(info.id)

    assert service.list() == []
    assert not (backups_dir / info.filename).exists()
    assert not (backups_dir / f"{info.filename}.json").exists()


def test_delete_unknown_id_raises(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    with pytest.raises(BackupError):
        service.delete("does-not-exist")


def test_settings_default_and_update(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=7)

    default_settings = service.get_settings()
    assert default_settings.retention == 7
    assert default_settings.auto_enabled is False

    updated = service.update_settings(retention=3, auto_enabled=True, auto_interval_hours=12)
    assert updated.retention == 3
    assert updated.auto_enabled is True
    assert updated.auto_interval_hours == 12

    # Persisted across a fresh BackupService instance pointed at the same dir.
    reloaded = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=7)
    assert reloaded.get_settings() == updated


def test_update_settings_rejects_out_of_range_retention(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=7)
    with pytest.raises(BackupError):
        service.update_settings(retention=0)
    with pytest.raises(BackupError):
        service.update_settings(auto_interval_hours=1000)


def test_maybe_run_scheduled_skips_when_disabled(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    assert service.maybe_run_scheduled() is False
    assert service.list() == []


def test_maybe_run_scheduled_runs_when_due(tmp_path):
    game_dir = _make_game_dir(tmp_path)
    service = BackupService(game_dir=game_dir, backups_dir=tmp_path / "backups", retention=10)
    service.update_settings(auto_enabled=True, auto_interval_hours=1)

    assert service.maybe_run_scheduled() is True
    for _ in range(100):
        if not service.is_busy():
            break
        time.sleep(0.05)
    else:
        pytest.fail("scheduled backup never finished")
    assert len(service.list()) == 1
