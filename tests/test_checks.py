import csv

import pytest

from taxpal import checks, db
from conftest import SAMPLE


def check_ids(c):
    return {x["id"] for x in c.get("/api/checks").json()["checks"]}


def open_blockers(c):
    return [x["id"] for x in c.get("/api/checks").json()["checks"] if x["severity"] == "blocker" and not x["acknowledged"]]


def labels():
    with open(SAMPLE / "labels.csv") as f:
        return {(r["date"], r["description"]): r["expected_category"] for r in csv.DictReader(f)}


def answer_everything(c):
    """Play the user: answer every review item with the right category and the facts it needs."""
    truth = labels()
    tax = c.get("/api/taxonomy").json()
    for t in c.get("/api/transactions", params={"status": "needs_review", "limit": 1000}).json():
        cat = truth[(t["date"], t["description"])]
        needs = tax[cat]["needs"] or []
        r = c.post("/api/decisions", json={
            "txn_id": t["txn_id"], "category": cat,
            "business_purpose": "Client meeting about the Q3 project" if "business_purpose" in needs else None,
            "business_use_pct": 60 if "business_use_pct" in needs else None,
            "recipient": "Dana at Acme" if "recipient" in needs else None})
        assert r.status_code == 200, r.text


def test_fresh_import_cannot_be_signed_off(loaded):
    ids = check_ids(loaded)
    stripe = next(r for r in db.rows("SELECT id, payer FROM info_returns") if r["payer"].startswith("Stripe"))
    assert {"needs_review", "spot_check", f"reconcile:{stripe['id']}", "income_below_1099"} <= ids
    assert not any(i.startswith("math:") for i in ids)
    r = loaded.post("/api/signoff", json={"confirm": True})
    assert r.status_code == 400 and "open blocker" in r.json()["detail"]
    assert loaded.post("/api/signoff", json={"confirm": False}).status_code == 422


def test_model_guess_needs_a_human_click(loaded):
    t = next(t for t in loaded.get("/api/transactions", params={"status": "uncategorized"}).json()
             if "APPLE STORE" in t["description"])
    loaded.post("/api/classifications", json=[{"txn_id": t["txn_id"], "category": "capital_purchase",
                                               "confidence": 0.95, "rationale": "laptop"}])
    model_check = next(x for x in loaded.get("/api/checks").json()["checks"] if x["id"] == "model_unconfirmed")
    assert [i["txn_id"] for i in model_check["items"]] == [t["txn_id"]]

    assert loaded.post("/api/confirm", json={"txn_ids": [t["txn_id"]]}).json()["confirmed"] == 1
    assert "model_unconfirmed" not in check_ids(loaded)
    [row] = db.rows("SELECT source, rationale FROM classifications WHERE txn_id = ?", (t["txn_id"],))
    assert row["source"] == "user" and "model's choice" in row["rationale"]
    assert loaded.post("/api/confirm", json={"txn_ids": [999999]}).status_code == 400


def test_confirm_keeps_missing_facts_flagged(loaded):
    verizon = next(t for t in loaded.get("/api/transactions", params={"status": "needs_review"}).json()
                   if "VERIZON" in t["description"])
    r = loaded.post("/api/confirm", json={"txn_ids": [verizon["txn_id"]]}).json()
    assert r["still_needs_review"] == [verizon["txn_id"]]  # still no business-use %


def test_resolve_everything_then_sign_off(loaded):
    answer_everything(loaded)
    stripe = next(x for x in loaded.get("/api/checks").json()["checks"] if x.get("suggested_adjustment"))
    for a in stripe["suggested_adjustment"]["adjustments"]:
        loaded.post("/api/adjustments", json=a)
    # these two can only be explained: the savings account isn't imported, and receipts live outside TaxPal
    left = open_blockers(loaded)
    assert sorted(i.split(":")[0] for i in left) == ["receipts", "untraced_deposit"]
    for check_id in left:
        r = loaded.post(f"/api/checks/{check_id}/acknowledge", json={"note": "Savings account at Ally; receipts in the 2026 folder"})
        assert r.status_code == 200, r.text
    assert open_blockers(loaded) == []

    s = loaded.post("/api/signoff", json={"confirm": True}).json()
    assert s["current"] is True
    assert "Reviewed and signed off" in loaded.get("/api/export/summary.md").text

    # any later change sends it back to draft
    loaded.put("/api/profile", json={"home_office_sqft": 120})
    assert loaded.get("/api/signoff").json()["current"] is False
    assert "DRAFT: data changed after you signed off" in loaded.get("/api/export/summary.md").text


def test_explaining_instead_of_fixing(loaded):
    answer_everything(loaded)
    stripe = next(x for x in loaded.get("/api/checks").json()["checks"] if x.get("suggested_adjustment"))
    assert loaded.post("/api/checks/needs_review/acknowledge", json={"note": "it is fine, trust me"}).status_code == 400
    assert loaded.post(f"/api/checks/{stripe['id']}/acknowledge", json={"note": "ok"}).status_code == 400

    note = "My accountant is handling the Stripe fees separately"
    assert loaded.post(f"/api/checks/{stripe['id']}/acknowledge", json={"note": note}).status_code == 200
    assert stripe["id"] not in open_blockers(loaded)
    md = loaded.get("/api/export/summary.md").text
    assert "Explanations you gave" in md and note in md


def test_reconcile_sees_applied_adjustment(loaded):
    stripe = lambda: next(r for r in loaded.get("/api/reconcile").json()["info_returns"] if r["form"] == "1099-K")
    suggested = stripe()["suggested_adjustment"]
    for a in suggested["adjustments"]:
        loaded.post("/api/adjustments", json=a)
    assert "suggested_adjustment" not in stripe() and stripe()["adjustments_applied"] == pytest.approx(685.40)
    for a in suggested["adjustments"]:  # applied twice by mistake
        loaded.post("/api/adjustments", json=a)
    assert any("applied twice" in w for w in stripe()["warnings"])


def test_math_checks_catch_a_bad_total():
    sc = {"lines": {"1": {"amount": 100.0}, "18": {"amount": 30.0}}, "gross_income": 100.0,
          "total_expenses": 40.0, "tentative_profit": 60.0, "home_office": 0.0, "net_profit": 60.0}
    txns = [{"category": "income", "amount": 100.0}, {"category": "office", "amount": -30.0}]
    assert [k for k, _ in checks._math_problems(sc, txns, [])] == ["expense_lines"]
    sc.update(total_expenses=30.0, tentative_profit=70.0, net_profit=70.0)
    assert checks._math_problems(sc, txns, []) == []


def test_agent_cannot_sign_off(client):
    tools = {t["function"]["name"]: t for t in client.get("/api/tools").json()}
    assert not any("signoff" in t["x-http"]["path"] for t in tools.values())
    assert tools["record_decision"]["x-confirm"] == {"when": "apply_to_merchant"}
    assert tools["explain_check"]["x-confirm"] is True
