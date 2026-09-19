"""'Check for updates' (app/api/v1/updates.py) — compares the running
build's version against the latest GitHub release, without ever making a
real network call in tests (httpx.get is monkeypatched)."""
import httpx

from app.core.version import get_app_version
from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


def _auth(client, session):
    make_user(session, UserRole.ADMIN, email="updates@x.com")
    return auth_headers(client, "updates@x.com")


def test_update_available_when_release_tag_is_newer(client, session, monkeypatch):
    current = get_app_version()
    newer = f"v{int(current.split('.')[0]) + 1}.0.0"

    def fake_get(url, **kwargs):
        return _FakeResponse({
            "tag_name": newer,
            "html_url": "https://github.com/x/y/releases/tag/" + newer,
            "assets": [{"name": "FirmRMS-Setup.exe", "browser_download_url": "https://example.com/FirmRMS-Setup.exe"}],
        })

    monkeypatch.setattr("app.api.v1.updates.httpx.get", fake_get)

    resp = client.get("/api/v1/updates/check", headers=_auth(client, session))
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_ok"] is True
    assert body["update_available"] is True
    assert body["latest_version"] == newer.lstrip("v")
    assert body["download_url"] == "https://example.com/FirmRMS-Setup.exe"
    assert body["current_version"] == current


def test_up_to_date_when_release_tag_matches_current(client, session, monkeypatch):
    current = get_app_version()

    def fake_get(url, **kwargs):
        return _FakeResponse({"tag_name": f"v{current}", "html_url": "https://example.com/release", "assets": []})

    monkeypatch.setattr("app.api.v1.updates.httpx.get", fake_get)

    resp = client.get("/api/v1/updates/check", headers=_auth(client, session))
    body = resp.json()
    assert body["checked_ok"] is True
    assert body["update_available"] is False
    assert body["download_url"] is None


def test_up_to_date_when_release_tag_is_older(client, session, monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResponse({"tag_name": "v0.0.1", "html_url": "https://example.com/release", "assets": []})

    monkeypatch.setattr("app.api.v1.updates.httpx.get", fake_get)

    resp = client.get("/api/v1/updates/check", headers=_auth(client, session))
    body = resp.json()
    assert body["update_available"] is False


def test_no_releases_yet_is_reported_distinctly_from_a_network_failure(client, session, monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResponse({}, status_code=404)

    monkeypatch.setattr("app.api.v1.updates.httpx.get", fake_get)

    resp = client.get("/api/v1/updates/check", headers=_auth(client, session))
    body = resp.json()
    assert body["checked_ok"] is False
    assert "no releases" in body["message"].lower()


def test_network_failure_is_reported_gracefully_not_as_a_500(client, session, monkeypatch):
    def fake_get(url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("app.api.v1.updates.httpx.get", fake_get)

    resp = client.get("/api/v1/updates/check", headers=_auth(client, session))
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_ok"] is False
    assert body["update_available"] is False
    assert body["message"]


def test_requires_authentication(client):
    resp = client.get("/api/v1/updates/check")
    assert resp.status_code == 401


def test_health_endpoint_reports_a_version(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["version"] == get_app_version()
