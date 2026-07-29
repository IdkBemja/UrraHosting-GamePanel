import pytest
from app.services.catalog import base as catalog_base
from app.services.catalog import minecraft_java as mj
from app.services.catalog.base import CatalogError, _TTLCache


class _FakeResponse:
    def __init__(self, *, json_data=None, text_data="", status_code=200, redirect_to=None):
        self._json = json_data
        self.text = text_data
        self.status_code = status_code
        self.headers = {"Location": redirect_to} if redirect_to else {}
        self.is_redirect = redirect_to is not None
        self.is_permanent_redirect = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json


def _patch_request(monkeypatch, responder):
    monkeypatch.setattr(catalog_base.requests, "request", responder)


def test_vanilla_list_versions_filters_releases(monkeypatch):
    manifest = {
        "versions": [
            {"id": "26.3-snapshot-1", "type": "snapshot", "url": "https://piston-meta.mojang.com/snap.json"},
            {"id": "26.2", "type": "release", "url": "https://piston-meta.mojang.com/26.2.json"},
        ]
    }
    _patch_request(monkeypatch, lambda method, url, **kw: _FakeResponse(json_data=manifest))

    provider = mj.VanillaProvider(_TTLCache(60))
    assert provider.list_versions() == ["26.2"]


def test_vanilla_rejects_disallowed_host(monkeypatch):
    provider = mj.VanillaProvider(_TTLCache(60))
    monkeypatch.setattr(provider, "_MANIFEST_URL", "https://evil.example.com/manifest.json", raising=False)
    with pytest.raises(CatalogError):
        provider.list_versions()


def test_vanilla_get_download(monkeypatch):
    manifest = {"versions": [{"id": "26.2", "type": "release", "url": "https://piston-meta.mojang.com/26.2.json"}]}
    version_meta = {"downloads": {"server": {"url": "https://piston-data.mojang.com/server.jar", "sha1": "abc", "size": 1}}}

    def responder(method, url, **kw):
        if "26.2.json" in url:
            return _FakeResponse(json_data=version_meta)
        return _FakeResponse(json_data=manifest)

    _patch_request(monkeypatch, responder)
    provider = mj.VanillaProvider(_TTLCache(60))
    info = provider.get_download("26.2")
    assert info.sha1 == "abc"


def test_paper_picks_stable_channel_build(monkeypatch):
    builds = [
        {
            "id": 10,
            "channel": "EXPERIMENTAL",
            "downloads": {"server:default": {"url": "https://fill-data.papermc.io/10.jar", "name": "10.jar"}},
        },
        {
            "id": 12,
            "channel": "STABLE",
            "downloads": {
                "server:default": {"url": "https://fill-data.papermc.io/12.jar", "name": "12.jar", "checksums": {"sha256": "deadbeef"}}
            },
        },
    ]
    _patch_request(monkeypatch, lambda method, url, **kw: _FakeResponse(json_data=builds))
    provider = mj.PaperProvider(_TTLCache(60))
    info = provider.get_download("1.21.1", channel="stable")
    assert info.url.endswith("12.jar")
    assert info.sha256 == "deadbeef"


def test_purpur_requires_success(monkeypatch):
    _patch_request(monkeypatch, lambda method, url, **kw: _FakeResponse(json_data={"result": "FAILURE"}))
    provider = mj.PurpurProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.get_download("1.21.1")


def test_buildtools_install_kind():
    provider = mj.BuildToolsProvider(_TTLCache(60), "spigot")
    info = provider.get_download("1.21.1")
    assert info.install_kind == "buildtools"


def test_fabric_get_download(monkeypatch):
    loaders = [{"loader": {"version": "0.15.0", "stable": True}, "installer": {"version": "1.0.0"}}]
    _patch_request(monkeypatch, lambda method, url, **kw: _FakeResponse(json_data=loaders))
    provider = mj.FabricProvider(_TTLCache(60))
    info = provider.get_download("1.21.1")
    assert "0.15.0" in info.url
    assert "1.0.0" in info.url


def test_maven_installer_provider_parses_metadata_xml(monkeypatch):
    xml = "<metadata><versioning><versions><version>1.21.1-51.0.1</version><version>1.21.1-51.0.2</version></versions></versioning></metadata>"
    _patch_request(monkeypatch, lambda method, url, **kw: _FakeResponse(text_data=xml))
    provider = mj.MavenInstallerProvider(
        _TTLCache(60),
        "forge",
        "https://maven.minecraftforge.net/metadata.xml",
        "https://maven.minecraftforge.net/net/minecraftforge/forge",
        frozenset({"maven.minecraftforge.net"}),
    )
    versions = provider.list_versions()
    assert versions[0] == "1.21.1-51.0.2"  # reversed: latest first
    info = provider.get_download("1.21.1-51.0.2")
    assert info.install_kind == "installer"


def test_redirect_to_disallowed_host_rejected(monkeypatch):
    calls = {"n": 0}

    def responder(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(redirect_to="https://evil.example.com/payload.json")
        raise AssertionError("should never follow the redirect")

    _patch_request(monkeypatch, responder)
    provider = mj.VanillaProvider(_TTLCache(60))
    with pytest.raises(CatalogError):
        provider.list_versions()
