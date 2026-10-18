import re

from . import db
from .rules import category, taxonomy

REVIEW_THRESHOLD = 0.8
# per-transaction answers, don't copy these to other txns from the same merchant
PER_TXN_FACTS = {"business_purpose", "recipient"}
FACT_LABELS = {"business_purpose": "business purpose", "business_use_pct": "business-use %", "recipient": "gift recipient"}


def _direction_ok(cat: dict, amount: float) -> bool:
    d = cat.get("direction", "any")
    return d == "any" or (d == "in") == (amount > 0)


def missing_facts(category_key: str, cls: dict) -> list[str]:
    needs = category(category_key).get("needs", [])
    return [n for n in needs if cls.get(n) in (None, "")]


def _upsert(conn, txn_id: int, cls: dict) -> None:
    needs_review = cls["confidence"] < REVIEW_THRESHOLD or bool(missing_facts(cls["category"], cls))
    conn.execute(
        """INSERT INTO classifications (txn_id, category, confidence, source, rationale,
                                        business_purpose, business_use_pct, recipient, needs_review)
           VALUES (:txn_id, :category, :confidence, :source, :rationale,
                   :business_purpose, :business_use_pct, :recipient, :needs_review)
           ON CONFLICT (txn_id) DO UPDATE SET
             category = excluded.category, confidence = excluded.confidence, source = excluded.source,
             rationale = excluded.rationale, business_purpose = excluded.business_purpose,
             business_use_pct = excluded.business_use_pct, recipient = excluded.recipient,
             needs_review = excluded.needs_review""",
        {"txn_id": txn_id, "rationale": None, "business_purpose": None, "business_use_pct": None,
         "recipient": None, **cls, "needs_review": int(needs_review)})


def _remember(conn, t, previous, answer: str) -> None:
    """Keep the user's answer, and what was suggested before it, as an example for the model."""
    if previous is None or previous["source"] == "user":
        suggested = suggested_by = None
        kind = "answered"
    else:
        suggested, suggested_by = previous["category"], previous["source"]
        kind = "confirmed" if suggested == answer else "corrected"
    conn.execute(
        """INSERT INTO feedback (txn_id, merchant, description, amount, suggested, suggested_by, answer, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (txn_id) DO UPDATE SET answer = excluded.answer, created_at = datetime('now'),
             kind = CASE WHEN feedback.suggested IS NULL THEN 'answered'
                         WHEN feedback.suggested = excluded.answer THEN 'confirmed' ELSE 'corrected' END""",
        (t["id"], t["merchant"], t["description"], t["amount"], suggested, suggested_by, answer, kind))


def keyword_match(description: str, amount: float) -> tuple[str, float] | None:
    text = description.upper()
    best = None
    for key, cat in taxonomy().items():
        if not _direction_ok(cat, amount):
            continue
        for kw in cat.get("keywords", []):
            # prefer the longest matching keyword
            if kw.upper() in text and (best is None or len(kw) > best[2]):
                best = (key, cat.get("keyword_confidence", 0.7), len(kw))
    return (best[0], best[1]) if best else None


def auto_categorize() -> dict:
    """Saved answers first, then keywords. Skips anything already classified."""
    counts = {"from_decisions": 0, "from_keywords": 0, "left_for_model": 0}
    with db.connect() as conn:
        decisions = {r["merchant"]: dict(r) for r in conn.execute("SELECT * FROM decisions")}
        pending = conn.execute(
            "SELECT t.* FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id WHERE c.txn_id IS NULL")
        for t in pending.fetchall():
            d = decisions.get(t["merchant"])
            if d and _direction_ok(category(d["category"]), t["amount"]):
                _upsert(conn, t["id"], {"category": d["category"], "confidence": 1.0, "source": "user",
                                        "rationale": "Applied your earlier answer for this merchant",
                                        "business_use_pct": d["business_use_pct"]})
                counts["from_decisions"] += 1
            elif m := keyword_match(t["description"], t["amount"]):
                _upsert(conn, t["id"], {"category": m[0], "confidence": m[1], "source": "rule",
                                        "rationale": "Matched a known merchant keyword"})
                counts["from_keywords"] += 1
            else:
                counts["left_for_model"] += 1
    return counts


def save_model_classifications(items: list[dict]) -> dict:
    """Results from the model in the browser. Doesn't overwrite user answers or confident
    rule matches, but can replace its own earlier guesses. Bad categories (or "income" on
    a purchase etc.) get rejected."""
    saved, rejected = 0, []
    with db.connect() as conn:
        for item in items:
            t = conn.execute("SELECT * FROM transactions WHERE id = ?", (item.get("txn_id"),)).fetchone()
            if not t:
                rejected.append({"txn_id": item.get("txn_id"), "reason": "unknown transaction"})
                continue
            existing = conn.execute("SELECT source, confidence FROM classifications WHERE txn_id = ?",
                                    (t["id"],)).fetchone()
            if existing and (existing["source"] == "user" or
                             (existing["source"] == "rule" and existing["confidence"] >= REVIEW_THRESHOLD)):
                continue
            key = item.get("category")
            if key not in taxonomy():
                rejected.append({"txn_id": t["id"], "reason": f"{key!r} is not a category"})
                continue
            if not _direction_ok(taxonomy()[key], t["amount"]):
                side = "money in" if t["amount"] > 0 else "money out"
                rejected.append({"txn_id": t["id"], "reason": f"{key!r} can't be used for {side}"})
                continue
            confidence = min(max(float(item.get("confidence", 0)), 0.0), 1.0)
            _upsert(conn, t["id"], {"category": key, "confidence": confidence, "source": "llm",
                                    "rationale": str(item.get("rationale", ""))[:300]})
            saved += 1
    return {"saved": saved, "rejected": rejected}


def record_decision(txn_id: int, category_key: str, business_purpose: str | None = None,
                    business_use_pct: float | None = None, recipient: str | None = None,
                    apply_to_merchant: bool = False) -> dict:
    category(category_key)  # validates
    if business_use_pct is not None and not 0 <= business_use_pct <= 100:
        raise ValueError("business_use_pct must be between 0 and 100")
    cls = {"category": category_key, "confidence": 1.0, "source": "user", "rationale": "Your answer",
           "business_purpose": business_purpose, "business_use_pct": business_use_pct, "recipient": recipient}
    applied_to = 0
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if not t:
            raise ValueError(f"Unknown transaction {txn_id}")
        if not _direction_ok(category(category_key), t["amount"]):
            raise ValueError(f"{category(category_key)['label']} can't be used for "
                             f"{'money coming in' if t['amount'] > 0 else 'money going out'}.")
        previous = conn.execute("SELECT category, source FROM classifications WHERE txn_id = ?", (txn_id,)).fetchone()
        _remember(conn, t, previous, category_key)
        _upsert(conn, txn_id, cls)
        if apply_to_merchant:
            conn.execute(
                "INSERT INTO decisions (merchant, category, business_use_pct, business_purpose) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (merchant) DO UPDATE SET category = excluded.category,"
                " business_use_pct = excluded.business_use_pct, decided_at = datetime('now')",
                (t["merchant"], category_key, business_use_pct, business_purpose))
            shared = {k: v for k, v in cls.items() if k not in PER_TXN_FACTS}
            shared["rationale"] = "Applied your answer for this merchant"
            others = conn.execute(
                "SELECT t.id, t.amount FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id"
                " WHERE t.merchant = ? AND t.id != ? AND (c.source IS NULL OR c.source != 'user')",
                (t["merchant"], txn_id)).fetchall()
            for o in others:
                if _direction_ok(category(category_key), o["amount"]):
                    _upsert(conn, o["id"], shared)
                    applied_to += 1
    return {"txn_id": txn_id, "still_missing": missing_facts(category_key, cls),
            "applied_to_other_transactions": applied_to}


def confirm_classifications(txn_ids: list[int]) -> dict:
    """The user looked at these and agrees with the category. Missing facts still keep
    an item in the review queue."""
    with db.connect() as conn:
        found = {r["txn_id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM classifications WHERE txn_id IN ({','.join('?' * len(txn_ids))})", txn_ids)}
        missing = [i for i in txn_ids if i not in found]
        if missing:
            raise ValueError(f"Not categorized yet: {missing}")
        confirmed = 0
        for txn_id, c in found.items():
            if c["source"] == "user":
                continue
            was = "model" if c["source"] == "llm" else "keyword rule"
            t = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
            _remember(conn, t, c, c["category"])
            cls = {k: c[k] for k in ("category", "business_purpose", "business_use_pct", "recipient")}
            cls.update(confidence=1.0, source="user",
                       rationale=f"You confirmed the {was}'s choice: {c['rationale'] or 'no reason given'}"[:300])
            _upsert(conn, txn_id, cls)
            confirmed += 1
    return {"confirmed": confirmed, "still_needs_review": [r["txn_id"] for r in db.rows(
        f"SELECT txn_id FROM classifications WHERE needs_review = 1 AND txn_id IN ({','.join('?' * len(txn_ids))})", txn_ids)]}


def _words(text: str) -> set[str]:
    # drop store numbers, card refs and other noise
    return {w for w in re.findall(r"[A-Z]{3,}", text.upper())
            if w not in {"THE", "AND", "POS", "PURCHASE", "PAYMENT", "DEBIT", "CARD", "ONLINE", "INC", "LLC"}}


def examples_for(descriptions: list[str], k: int = 6) -> list[dict]:
    """Past user answers most like these transactions, for the model's prompt. Word overlap
    on the description is plenty here and costs nothing; corrections rank first because
    they're where the model or the rules went wrong before."""
    rows = db.rows("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 500")
    targets = [_words(d) for d in descriptions]
    scored = []
    for r in rows:
        words = _words(r["description"])
        overlap = max((len(words & t) / (len(words | t) or 1) for t in targets), default=0)
        if overlap > 0:
            scored.append((overlap + (0.5 if r["kind"] == "corrected" else 0), r))
    picked = [r for _, r in sorted(scored, key=lambda x: -x[0])]
    # a couple of recent corrections even if unrelated, they show what the user cares about
    picked += [r for r in rows if r["kind"] == "corrected" and r not in picked][:2]
    seen, out = set(), []
    for r in picked:
        key = (r["merchant"], r["answer"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"description": r["description"], "direction": "in" if r["amount"] > 0 else "out",
                    "answer": r["answer"], "suggested": r["suggested"], "kind": r["kind"]})
        if len(out) == k:
            break
    return out


def feedback_stats() -> dict:
    [s] = db.rows("""SELECT COUNT(*) AS total, SUM(kind = 'corrected') AS corrected,
                            SUM(kind = 'confirmed') AS confirmed, SUM(kind = 'answered') AS answered,
                            SUM(kind = 'corrected' AND suggested_by = 'llm') AS model_corrected,
                            SUM(suggested_by = 'llm') AS model_reviewed
                     FROM feedback""")
    s = {k: v or 0 for k, v in s.items()}
    s["model_accuracy"] = round(1 - s["model_corrected"] / s["model_reviewed"], 3) if s["model_reviewed"] else None
    return s


def review_queue(limit: int = 50) -> list[dict]:
    out = []
    for r in db.rows(
            """SELECT t.id AS txn_id, t.date, t.description, t.amount, t.account, c.category, c.confidence,
                      c.source, c.rationale, c.business_purpose, c.business_use_pct, c.recipient
               FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id
               WHERE c.txn_id IS NULL OR c.needs_review = 1
               ORDER BY ABS(t.amount) DESC LIMIT ?""", (limit,)):
        if r["category"] is None:
            r["reason"] = "Not categorized yet."
        else:
            missing = missing_facts(r["category"], r)
            r["reason"] = (f"Missing the {' and '.join(FACT_LABELS.get(m, m) for m in missing)}." if missing
                           else "Not sure. Please confirm.")
        out.append(r)
    return out
