import pytest
from app.services.catalog import base as catalog_base
from app.services.catalog import tmodloader as tml
from app.services.catalog.base import CatalogError, _TTLCache


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data
        self.status_code = 200
        self.headers = {}
        self.is_redirect = False
        self.is_permanent_redirect = False

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


_RELEASES = [
    {
        "tag_name": "v2026.5.3.0",
        "prerelease": False,
        "assets": [
            {"name": "tModLoader.zip", "browser_download_url": "https://github.com/x/tModLoader.zip", "size": 100, "digest": "sha256:abcd"}
        ],
    },
    {
        "tag_name": "v2026.6.0.0-beta",
        "prerelease": True,
        "assets": [{"name": "tModLoader.zip", "browser_download_url": "https://github.com/x/tModLoader-beta.zip", "size": 100}],
    },
]


def test_list_versions_stable_excludes_prerelease(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_RELEASES))
    provider = tml.TModLoaderProvider(_TTLCache(60))
    assert provider.list_versions("stable") == ["v2026.5.3.0"]


def test_list_versions_preview_only_prerelease(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_RELEASES))
    provider = tml.TModLoaderProvider(_TTLCache(60))
    assert provider.list_versions("preview") == ["v2026.6.0.0-beta"]


def test_get_download_extracts_sha256_digest(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_RELEASES))
    provider = tml.TModLoaderProvider(_TTLCache(60))
    info = provider.get_download("v2026.5.3.0", "stable")
    assert info.sha256 == "abcd"
    assert info.install_kind == "zip"
    assert info.expected_entrypoint == "start-tModLoaderServer.sh"


def test_get_download_unknown_version_raises(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_RELEASES))
    provider = tml.TModLoaderProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.get_download("v0.0.0.0", "stable")
