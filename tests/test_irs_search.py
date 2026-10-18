import random

import pytest
from fastapi.testclient import TestClient

from taxpal import irs_search

MODEL = "test-embed"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXPAL_DB", str(tmp_path / "test.db"))
    from taxpal.api import app
    with TestClient(app) as c:
        yield c


def test_corpus_is_well_formed():
    chunks, version = irs_search.corpus()
    assert len(chunks) > 1000 and len(version) == 12
    assert len({c["id"] for c in chunks}) == len(chunks)
    assert all(c["url"].startswith("https://www.irs.gov/") for c in chunks)
    assert all(len(c["text"].split()) <= 220 for c in chunks)
    assert {c["doc"] for c in chunks} >= {"i1040sc", "i1040sse", "i8995", "p334", "p463", "p505", "p583", "p587"}


@pytest.mark.parametrize("query, doc, section_word", [
    ("can I deduct a lunch with a client", "p463", "Meals"),
    ("standard mileage rate parking tolls", "p463", "Mileage"),
    ("how long to keep records", None, "Records"),
    ("when are estimated tax payments due", "p505", "Estimated Tax"),
])
def test_keyword_search_finds_the_right_publication(query, doc, section_word):
    hits = irs_search.search(query, 3)["hits"]
    assert any((doc is None or h["url"].split("/")[-1].startswith(doc)) and section_word.lower() in h["citation"].lower()
               for h in hits), [h["citation"] for h in hits]


def test_search_filters_by_document():
    hits = irs_search.search("deduct meals", 5, docs=["i1040sc"])["hits"]
    assert hits and all("Schedule C" in h["citation"] for h in hits)


def fake_vector(seed: int, dim: int = 16) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(dim)]


def test_embeddings_roundtrip_and_hybrid_search(client):
    status = client.get("/api/irs/status", params={"model": MODEL}).json()
    assert status["embedded"] == 0
    todo = client.get("/api/irs/chunks", params={"model": MODEL, "limit": 40}).json()
    assert len(todo) == 40 and "\n" in todo[0]["text"]  # section heading + body

    items = [{"id": c["id"], "vector": fake_vector(i)} for i, c in enumerate(todo)]
    r = client.post("/api/irs/embeddings", json={"model": MODEL, "corpus_version": status["corpus_version"], "items": items})
    assert r.status_code == 200 and r.json()["embedded"] == 40
    assert todo[0]["id"] not in {c["id"] for c in client.get("/api/irs/chunks", params={"model": MODEL}).json()}

    # A query vector identical to chunk 7's vector must surface chunk 7 via semantic search,
    # even for a query with no keyword overlap.
    target = todo[7]["id"]
    res = client.post("/api/irs/search", json={"query": "zzqx", "k": 3, "vector": fake_vector(7), "model": MODEL}).json()
    assert res["mode"] == "hybrid"
    chunks = {c["id"]: c for c in irs_search.corpus()[0]}
    assert res["hits"][0]["text"] == chunks[target]["text"]
    assert res["hits"][0]["match"] == "semantic"


def test_embeddings_validation(client):
    version = client.get("/api/irs/status").json()["corpus_version"]
    todo = client.get("/api/irs/chunks", params={"model": MODEL, "limit": 2}).json()
    ok = {"id": todo[0]["id"], "vector": fake_vector(1)}
    assert client.post("/api/irs/embeddings", json={"model": MODEL, "corpus_version": "stale", "items": [ok]}).status_code == 400
    assert client.post("/api/irs/embeddings", json={"model": MODEL, "corpus_version": version,
                                                    "items": [{"id": "nope", "vector": fake_vector(1)}]}).status_code == 400
    assert client.post("/api/irs/embeddings", json={"model": MODEL, "corpus_version": version, "items": [ok]}).status_code == 200
    wrong_dim = {"id": todo[1]["id"], "vector": fake_vector(2, dim=32)}
    assert client.post("/api/irs/embeddings", json={"model": MODEL, "corpus_version": version, "items": [wrong_dim]}).status_code == 400


def test_search_endpoint_keyword_mode_and_tool(client):
    res = client.post("/api/irs/search", json={"query": "business use of home exclusive use"}).json()
    assert res["mode"] == "keyword" and res["hits"][0]["url"].startswith("https://www.irs.gov/")
    tools = {t["function"]["name"]: t for t in client.get("/api/tools").json()}
    assert tools["search_irs_guidance"]["x-http"] == {"method": "POST", "path": "/api/irs/search"}


def test_documents_list(client):
    docs = client.get("/api/irs/documents").json()
    assert docs and all(d["url"].startswith("https://www.irs.gov/") and "#" not in d["url"] for d in docs)
    assert any(d["doc"] == "p463" for d in docs) and sum(d["passages"] for d in docs) > 1000
    assert all(d.get("summary") and d.get("key_points") and d.get("used_for") for d in docs)  # every document is summarized
