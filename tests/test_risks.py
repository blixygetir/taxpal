"""Each test sets up one thing that's easy to get wrong and checks that sign-off is blocked
until it's fixed or explained."""

import io

from taxpal import db


def import_csv(c, text, account, **form):
    r = c.post("/api/import/transactions", files={"file": ("t.csv", io.BytesIO(text.encode()), "text/csv")},
               data={"account": account, **form})
    assert r.status_code == 200, r.text
    c.post("/api/auto-categorize")
    return r.json()


def checks(c, prefix):
    return [x for x in c.get("/api/checks").json()["checks"] if x["id"].startswith(prefix)]


def txn(text):
    return next(r for r in db.rows("SELECT t.id, t.amount, c.category FROM transactions t"
                                   " LEFT JOIN classifications c ON c.txn_id = t.id WHERE t.description LIKE ?", (f"%{text}%",)))


def decide(c, text, category):
    r = c.post("/api/decisions", json={"txn_id": txn(text)["id"], "category": category})
    assert r.status_code == 200, r.text


CARD_CHARGES_POSITIVE = "Date,Description,Amount\n" + "\n".join(
    f"2026-{m:02d}-04,ADOBE *CREATIVE CLOUD,59.99\n2026-{m:02d}-09,STAPLES 00{m},{20 + m}.50" for m in range(1, 7))


def test_flipped_signs_are_caught_and_cleared_by_reimport(client):
    import_csv(client, CARD_CHARGES_POSITIVE, "visa")
    [flip] = checks(client, "signs_flipped:visa")
    assert "re-import" in flip["message"] and len(flip["items"]) >= 3

    client.post("/api/reset")
    import_csv(client, CARD_CHARGES_POSITIVE, "visa", purchases_positive="true")
    assert checks(client, "signs_flipped") == []


def test_card_payment_counted_as_expense(loaded):
    decide(loaded, "AUTOPAY PAYMENT CREDIT CARD", "office")
    [hit] = checks(loaded, "transfer_counted")
    assert "counts them twice" in hit["message"]
    decide(loaded, "AUTOPAY PAYMENT CREDIT CARD", "transfer")
    assert checks(loaded, "transfer_counted") == []


def test_same_purchase_in_two_exports(loaded):
    import_csv(loaded, "Date,Description,Amount\n2026-01-05,PAYPAL *ADOBE CREATIVE,-59.99\n", "paypal")
    [dup] = checks(loaded, "duplicate")
    assert {i["account"] for i in dup["items"]} == {"paypal", "card"}
    decide(loaded, "PAYPAL *ADOBE", "transfer")
    assert checks(loaded, "duplicate") == []


def test_income_hidden_as_transfer_or_refund(loaded):
    # the sample's $3,000 from an un-imported savings account
    [savings] = checks(loaded, "untraced_deposit")
    assert "SAVINGS" in savings["message"]

    # a "refund" from a merchant we never paid
    import_csv(loaded, "Date,Description,Amount\n2026-05-02,BIGCO MEDIA REFUND,850.00\n", "checking2")
    assert txn("BIGCO")["category"] == "refund"
    assert len(checks(loaded, "untraced_deposit")) == 2
    # the Adobe refund matches earlier Adobe charges, the card payments match the autopays
    assert not any("ADOBE" in c["message"] or "PAYMENT THANK YOU" in c["message"] for c in checks(loaded, "untraced_deposit"))

    decide(loaded, "BIGCO MEDIA REFUND", "income")
    assert len(checks(loaded, "untraced_deposit")) == 1


def test_home_rent_deducted_with_home_office(loaded):
    loaded.put("/api/profile", json={"home_office_sqft": 150})
    rent = db.rows("SELECT id FROM transactions WHERE description LIKE 'RENT PAYMENT%' ORDER BY date LIMIT 1")[0]["id"]
    loaded.post("/api/decisions", json={"txn_id": rent, "category": "rent"})
    [home] = checks(loaded, "home_expense")
    assert "counts them twice" in home["message"] and "Pub 587" in home["message"]


def test_big_item_expensed_instead_of_depreciated(loaded):
    import_csv(loaded, "Date,Description,Amount\n2026-02-11,DELL TECHNOLOGIES WORKSTATION,-3200.00\n", "checking2")
    decide(loaded, "DELL TECHNOLOGIES", "supplies")
    [big] = checks(loaded, "over_de_minimis")
    assert "$2,500" in big["message"]
    decide(loaded, "DELL TECHNOLOGIES", "capital_purchase")
    assert checks(loaded, "over_de_minimis") == []


def test_receipts_check_reopens_when_a_new_expense_appears(loaded):
    [before] = checks(loaded, "receipts")
    note = {"note": "Receipts saved in Google Drive / Taxes 2026"}
    assert loaded.post(f"/api/checks/{before['id']}/acknowledge", json=note).status_code == 200
    assert checks(loaded, "receipts")[0]["acknowledged"]

    import_csv(loaded, "Date,Description,Amount\n2026-11-20,HILTON CHICAGO,-61.00\n", "checking2")
    [after] = checks(loaded, "receipts")
    assert after["id"] != before["id"] and after["acknowledged"] is None  # lodging, so even under $75
    assert any("HILTON" in i["description"] for i in after["items"])


def test_every_risk_blocks_sign_off_until_explained(loaded):
    decide(loaded, "AUTOPAY PAYMENT CREDIT CARD", "office")
    ids = [c["id"] for c in loaded.get("/api/checks").json()["checks"] if c["severity"] == "blocker"]
    assert any(i.startswith("transfer_counted") for i in ids)
    r = loaded.post("/api/signoff", json={"confirm": True})
    assert r.status_code == 400 and "open blocker" in r.json()["detail"]
