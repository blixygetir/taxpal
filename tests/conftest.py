import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"
sys.path.insert(0, str(ROOT / "scripts"))  # docgen, for building test statements


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXPAL_DB", str(tmp_path / "test.db"))
    from taxpal.api import app
    with TestClient(app) as c:
        yield c


def upload(client, path, name, **form):
    with open(SAMPLE / name, "rb") as f:
        r = client.post(path, files={"file": (name, f, "text/csv")}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def loaded(client):
    upload(client, "/api/import/transactions", "bank_checking.csv", account="checking")
    upload(client, "/api/import/transactions", "credit_card.csv", account="card")
    upload(client, "/api/import/info-returns", "info_returns.csv")
    upload(client, "/api/import/mileage", "mileage.csv")
    client.post("/api/auto-categorize")
    return client
