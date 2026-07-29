import pytest
from app.services.catalog import base as catalog_base
from app.services.catalog import minecraft_bedrock as mb
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


_LINKS_PAYLOAD = {
    "result": {
        "links": [
            {
                "downloadType": "serverBedrockWindows",
                "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-win/bedrock-server-1.21.50.7.zip",
            },
            {
                "downloadType": "serverBedrockLinux",
                "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.21.50.7.zip",
            },
            {
                "downloadType": "serverBedrockPreviewLinux",
                "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux-preview/bedrock-server-1.21.60.3.zip",
            },
        ]
    }
}


def test_list_versions_stable(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    assert provider.list_versions("stable") == ["1.21.50.7"]


def test_list_versions_preview(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    assert provider.list_versions("preview") == ["1.21.60.3"]


def test_get_download_returns_linux_zip(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    info = provider.get_download("1.21.50.7", "stable")
    assert info.install_kind == "zip"
    assert info.expected_entrypoint == "bedrock_server"
    assert "bin-linux/" in info.url


def test_get_download_stale_version_rejected(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.get_download("1.0.0", "stable")


def test_get_download_latest_alias_works(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    info = provider.get_download("latest", "stable")
    assert info.url.endswith("bedrock-server-1.21.50.7.zip")


def test_invalid_channel_rejected(monkeypatch):
    monkeypatch.setattr(catalog_base.requests, "request", lambda method, url, **kw: _FakeResponse(_LINKS_PAYLOAD))
    provider = mb.BedrockProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.list_versions("nightly")
