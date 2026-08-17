from config.game_properties import upsert_properties


def test_creates_new_file(tmp_path):
    path = tmp_path / "server.properties"
    upsert_properties(path, {"server-port": "25565", "motd": "Hello"})
    content = path.read_text(encoding="utf-8")
    assert "server-port=25565" in content
    assert "motd=Hello" in content


def test_preserves_comments_and_unmanaged_keys(tmp_path):
    path = tmp_path / "server.properties"
    path.write_text("#Minecraft server properties\n#Comment line\nlevel-seed=\nunrelated-key=keepme\n", encoding="utf-8")
    upsert_properties(path, {"level-name": "world"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "#Minecraft server properties" in lines
    assert "#Comment line" in lines
    assert "unrelated-key=keepme" in lines
    assert "level-name=world" in lines


def test_updates_existing_managed_key_in_place(tmp_path):
    path = tmp_path / "server.properties"
    path.write_text("motd=Old\nlevel-name=world\n", encoding="utf-8")
    upsert_properties(path, {"motd": "New"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "motd=New"
    assert lines[1] == "level-name=world"


def test_remove_drops_existing_key_entirely(tmp_path):
    path = tmp_path / "serverconfig.txt"
    path.write_text("worldname=world\nautocreate=2\n", encoding="utf-8")
    upsert_properties(path, {"worldname": "world"}, remove={"autocreate"})
    content = path.read_text(encoding="utf-8")
    assert "autocreate" not in content
    assert "worldname=world" in content


def test_remove_is_a_noop_when_key_never_existed(tmp_path):
    path = tmp_path / "serverconfig.txt"
    upsert_properties(path, {"worldname": "world"}, remove={"autocreate"})
    content = path.read_text(encoding="utf-8")
    assert "autocreate" not in content
    assert "worldname=world" in content


def test_values_takes_precedence_over_remove_for_the_same_key(tmp_path):
    path = tmp_path / "serverconfig.txt"
    path.write_text("autocreate=1\n", encoding="utf-8")
    upsert_properties(path, {"autocreate": "2"}, remove={"autocreate"})
    assert "autocreate=2" in path.read_text(encoding="utf-8")
