import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.source import MockSource
from app.urls import URLRules


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database=tmp_path / "db.sqlite3", api_keys={"alice": "alice-key", "bob": "bob-key"}, min_request_interval=0)


@pytest.fixture
def snapshot(settings):
    source = MockSource(settings)
    return source.snapshot("mock", source.accounts("mock")[1])


@pytest.fixture
def rules(settings):
    return URLRules.load(settings.normalization_file)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        client.headers["X-API-Key"] = "alice-key"
        yield client


@pytest.fixture
def connected(client):
    ident = client.post("/connections/mock").json()["connection_id"]
    assert client.get(f"/connections/{ident}/accounts").status_code == 200
    assert client.put(f"/connections/{ident}/accounts", json={"customer_ids": ["1234567890"]}).status_code == 200
    return ident
