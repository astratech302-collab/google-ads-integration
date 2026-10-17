from app.models import Report
from app.source import AuthorizationExpired


def test_auth_and_account_isolation(client, connected):
    assert client.get(f"/connections/{connected}/accounts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get(f"/connections/{connected}/accounts", headers={"X-API-Key": "bob-key"}).status_code == 404
    assert client.put(f"/connections/{connected}/accounts", json={"customer_ids": ["9999999999"]}).status_code == 403
    assert client.post(f"/connections/{connected}/reports", json={"customer_id": "2345678901"}).status_code == 403


def test_input_output_exports_and_revoke(client, connected):
    base = f"/connections/{connected}"
    for invalid in [{"customer_id": "123-456-7890"}, {"customer_id": "1234567890", "mutate": True}]:
        assert client.post(base + "/reports", json=invalid).status_code == 422
    assert client.post(base + "/reports", json={"customer_id": "1234567890", "follow_redirects": True}).status_code == 422
    result = client.post(base + "/reports", json={"customer_id": "1234567890"})
    assert result.status_code == 201
    payload = result.json()
    Report.model_validate(payload["report"])
    path = base + "/reports/" + payload["report_id"]
    assert client.get(path).json() == payload["report"]
    csv = client.get(path, params={"format": "csv"})
    assert "configuration_json" in csv.text and csv.headers["content-type"].startswith("text/csv")
    table = client.get(path, params={"format": "table"})
    assert "<table>" in table.text and "Unmapped" in table.text
    assert client.get(path, params={"format": "xml"}).status_code == 422
    assert client.get(path).headers["cache-control"] == "no-store"
    assert client.get(path, headers={"X-API-Key": "bob-key"}).status_code == 404
    assert client.delete(base).json()["local_access_revoked"]
    assert client.get(path).status_code == 401
    with client.app.state.store.db() as db:
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
    assert client.app.state.store.connection(connected, "alice")["refresh_token"] is None


def test_sharing_can_be_withdrawn(client, connected):
    base = f"/connections/{connected}"
    result = client.post(base + "/reports", json={"customer_id": "1234567890"}).json()
    assert client.put(base + "/accounts", json={"customer_ids": ["2345678901"]}).status_code == 200
    assert client.get(base + "/reports/" + result["report_id"]).status_code == 403
    empty = client.post(base + "/reports", json={"customer_id": "2345678901"}).json()["report"]
    assert empty["landing_pages"] == [] and empty["unmapped_ads"] == []


def test_revoked_refresh_token_becomes_reconnect(client, connected, monkeypatch):
    def expired(*args): raise AuthorizationExpired("secret token must not escape")
    monkeypatch.setattr(client.app.state.source, "accounts", expired)
    result = client.get(f"/connections/{connected}/accounts")
    assert result.status_code == 401
    assert "secret" not in result.text
    assert client.app.state.store.connection(connected, "alice")["revoked"]


def test_tokens_and_reports_are_encrypted(client, connected, settings):
    client.post(f"/connections/{connected}/reports", json={"customer_id": "1234567890"})
    raw = settings.database.read_bytes()
    assert b"mock-refresh-token" not in raw and b"Soft Cotton Shirts" not in raw
    assert client.post("/oauth/start").status_code == 409
