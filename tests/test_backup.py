import pytest
from app.services.backup import BackupError, BackupService


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
