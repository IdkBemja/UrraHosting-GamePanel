from config.game_config import catalog_structure, is_valid_combo, known_adapter_ids, load_from_environ, validate
from tests.conftest import base_env


def test_valid_env_loads_config():
    config, result = load_from_environ(base_env())
    assert result.ok
    assert config is not None
    assert config.adapter_id == "minecraft-java/paper"
    assert config.supports_rcon is True


def test_invalid_uuid_rejected():
    result = validate(base_env(INSTANCE_ID="not-a-uuid"))
    assert not result.ok
    assert any("INSTANCE_ID" in e for e in result.errors)


def test_game_port_equals_dashboard_port_rejected():
    result = validate(base_env(GAME_PORT="8080", DASHBOARD_PORT="8080"))
    assert not result.ok
    assert any("mismo puerto" in e for e in result.errors)


def test_unknown_family_rejected():
    result = validate(base_env(GAME_FAMILY="roblox"))
    assert not result.ok


def test_bedrock_edition_requires_bedrock_software():
    result = validate(base_env(GAME_FAMILY="minecraft", GAME_EDITION="bedrock", GAME_SOFTWARE="paper"))
    assert not result.ok


def test_terraria_rejects_nonempty_edition():
    result = validate(base_env(GAME_FAMILY="terraria", GAME_EDITION="java", GAME_SOFTWARE="vanilla"))
    assert not result.ok


def test_terraria_tmodloader_valid():
    result = validate(
        base_env(
            GAME_FAMILY="terraria",
            GAME_EDITION="",
            GAME_SOFTWARE="tmodloader",
            GAME_PORT="7777",
            RCON_PASSWORD="",
        )
    )
    assert result.ok


def test_terraria_does_not_require_rcon_password():
    config, result = load_from_environ(
        base_env(GAME_FAMILY="terraria", GAME_EDITION="", GAME_SOFTWARE="vanilla", GAME_PORT="7777", RCON_PASSWORD="")
    )
    assert result.ok
    assert config.supports_rcon is False


def test_weak_secret_rejected():
    result = validate(base_env(APP_SECRET="changeme"))
    assert not result.ok


def test_app_password_equal_app_user_rejected():
    result = validate(base_env(APP_USER="admin", APP_PASSWORD="Admin"))
    assert not result.ok


def test_license_not_accepted_rejected():
    result = validate(base_env(LICENSE_ACCEPTED="false"))
    assert not result.ok
    assert any("LICENSE_ACCEPTED" in e for e in result.errors)


def test_memory_reservation_gt_limit_rejected():
    result = validate(base_env(GAME_MEMORY_LIMIT="1G", GAME_MEMORY_RESERVATION="2G"))
    assert not result.ok


def test_control_token_min_length_enforced():
    result = validate(base_env(GAME_CONTROL_TOKEN="short"))
    assert not result.ok
    assert any("GAME_CONTROL_TOKEN" in e for e in result.errors)


def test_known_adapter_ids_cover_plan_table():
    ids = known_adapter_ids()
    for expected in (
        "minecraft-java/vanilla",
        "minecraft-java/paper",
        "minecraft-java/purpur",
        "minecraft-java/spigot",
        "minecraft-java/bukkit",
        "minecraft-java/fabric",
        "minecraft-java/forge",
        "minecraft-java/neoforge",
        "minecraft-bedrock/bedrock",
        "terraria/vanilla",
        "terraria/tmodloader",
    ):
        assert expected in ids


def test_catalog_structure_shape():
    tree = catalog_structure()
    families = {g["family"] for g in tree}
    assert families == {"minecraft", "terraria"}

    minecraft = next(g for g in tree if g["family"] == "minecraft")
    editions = {e["edition"] for e in minecraft["editions"]}
    assert editions == {"java", "bedrock"}

    java_edition = next(e for e in minecraft["editions"] if e["edition"] == "java")
    assert "paper" in java_edition["software"]

    terraria = next(g for g in tree if g["family"] == "terraria")
    assert terraria["editions"][0]["edition"] == ""
    assert set(terraria["editions"][0]["software"]) == {"vanilla", "tmodloader"}


def test_is_valid_combo():
    assert is_valid_combo("minecraft", "java", "paper") is True
    assert is_valid_combo("minecraft", "bedrock", "paper") is False
    assert is_valid_combo("terraria", "", "tmodloader") is True
    assert is_valid_combo("terraria", "java", "vanilla") is False
    assert is_valid_combo("roblox", "", "anything") is False
