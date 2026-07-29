import json
import os

import pytest

from config.game_config import load_from_environ
from runtime.adapters import UnknownAdapterError, get_adapter
from runtime.adapters.base import AdapterConfigError
from tests.conftest import base_env


def _config(**overrides):
    config, result = load_from_environ(base_env(**overrides))
    assert result.ok, result.errors
    return config


def test_get_adapter_unknown_combo_raises():
    with pytest.raises(UnknownAdapterError):
        get_adapter("minecraft", "pocket", "vanilla")


# -- Minecraft Java -----------------------------------------------------


def test_minecraft_java_file_categories_paper_has_plugins():
    adapter = get_adapter("minecraft", "java", "paper")
    config = _config(GAME_SOFTWARE="paper")
    categories = adapter.file_categories(config)
    assert "plugins" in categories
    assert "mods" not in categories


def test_minecraft_java_file_categories_fabric_has_mods():
    adapter = get_adapter("minecraft", "java", "fabric")
    config = _config(GAME_SOFTWARE="fabric")
    categories = adapter.file_categories(config)
    assert "mods" in categories
    assert "plugins" not in categories


def test_minecraft_java_prepare_writes_eula_and_properties(tmp_path):
    adapter = get_adapter("minecraft", "java", "paper")
    config = _config()
    adapter.prepare(config, base_env(), tmp_path)

    eula = (tmp_path / "eula.txt").read_text()
    assert "eula=true" in eula

    props = (tmp_path / "server.properties").read_text()
    assert "server-port=25565" in props
    assert "rcon.password=a-strong-rcon-password" in props
    assert "enable-rcon=true" in props


def test_minecraft_java_launch_command_no_shell_and_requires_jar(tmp_path):
    adapter = get_adapter("minecraft", "java", "paper")
    config = _config()
    with pytest.raises(AdapterConfigError):
        adapter.launch_command(config, base_env(), tmp_path)

    (tmp_path / "server.jar").write_bytes(b"fake jar")
    (tmp_path / "installation.json").write_text(json.dumps({"launch_mode": "jar", "target_filename": "server.jar"}))
    argv = adapter.launch_command(config, base_env(), tmp_path)
    assert "-jar" in argv
    assert str(tmp_path / "server.jar") in argv
    assert " " not in argv[0]  # argv[0] is a bare path, never shell-interpolated


def test_minecraft_java_launch_command_script_mode(tmp_path):
    adapter = get_adapter("minecraft", "java", "forge")
    config = _config(GAME_SOFTWARE="forge")
    run_sh = tmp_path / "run.sh"
    run_sh.write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "installation.json").write_text(json.dumps({"launch_mode": "script"}))
    argv = adapter.launch_command(config, base_env(), tmp_path)
    assert argv[0] == "bash"
    assert str(run_sh) in argv


def test_minecraft_java_validate_extra_rejects_bad_difficulty():
    adapter = get_adapter("minecraft", "java", "paper")
    errors = adapter.validate_extra(base_env(DIFFICULTY="impossible"))
    assert errors


# -- Minecraft Bedrock ----------------------------------------------------


def test_bedrock_file_categories_no_plugins_or_mods():
    adapter = get_adapter("minecraft", "bedrock", "bedrock")
    config = _config(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="bedrock", GAME_PORT="19132")
    categories = adapter.file_categories(config)
    assert "plugins" not in categories
    assert "mods" not in categories
    assert "game" in categories


def test_bedrock_prepare_writes_expected_keys(tmp_path):
    adapter = get_adapter("minecraft", "bedrock", "bedrock")
    config = _config(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="bedrock", GAME_PORT="19132")
    adapter.prepare(config, base_env(GAME_EDITION="bedrock"), tmp_path)
    props = (tmp_path / "server.properties").read_text()
    assert "server-port=19132" in props
    assert "level-name=world" in props


def test_bedrock_launch_env_sets_ld_library_path(tmp_path):
    adapter = get_adapter("minecraft", "bedrock", "bedrock")
    config = _config(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="bedrock", GAME_PORT="19132")
    assert adapter.launch_env(config, base_env(), tmp_path) == {"LD_LIBRARY_PATH": "."}


def test_bedrock_launch_command_requires_binary(tmp_path):
    adapter = get_adapter("minecraft", "bedrock", "bedrock")
    config = _config(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="bedrock", GAME_PORT="19132")
    with pytest.raises(AdapterConfigError):
        adapter.launch_command(config, base_env(), tmp_path)


def test_bedrock_launch_command_makes_binary_executable(tmp_path):
    # archive_extract.py does not preserve the zip's Unix permission bits,
    # so a freshly-extracted bedrock_server would not be +x without this.
    # Windows has no real executable bit to assert on, so this only checks
    # the meaningful part there (chmod() is called without raising).
    adapter = get_adapter("minecraft", "bedrock", "bedrock")
    config = _config(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="bedrock", GAME_PORT="19132")
    binary = tmp_path / "bedrock_server"
    binary.write_bytes(b"fake binary")
    binary.chmod(0o644)
    adapter.launch_command(config, base_env(), tmp_path)
    if os.name == "posix":
        assert binary.stat().st_mode & 0o100


# -- Terraria / tModLoader -------------------------------------------------


def test_terraria_file_categories_no_mods():
    adapter = get_adapter("terraria", "", "vanilla")
    categories = adapter.file_categories(None)
    assert "mods" not in categories
    assert "worlds" in categories


def test_tmodloader_file_categories_include_mods_and_worlds():
    adapter = get_adapter("terraria", "", "tmodloader")
    categories = adapter.file_categories(None)
    for expected in ("mods", "modconfigs", "worlds", "players"):
        assert expected in categories


def _terraria_env(**overrides):
    return base_env(
        GAME_FAMILY="terraria",
        GAME_EDITION="",
        GAME_SOFTWARE="vanilla",
        GAME_PORT="7777",
        RCON_PASSWORD="",
        DIFFICULTY="classic",
        **overrides,
    )


def test_terraria_prepare_sets_autocreate_when_world_missing(tmp_path):
    adapter = get_adapter("terraria", "", "vanilla")
    env = _terraria_env()
    config = _config(**env)
    adapter.prepare(config, env, tmp_path)
    content = (tmp_path / "serverconfig.txt").read_text()
    assert "autocreate=2" in content
    assert "port=7777" in content


def test_terraria_prepare_skips_autocreate_when_world_exists(tmp_path):
    adapter = get_adapter("terraria", "", "vanilla")
    env = _terraria_env()
    config = _config(**env)
    (tmp_path / "worlds").mkdir()
    (tmp_path / "worlds" / "world.wld").write_bytes(b"fake")
    adapter.prepare(config, env, tmp_path)
    content = (tmp_path / "serverconfig.txt").read_text()
    assert "autocreate" not in content


def test_terraria_launch_command_requires_binary(tmp_path):
    adapter = get_adapter("terraria", "", "vanilla")
    config = _config(GAME_FAMILY="terraria", GAME_EDITION="", GAME_SOFTWARE="vanilla", GAME_PORT="7777", RCON_PASSWORD="")
    with pytest.raises(AdapterConfigError):
        adapter.launch_command(config, base_env(), tmp_path)


def test_tmodloader_launch_command_uses_start_script(tmp_path):
    adapter = get_adapter("terraria", "", "tmodloader")
    config = _config(GAME_FAMILY="terraria", GAME_EDITION="", GAME_SOFTWARE="tmodloader", GAME_PORT="7777", RCON_PASSWORD="")
    (tmp_path / "start-tModLoaderServer.sh").write_text("#!/bin/bash\n")
    argv = adapter.launch_command(config, base_env(), tmp_path)
    assert argv[0] == "bash"
    assert "-savedirectory" in argv


def test_control_channel_is_fifo_for_terraria_family():
    assert get_adapter("terraria", "", "vanilla").control_channel == "fifo"
    assert get_adapter("terraria", "", "tmodloader").control_channel == "fifo"
    assert get_adapter("minecraft", "java", "paper").control_channel == "rcon"
    assert get_adapter("minecraft", "bedrock", "bedrock").control_channel == "rcon"
