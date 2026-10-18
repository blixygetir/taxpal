import pytest

from conftest import upload


def test_import_is_idempotent(client):
    first = upload(client, "/api/import/transactions", "bank_checking.csv")
    again = upload(client, "/api/import/transactions", "bank_checking.csv")
    assert first["imported"] == 113 and again == {"imported": 0, "duplicates_skipped": 113, "outside_tax_year_skipped": 0}


def test_card_signs_are_outflows(loaded):
    rows = loaded.get("/api/transactions").json()
    figma = next(r for r in rows if r["description"] == "FIGMA MONTHLY")
    assert figma["amount"] == -15 and figma["category"] == "software"


def test_only_last4_of_tin_stored(loaded):
    from taxpal import db
    assert {r["payer_tin_last4"] for r in db.rows("SELECT payer_tin_last4 FROM info_returns")} == {"4521", "7710", "0943"}


def test_model_output_is_validated(loaded):
    queue = loaded.get("/api/transactions", params={"status": "uncategorized"}).json()
    apple = next(r for r in queue if "APPLE STORE" in r["description"])
    r = loaded.post("/api/classifications", json=[
        {"txn_id": apple["txn_id"], "category": "income", "confidence": 0.9},       # purchase can't be income
        {"txn_id": apple["txn_id"], "category": "not_a_category", "confidence": 0.9},
    ]).json()
    assert r["saved"] == 0 and len(r["rejected"]) == 2
    r = loaded.post("/api/classifications", json=[
        {"txn_id": apple["txn_id"], "category": "capital_purchase", "confidence": 0.95, "rationale": "laptop"}]).json()
    assert r["saved"] == 1


def test_decision_applies_to_merchant(loaded):
    queue = loaded.get("/api/transactions", params={"status": "needs_review"}).json()
    verizon = next(r for r in queue if "VERIZON" in r["description"])
    assert verizon["reason"] == "Missing the business-use %."
    r = loaded.post("/api/decisions", json={"txn_id": verizon["txn_id"], "category": "phone_internet",
                                            "business_use_pct": 50, "apply_to_merchant": True}).json()
    assert r["still_missing"] == [] and r["applied_to_other_transactions"] == 11
    queue = loaded.get("/api/transactions", params={"status": "needs_review", "limit": 500}).json()
    assert not any("VERIZON" in q["description"] for q in queue)


def test_reconcile(loaded):
    rec = loaded.get("/api/reconcile").json()
    by_payer = {r["payer"]: r for r in rec["info_returns"]}
    assert by_payer["Acme Design LLC"]["status"] == "matched"
    assert by_payer["Northwind Studios Inc"]["status"] == "matched"
    stripe = by_payer["Stripe, Inc."]
    assert stripe["status"] == "gross_vs_net" and stripe["gap"] == pytest.approx(685.40)
    assert stripe["suggested_adjustment"]["adjustments"][0]["amount"] == pytest.approx(685.40)
    assert any("MOBILE DEPOSIT" in d["description"] for d in rec["unexplained_deposits"])


def test_full_flow_and_export(loaded):
    loaded.put("/api/profile", json={"home_office_sqft": 150, "prior_year_total_tax": 9000, "prior_year_agi": 70000})
    sc = loaded.get("/api/schedule-c").json()
    assert sc["complete"] is False and sc["home_office"] == 750
    assert sc["mileage"]["amount"] == pytest.approx((42 + 38) * 0.725 + (27 + 42) * 0.76)
    assert loaded.get("/api/se-qbi").json()["schedule_se"]["must_file_schedule_se"]
    plan = loaded.get("/api/estimated-tax").json()
    assert plan["required_annual_payment"] == 9000
    md = loaded.get("/api/export/summary.md").text
    assert "Schedule C (Form 1040) worksheet" in md and "not tax advice" in md
    assert loaded.get("/api/export/transactions.csv").text.startswith("id,date")


def test_pdf_and_workbook_exports(loaded):
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    from pypdf import PdfReader

    r = loaded.get("/api/export/summary.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Schedule C (Form 1040) worksheet" in text and "Net profit" in text and "not tax advice" in text

    r = loaded.get("/api/export/workbook.xlsx")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    for name in z.namelist():
        ET.fromstring(z.read(name))  # every part is well-formed XML
    book = z.read("xl/workbook.xml").decode()
    assert all(f'name="{s}"' in book for s in ("Schedule C", "Transactions", "1099 check", "Mileage"))
    assert "ONLINE TRANSFER" in z.read("xl/worksheets/sheet2.xml").decode()


def test_profile_validation(client):
    assert client.put("/api/profile", json={"filing_status": "married"}).status_code == 400
    assert client.put("/api/profile", json={"ssn": "123"}).status_code == 400


def test_tools_have_http_bindings(client):
    tools = client.get("/api/tools").json()
    names = {t["function"]["name"] for t in tools}
    assert {"list_needs_review", "record_decision", "compute_schedule_c", "reconcile_income"} <= names
    assert all(t["x-http"]["path"].startswith("/api/") for t in tools)
    assert next(t for t in tools if t["function"]["name"] == "apply_adjustment")["x-confirm"] is True


def test_imported_files(loaded):
    files = loaded.get("/api/files").json()
    by_name = {f["name"]: f for f in files}
    assert by_name["bank_checking.csv"]["type"] == "transactions" and by_name["bank_checking.csv"]["items"] > 0
    assert by_name["info_returns.csv"]["type"] == "1099s" and by_name["info_returns.csv"]["items"] == 3
    assert by_name["Mileage log"]["items"] == 4
