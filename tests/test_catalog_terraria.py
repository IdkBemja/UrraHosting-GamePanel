import pytest
from app.services.catalog import base as catalog_base
from app.services.catalog import terraria as tr
from app.services.catalog.base import CatalogError, _TTLCache


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data
        self.text = ""
        self.status_code = 200
        self.headers = {}
        self.is_redirect = False
        self.is_permanent_redirect = False

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


_NAMES_RESPONSE = ["terraria-server-1449.zip", "terraria-server-1449.zip"]


def test_list_versions_parses_compact_version(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_NAMES_RESPONSE))
    provider = tr.TerrariaProvider(_TTLCache(60))
    assert provider.list_versions() == ["1.4.4.9"]


def test_get_download_builds_correct_url(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_NAMES_RESPONSE))
    provider = tr.TerrariaProvider(_TTLCache(60))
    info = provider.get_download("1.4.4.9")
    assert info.url == "https://terraria.org/api/download/pc-dedicated-server/terraria-server-1449.zip"
    assert info.install_kind == "zip"
    assert info.expected_entrypoint == "TerrariaServer.bin.x86_64"


def test_get_download_accepts_compact_or_latest(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_NAMES_RESPONSE))
    provider = tr.TerrariaProvider(_TTLCache(60))
    assert provider.get_download("1449").url.endswith("1449.zip")
    assert provider.get_download("latest").url.endswith("1449.zip")


def test_get_download_stale_version_rejected(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_NAMES_RESPONSE))
    provider = tr.TerrariaProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.get_download("1.0.0.0")


def test_no_version_found_raises(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse([]))
    provider = tr.TerrariaProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.list_versions()
