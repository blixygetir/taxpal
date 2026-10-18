# Search over the IRS pubs. BM25 always works; if the browser has built embeddings we
# also rank by cosine and merge the two lists with reciprocal rank fusion.
# (the server never runs a model, it just stores the vectors.)

import hashlib
import json
import math
import re
from array import array
from collections import Counter
from functools import cache

from . import db
from .rules import RULES_DIR, irs_documents

CORPUS_PATH = RULES_DIR / "irs_corpus.jsonl"
RRF_K = 60
BM25_K1, BM25_B = 1.2, 0.75
STOPWORDS = set("a an and are as at be by can for from has have if in is it its may not of on or that the this to was were will with you your".split())


# everyday words -> what the IRS docs actually say. only applied to queries
SYNONYMS = {
    "lunch": "meal", "dinner": "meal", "breakfast": "meal", "coffee": "meal", "restaurant": "meal", "food": "meal",
    "laptop": "computer", "phone": "telephone", "cellphone": "telephone", "receipt": "record",
    "car": "vehicle", "auto": "vehicle", "truck": "vehicle", "gas": "fuel", "fuel": "gasoline",
    "flight": "travel", "hotel": "lodging", "trip": "travel", "freelancer": "self-employed", "freelance": "self-employed",
    "office": "home", "quarterly": "estimated", "401k": "401(k)", "sep": "sep",
}


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str, expand: bool = False) -> list[str]:
    tokens = [_stem(t) for t in re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*", text.lower()) if t not in STOPWORDS]
    if expand:
        tokens += [SYNONYMS[t] for t in tokens if t in SYNONYMS]
    return tokens


@cache
def corpus() -> tuple[list[dict], str]:
    raw = CORPUS_PATH.read_bytes()
    chunks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return chunks, hashlib.sha256(raw).hexdigest()[:12]


def embed_text(chunk: dict) -> str:
    return f"{chunk['section']}\n{chunk['text']}"


@cache
def _bm25_index():
    chunks, _ = corpus()
    docs = [Counter(tokenize(c["section"] + " " + c["text"])) for c in chunks]
    lengths = [sum(d.values()) for d in docs]
    df = Counter(t for d in docs for t in d)
    n = len(docs)
    idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
    return docs, lengths, sum(lengths) / n, idf


def bm25(query: str) -> list[tuple[int, float]]:
    docs, lengths, avg, idf = _bm25_index()
    terms = tokenize(query, expand=True)
    scores = []
    for i, d in enumerate(docs):
        s = 0.0
        for t in terms:
            f = d.get(t)
            if f:
                s += idf[t] * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * lengths[i] / avg))
        if s > 0:
            scores.append((i, s))
    return sorted(scores, key=lambda x: -x[1])
# (db path, model) -> (corpus_version, {chunk_id: vector})
_vectors: dict[tuple[str, str], tuple[str, dict[str, array]]] = {}


def _load_vectors(model: str) -> dict[str, array]:
    _, version = corpus()
    key = (str(db.db_path()), model)
    cached = _vectors.get(key)
    if cached and cached[0] == version:
        return cached[1]
    vecs = {}
    for r in db.rows("SELECT chunk_id, vector FROM irs_embeddings WHERE model = ? AND corpus_version = ?", (model, version)):
        v = array("f")
        v.frombytes(r["vector"])
        vecs[r["chunk_id"]] = v
    _vectors[key] = (version, vecs)
    return vecs


def _normalize(v) -> array:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return array("f", (x / norm for x in v))


def store_embeddings(model: str, corpus_version: str, items: list[dict]) -> dict:
    chunks, version = corpus()
    if corpus_version != version:
        raise ValueError("The IRS passages have changed since the index was built. Rebuild the index.")
    known = {c["id"] for c in chunks}
    existing = _load_vectors(model)
    dim = len(next(iter(existing.values()))) if existing else None
    rows = []
    for it in items:
        if it["id"] not in known:
            raise ValueError(f"Unknown chunk {it['id']!r}")
        vec = it["vector"]
        if not vec or any(not math.isfinite(x) for x in vec):
            raise ValueError(f"Invalid vector for {it['id']}")
        dim = dim or len(vec)
        if len(vec) != dim:
            raise ValueError(f"Vector for {it['id']} has {len(vec)} dims, expected {dim}")
        rows.append((it["id"], model, version, _normalize(vec).tobytes()))
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO irs_embeddings (chunk_id, model, corpus_version, vector) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (chunk_id, model) DO UPDATE SET corpus_version = excluded.corpus_version, vector = excluded.vector",
            rows)
    _vectors.pop((str(db.db_path()), model), None)
    return {"stored": len(rows), **status(model)}


def status(model: str | None = None) -> dict:
    chunks, version = corpus()
    out = {"chunks": len(chunks), "corpus_version": version,
           "documents": sorted({(c["doc"], c["doc_title"]) for c in chunks})}
    if model:
        out["model"] = model
        out["embedded"] = len(_load_vectors(model))
    return out


def documents() -> list[dict]:
    """The IRS publications and instructions TaxPal searches, with links to irs.gov."""
    docs: dict[str, dict] = {}
    for c in corpus()[0]:
        d = docs.setdefault(c["doc"], {"doc": c["doc"], "title": c["doc_title"], "revision": c["revision"],
                                       "url": c["url"].split("#")[0], "passages": 0})
        d["passages"] += 1
    about = irs_documents()
    for d in docs.values():
        d.update(about.get(d["doc"], {}))
    return sorted(docs.values(), key=lambda d: (not d["doc"].startswith("i"), d["doc"]))


def chunks_to_embed(model: str, limit: int = 64) -> list[dict]:
    chunks, _ = corpus()
    done = _load_vectors(model)
    return [{"id": c["id"], "text": embed_text(c)} for c in chunks if c["id"] not in done][:limit]


def semantic(vector: list[float], model: str) -> list[tuple[int, float]]:
    vecs = _load_vectors(model)
    if not vecs:
        return []
    q = _normalize(vector)
    chunks, _ = corpus()
    scored = []
    for i, c in enumerate(chunks):
        v = vecs.get(c["id"])
        if v is not None and len(v) == len(q):
            scored.append((i, sum(a * b for a, b in zip(q, v))))
    return sorted(scored, key=lambda x: -x[1])


def search(query: str, k: int = 5, vector: list[float] | None = None, model: str | None = None,
           docs: list[str] | None = None) -> dict:
    chunks, version = corpus()
    allowed = (lambda i: chunks[i]["doc"] in docs) if docs else (lambda i: True)
    keyword = [i for i, _ in bm25(query) if allowed(i)][:50]
    sem = [i for i, _ in semantic(vector, model) if allowed(i)][:50] if vector and model else []

    fused: dict[int, float] = {}
    for ranking in (keyword, sem):
        for rank, i in enumerate(ranking):
            fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank + 1)
    kw_set, sem_set = set(keyword), set(sem)
    hits = []
    for i, score in sorted(fused.items(), key=lambda x: -x[1])[:k]:
        c = chunks[i]
        hits.append({
            "citation": f"{c['doc_title']}, {c['section']}",
            "url": c["url"],
            "revision": c["revision"],
            "text": c["text"],
            "match": "both" if i in kw_set and i in sem_set else ("semantic" if i in sem_set else "keyword"),
            "score": round(score, 5),
        })
    return {
        "query": query,
        "mode": "hybrid" if sem else "keyword",
        "corpus_version": version,
        "hits": hits,
        "note": "Quoted IRS text may be from the 2025 revision. For 2026 dollar amounts, rely on TaxPal's "
                "ty2026 values (compute tools), which cite the 2026 source.",
    }
