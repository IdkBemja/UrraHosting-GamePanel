import pytest
from app.services.users import UserStore


def test_bootstrap_admin_created(tmp_path):
    store = UserStore(tmp_path / "users.sqlite3", "admin", "a-strong-password")
    user = store.get("admin")
    assert user is not None
    assert user["role"] == "admin"


def test_authenticate_success_and_failure(tmp_path):
    store = UserStore(tmp_path / "users.sqlite3", "admin", "a-strong-password")
    assert store.authenticate("admin", "a-strong-password") is not None
    assert store.authenticate("admin", "wrong") is None


def test_create_user_duplicate_rejected(tmp_path):
    store = UserStore(tmp_path / "users.sqlite3", "admin", "a-strong-password")
    store.create("operator1", "another-strong-pw", "operator")
    with pytest.raises(ValueError):
        store.create("operator1", "another-strong-pw", "operator")


def test_create_user_short_password_rejected(tmp_path):
    store = UserStore(tmp_path / "users.sqlite3", "admin", "a-strong-password")
    with pytest.raises(ValueError):
        store.create("shortpw", "short", "operator")


def test_update_deactivate_user(tmp_path):
    store = UserStore(tmp_path / "users.sqlite3", "admin", "a-strong-password")
    store.create("operator1", "another-strong-pw", "operator")
    store.update("operator1", active=False)
    assert store.authenticate("operator1", "another-strong-pw") is None
