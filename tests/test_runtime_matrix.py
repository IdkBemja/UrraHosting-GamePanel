import pytest

from config import runtime_matrix as rm


def test_applies_to_only_minecraft_java():
    assert rm.applies_to("minecraft", "java") is True
    assert rm.applies_to("minecraft", "bedrock") is False
    assert rm.applies_to("terraria", "") is False


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.16.5", "8"),
        ("1.17.1", "16"),
        ("1.18.2", "17"),
        ("1.20.4", "17"),
        ("1.20.5", "21"),
        ("1.21.1", "21"),
        ("26.2", "25"),
        ("25.1", "25"),
    ],
)
def test_required_java_version_matrix(version, expected):
    assert rm.required_java_version(version) == expected


def test_resolve_auto_uses_matrix():
    resolved, warning = rm.resolve("1.21.1", "auto")
    assert resolved == "21"
    assert warning is None


def test_resolve_explicit_matching_no_warning():
    resolved, warning = rm.resolve("1.21.1", "21")
    assert resolved == "21"
    assert warning is None


def test_resolve_explicit_mismatch_warns_but_honors_override():
    resolved, warning = rm.resolve("1.16.5", "21")
    assert resolved == "21"
    assert warning is not None


def test_resolve_unsupported_java_raises():
    with pytest.raises(rm.UnsupportedJavaError):
        rm.resolve("1.21.1", "99")
