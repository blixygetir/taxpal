from taxpal import db


def txn(c, text, status="all"):
    return next(t for t in c.get("/api/transactions", params={"status": status, "limit": 1000}).json()
                if text in t["description"])


def feedback_row(txn_id):
    [r] = db.rows("SELECT * FROM feedback WHERE txn_id = ?", (txn_id,))
    return r


def test_correcting_a_rule_is_remembered(loaded):
    sbux = txn(loaded, "STARBUCKS")
    assert sbux["category"] == "meals"
    loaded.post("/api/decisions", json={"txn_id": sbux["txn_id"], "category": "personal"})
    r = feedback_row(sbux["txn_id"])
    assert (r["suggested"], r["suggested_by"], r["answer"], r["kind"]) == ("meals", "rule", "personal", "corrected")


def test_new_answer_and_confirmation(loaded):
    upwork = txn(loaded, "UPWORK", "uncategorized")
    loaded.post("/api/decisions", json={"txn_id": upwork["txn_id"], "category": "contract_labor"})
    assert feedback_row(upwork["txn_id"])["kind"] == "answered"

    apple = txn(loaded, "APPLE STORE", "uncategorized")
    loaded.post("/api/classifications", json=[{"txn_id": apple["txn_id"], "category": "capital_purchase", "confidence": 0.9}])
    loaded.post("/api/confirm", json={"txn_ids": [apple["txn_id"]]})
    assert feedback_row(apple["txn_id"])["kind"] == "confirmed"


def test_model_accuracy_from_reviews(loaded):
    amazon = [t for t in loaded.get("/api/transactions", params={"status": "uncategorized"}).json() if "AMAZON" in t["description"]]
    loaded.post("/api/classifications", json=[{"txn_id": t["txn_id"], "category": "supplies", "confidence": 0.6} for t in amazon])
    loaded.post("/api/confirm", json={"txn_ids": [amazon[0]["txn_id"]]})
    loaded.post("/api/decisions", json={"txn_id": amazon[1]["txn_id"], "category": "personal"})
    f = loaded.get("/api/feedback").json()
    assert f["model_reviewed"] == 2 and f["model_corrected"] == 1 and f["model_accuracy"] == 0.5


def test_examples_find_similar_corrections_first(loaded):
    sbux = txn(loaded, "STARBUCKS")
    loaded.post("/api/decisions", json={"txn_id": sbux["txn_id"], "category": "personal"})
    upwork = txn(loaded, "UPWORK", "uncategorized")
    loaded.post("/api/decisions", json={"txn_id": upwork["txn_id"], "category": "contract_labor"})

    ex = loaded.post("/api/examples", json={"descriptions": ["STARBUCKS STORE 0042"], "k": 3}).json()
    assert ex[0]["description"] == "STARBUCKS STORE 1182"
    assert ex[0]["answer"] == "personal" and ex[0]["kind"] == "corrected" and ex[0]["direction"] == "out"
    # unrelated new answers don't come along, only recent corrections do
    assert all(e["kind"] == "corrected" for e in ex)

    ex = loaded.post("/api/examples", json={"descriptions": ["UPWORK FREELANCER - M. CHEN"]}).json()
    assert ex[0]["answer"] == "contract_labor"


def test_examples_empty_without_feedback(loaded):
    assert loaded.post("/api/examples", json={"descriptions": ["ANYTHING"]}).json() == []


def test_model_can_revise_its_own_guess_only(loaded):
    apple = txn(loaded, "APPLE STORE", "uncategorized")
    post = lambda cat, conf: loaded.post("/api/classifications", json=[
        {"txn_id": apple["txn_id"], "category": cat, "confidence": conf}]).json()
    assert post("supplies", 0.9)["saved"] == 1
    assert [t["txn_id"] for t in loaded.get("/api/transactions", params={"status": "model_guesses"}).json()] == [apple["txn_id"]]
    assert post("capital_purchase", 0.9)["saved"] == 1  # re-check after feedback
    assert txn(loaded, "APPLE STORE")["category"] == "capital_purchase"

    figma = txn(loaded, "FIGMA")  # confident keyword match stays
    assert loaded.post("/api/classifications", json=[
        {"txn_id": figma["txn_id"], "category": "office", "confidence": 0.9}]).json()["saved"] == 0


def test_reset_forgets_feedback(loaded):
    sbux = txn(loaded, "STARBUCKS")
    loaded.post("/api/decisions", json={"txn_id": sbux["txn_id"], "category": "personal"})
    loaded.post("/api/reset")
    assert loaded.get("/api/feedback").json()["total"] == 0
