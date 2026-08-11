import pytest

from config.instance_state import clear_override, effective_environ, read_override, remove_override_keys, write_override


def test_read_override_missing_file_returns_empty(tmp_path):
    assert read_override(tmp_path / "nope.json") == {}


def test_write_and_read_override_roundtrip(tmp_path):
    path = tmp_path / "override.json"
    write_override({"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "tmodloader"}, path=path)
    assert read_override(path) == {"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "tmodloader"}


def test_write_override_rejects_unknown_keys(tmp_path):
    with pytest.raises(ValueError):
        write_override({"APP_SECRET": "sneaky"}, path=tmp_path / "override.json")


def test_read_override_ignores_unknown_keys_in_file(tmp_path):
    path = tmp_path / "override.json"
    path.write_text('{"GAME_FAMILY": "terraria", "APP_SECRET": "sneaky", "RCON_PASSWORD": "x"}', encoding="utf-8")
    assert read_override(path) == {"GAME_FAMILY": "terraria"}


def test_read_override_ignores_malformed_json(tmp_path):
    path = tmp_path / "override.json"
    path.write_text("not json{{{", encoding="utf-8")
    assert read_override(path) == {}


def test_clear_override_removes_file(tmp_path):
    path = tmp_path / "override.json"
    write_override({"GAME_FAMILY": "terraria"}, path=path)
    assert path.exists()
    clear_override(path)
    assert not path.exists()
    clear_override(path)  # idempotent, no error on missing file


def test_effective_environ_merges_override_over_base(tmp_path):
    path = tmp_path / "override.json"
    write_override({"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "vanilla"}, path=path)
    base = {"GAME_FAMILY": "minecraft", "GAME_EDITION": "java", "GAME_SOFTWARE": "paper", "APP_SECRET": "x" * 40}
    merged = effective_environ(base, path=path)
    assert merged["GAME_FAMILY"] == "terraria"
    assert merged["GAME_EDITION"] == ""
    assert merged["GAME_SOFTWARE"] == "vanilla"
    assert merged["APP_SECRET"] == "x" * 40  # untouched, not an overridable key


def test_effective_environ_without_override_file_is_passthrough(tmp_path):
    base = {"GAME_FAMILY": "minecraft"}
    assert effective_environ(base, path=tmp_path / "missing.json") == base


def test_write_override_creates_world_readable_file(tmp_path):
    """The dashboard (uid 10001) writes this file but game-runtime's
    entrypoint/agent (a different container, uid 10000, no shared group)
    must be able to read it - tempfile.mkstemp()'s default 0600 would make
    that impossible."""
    import stat

    path = tmp_path / "override.json"
    write_override({"GAME_FAMILY": "terraria"}, path=path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o044 == 0o044, f"expected group+other read bits, got {oct(mode)}"


def test_write_override_merges_with_existing_keys_by_default(tmp_path):
    """The Software tab (reprovision identity keys) and the Configuracion
    tab (settings keys, see dashboard/app/blueprints/settings.py) are two
    independent callers writing to the SAME override file - neither may
    clobber the other's fields."""
    path = tmp_path / "override.json"
    write_override({"MOTD": "hola", "MAX_PLAYERS": "10"}, path=path)
    write_override({"GAME_FAMILY": "terraria", "GAME_EDITION": "", "GAME_SOFTWARE": "vanilla"}, path=path)
    assert read_override(path) == {
        "MOTD": "hola",
        "MAX_PLAYERS": "10",
        "GAME_FAMILY": "terraria",
        "GAME_EDITION": "",
        "GAME_SOFTWARE": "vanilla",
    }


def test_write_override_merge_false_replaces_the_file(tmp_path):
    path = tmp_path / "override.json"
    write_override({"MOTD": "hola", "MAX_PLAYERS": "10"}, path=path)
    write_override({"GAME_FAMILY": "terraria"}, path=path, merge=False)
    assert read_override(path) == {"GAME_FAMILY": "terraria"}


def test_remove_override_keys_drops_only_the_given_keys(tmp_path):
    path = tmp_path / "override.json"
    write_override({"MOTD": "hola", "MAX_PLAYERS": "10", "DIFFICULTY": "hard", "GAMEMODE": "creative"}, path=path)
    remove_override_keys({"DIFFICULTY", "GAMEMODE"}, path=path)
    assert read_override(path) == {"MOTD": "hola", "MAX_PLAYERS": "10"}


def test_remove_override_keys_is_a_noop_when_nothing_to_remove(tmp_path):
    path = tmp_path / "override.json"
    write_override({"MOTD": "hola"}, path=path)
    before = path.stat().st_mtime_ns
    remove_override_keys({"DIFFICULTY"}, path=path)
    assert path.stat().st_mtime_ns == before  # never rewritten when there's nothing to drop
