from config import validate_cli
from tests.conftest import base_env


def _run(monkeypatch, env):
    monkeypatch.setattr("os.environ", env)
    return validate_cli.main()


def test_valid_env_returns_zero(monkeypatch):
    assert _run(monkeypatch, base_env()) == 0


def test_invalid_core_env_returns_one(monkeypatch):
    assert _run(monkeypatch, base_env(INSTANCE_ID="nope")) == 1


def test_invalid_adapter_specific_env_returns_one(monkeypatch):
    assert _run(monkeypatch, base_env(DIFFICULTY="impossible")) == 1


def test_terraria_env_valid(monkeypatch):
    env = base_env(
        GAME_FAMILY="terraria", GAME_EDITION="", GAME_SOFTWARE="vanilla", GAME_PORT="7777", RCON_PASSWORD="", DIFFICULTY="classic"
    )
    assert _run(monkeypatch, env) == 0
