import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.ads.googleads.v23.services.types.google_ads_service import GoogleAdsRow
from google.api_core.exceptions import ServiceUnavailable
from google.auth.exceptions import RefreshError

from app.config import Settings
from app.main import create_app
from app.source import AuthorizationExpired, GoogleSource, RetryableSourceError, retry


def test_page_retry_resumes_without_duplicates(settings):
    source = GoogleSource(settings)
    attempts = []
    class Service:
        def search(self, request, **kwargs):
            token = request["page_token"]
            attempts.append(token)
            if token == "next" and attempts.count("next") == 1: raise ServiceUnavailable("private API error")
            page = SimpleNamespace(results=[GoogleAdsRow(customer={"id": "1234567890" if not token else "2345678901"})], next_page_token="next" if not token else "")
            return SimpleNamespace(pages=iter([page]))
    client = SimpleNamespace(get_service=lambda name: Service())
    # Keep backoff deterministic and fast while still testing source.call classification.
    import app.source as module
    original_retry = module.retry
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(module, "retry", lambda operation, count: original_retry(operation, count, sleep=lambda _: None))
        result = source.search(client, "1234567890", "SELECT customer.id FROM customer")
    assert [row["customer"]["id"] for row in result] == ["1234567890", "2345678901"]
    assert attempts == ["", "next", "next"]


def test_metadata_pagination_selectable_filter(settings):
    source = GoogleSource(settings)
    calls = []
    class Service:
        def search_google_ads_fields(self, request, **kwargs):
            calls.append(request)
            first = not request["page_token"]
            row = SimpleNamespace(name="ad_group_ad.ad.id" if first else "ad_group_ad.status", selectable=True)
            return SimpleNamespace(pages=iter([SimpleNamespace(results=[row, SimpleNamespace(name="other.secret", selectable=True)], next_page_token="next" if first else "")]))
    fields = source.fields(SimpleNamespace(get_service=lambda name: Service()), "ad_group_ad")
    assert fields == ["ad_group_ad.ad.id", "ad_group_ad.status"]
    assert len(calls) == 2


def test_backoff_is_bounded():
    delays = []
    def fail(): raise RetryableSourceError("busy")
    with pytest.raises(RetryableSourceError): retry(fail, 3, sleep=delays.append)
    assert len(delays) == 3
    assert all(low <= actual < low + .26 for actual, low in zip(delays, [1, 2, 4]))


def test_eager_refresh_error_is_sanitized(settings, monkeypatch):
    source = GoogleSource(settings)
    def load(*a, **kw): raise RefreshError("invalid_grant secret-token")
    monkeypatch.setattr("app.source.GoogleAdsClient.load_from_dict", load)
    with pytest.raises(AuthorizationExpired, match="reconnect") as exc: source.client("secret-token")
    assert "secret-token" not in str(exc.value)


def live_settings(tmp_path):
    return Settings(_env_file=None, mode="live", database=tmp_path / "live.db", encryption_key=Fernet.generate_key().decode(), api_keys={"alice": "a" * 40}, client_id="demo.apps.googleusercontent.com", client_secret="demo-secret")


def test_oauth_pkce_state_replay_and_token_storage(tmp_path, monkeypatch):
    settings = live_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers["X-API-Key"] = "a" * 40
        result = client.post("/oauth/start").json()
        params = parse_qs(urlsplit(result["authorization_url"]).query)
        assert params["scope"] == ["https://www.googleapis.com/auth/adwords"]
        state = params["state"][0]
        original_client = httpx.Client
        def exchange(request):
            body = parse_qs(request.content.decode())
            challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest()).decode().rstrip("=")
            assert challenge == params["code_challenge"][0]
            return httpx.Response(200, json={"refresh_token": "secret-test-refresh", "access_token": "secret-access"})
        monkeypatch.setattr("app.main.httpx.Client", lambda **kw: original_client(transport=httpx.MockTransport(exchange), **kw))
        response = client.get("/oauth/callback", params={"state": state, "code": "synthetic-code"})
        assert response.status_code == 200
        assert "secret" not in response.text
        connection_id = response.json()["connection_id"]
        assert app.state.store.connection(connection_id, "alice")["refresh_token"] == "secret-test-refresh"
        assert b"secret-test-refresh" not in settings.database.read_bytes()
        assert client.get("/oauth/callback", params={"state": state, "code": "synthetic-code"}).status_code == 400
        assert client.post("/connections/mock").status_code == 404


def test_oauth_denial_and_expiry(tmp_path):
    app = create_app(live_settings(tmp_path))
    with TestClient(app) as client:
        client.headers["X-API-Key"] = "a" * 40
        state = parse_qs(urlsplit(client.post("/oauth/start").json()["authorization_url"]).query)["state"][0]
        assert client.get("/oauth/callback", params={"state": state, "error": "access_denied"}).status_code == 400
        state = app.state.store.new_state("alice", "verifier")
        with app.state.store.db() as db: db.execute("UPDATE states SET expires=0")
        assert client.get("/oauth/callback", params={"state": state, "code": "expired"}).status_code == 400


def test_revocation_cannot_be_overwritten(client, connected):
    store = client.app.state.store
    old = store.connection(connected, "alice")
    store.revoke(connected, "alice")
    assert not store.update(connected, "alice", old)
    assert store.save_report(connected, "alice", {"customer_id": "1234567890"}) is None
    assert store.connection(connected, "alice")["revoked"]
