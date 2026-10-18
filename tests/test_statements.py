import io

import pytest
from conftest import SAMPLE
from docgen import write_docx, write_pdf

from taxpal import db
from taxpal.statements import parse_statement, read_statement

STATEMENTS = SAMPLE / "statements"


def by_desc(preview, text):
    return next(r for r in preview["rows"] if text in r["description"])


def test_sample_card_pdf_balances():
    p = read_statement((STATEMENTS / "card_2026-01.pdf").read_bytes(), "card_2026-01.pdf")
    assert p["kind"] == "card" and p["period"] == ["2026-01-01", "2026-01-31"]
    assert len(p["rows"]) == 7 and p["check"]["ok"] and p["unparsed"] == [] and p["warnings"] == []
    assert by_desc(p, "PAYMENT THANK YOU")["amount"] == 972.98       # payment = money into the card
    assert by_desc(p, "MACBOOK")["amount"] == -2399.00               # purchase = money out


def test_sample_checking_docx_balances_from_running_balance():
    p = read_statement((STATEMENTS / "checking_2026-02.docx").read_bytes(), "checking_2026-02.docx")
    assert p["kind"] == "bank" and p["check"]["ok"] and len(p["rows"]) == 10
    assert by_desc(p, "STRIPE PAYOUT")["amount"] > 0 and by_desc(p, "RENT PAYMENT")["amount"] == -2100
    assert {r["sign_from"] for r in p["rows"]} == {"balance"}


def test_wrong_statement_type_is_caught_both_ways():
    card = read_statement((STATEMENTS / "card_2026-01.pdf").read_bytes(), "c.pdf", "bank")
    assert card["check"]["ok"] is False
    bank = read_statement((STATEMENTS / "checking_2026-02.docx").read_bytes(), "b.docx", "card")
    assert bank["check"]["ok"]  # flipping every sign fools the balance check...
    assert "switch the statement type" in bank["warnings"][0]  # ...but not the sign check


def test_missing_line_breaks_the_balance_check():
    lines = ["Checking Account Statement", "Statement Period 03/01/2026 - 03/31/2026",
             "Beginning Balance  $1,000.00",
             "03/02  COFFEE ROASTERS          -12.50",
             "03/05  CLIENT PAYMENT INV 4411    1,500.00",
             "03/09  STAPLES 1203              -40.00",
             "Ending Balance  $2,447.50"]
    full = parse_statement("\n".join(lines))
    assert full["check"]["ok"]
    dropped = parse_statement("\n".join(l for l in lines if "STAPLES" not in l))
    assert dropped["check"]["difference"] == 40.00 and not dropped["check"]["ok"]


def test_statement_formats():
    text = "\n".join([
        "Credit Card Statement   Payment Due Date 01/25/2026",
        "Statement Period 12/15/2025 - 01/14/2026",
        "Previous Balance $1,200.00",
        "12/18  12/19  DELTA AIR 0062 SFO-JFK            $1,045.20",
        "Dec 30        AMAZON MKTPLACE                     (25.00)",
        "01/02  01/03  PAYMENT - THANK YOU                 500.00 CR",
        "01/05  01/06  UNITED 016 BAGGAGE FEE               35.00",
        "01/10  01/11  STORE CREDIT                          20.00-",
        "New Balance $1,735.20",
    ])
    p = parse_statement(text)
    assert p["kind"] == "card" and p["check"]["ok"], p["check"]
    assert by_desc(p, "DELTA")["date"] == "2025-12-18"          # December goes with the period's first year
    assert by_desc(p, "UNITED")["date"] == "2026-01-05"
    assert by_desc(p, "AMAZON")["amount"] == 25.00               # (25.00) on a card = credit
    assert by_desc(p, "PAYMENT")["amount"] == 500.00             # CR
    assert by_desc(p, "STORE CREDIT")["amount"] == 20.00         # trailing minus
    assert by_desc(p, "DELTA")["amount"] == -1045.20


def test_wrapped_description_is_joined():
    p = parse_statement("Checking statement\nBeginning balance 100.00\n"
                        "04/01  ACH DEBIT GUSTO PAYROLL       -50.00\nCONTRACTOR J LEE\nEnding balance 50.00")
    assert p["rows"][0]["description"] == "ACH DEBIT GUSTO PAYROLL CONTRACTOR J LEE"


def test_scanned_pdf_and_bad_files(client):
    assert read_statement(write_pdf([]), "scan.pdf")["needs_ocr"] is True
    r = client.post("/api/import/statement", files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/import/statement", files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4 garbage"), "application/pdf")})
    assert r.status_code == 400


def preview(client, path):
    with open(path, "rb") as f:
        r = client.post("/api/import/statement", files={"file": (path.name, f)})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, p, account, rows=None):
    return client.post("/api/import/statement/confirm", json={
        "account": account, "source_file": p["source_file"], "kind": p["kind"], "period": p["period"],
        "opening": p["opening"], "closing": p["closing"],
        "rows": [{k: r[k] for k in ("date", "description", "amount")} for r in (rows if rows is not None else p["rows"])]})


def test_preview_saves_nothing_then_confirm_imports(client):
    p = preview(client, STATEMENTS / "checking_2026-02.docx")
    assert client.get("/api/status").json()["transactions"] == 0
    r = confirm(client, p, "checking").json()
    assert r["imported"] == 10 and r["check"]["ok"]
    assert confirm(client, p, "checking").json()["duplicates_skipped"] == 10


def test_statement_checks_block_sign_off(client):
    p = preview(client, STATEMENTS / "card_2026-01.pdf")
    # import with one purchase left out: the statement no longer balances
    r = confirm(client, p, "visa", rows=[x for x in p["rows"] if "NETFLIX" not in x["description"]]).json()
    assert r["check"]["difference"] == 15.49

    found = {c["id"].split(":")[0]: c for c in client.get("/api/checks").json()["checks"]}
    assert "15.49" in found["statement_unbalanced"]["message"]
    assert "Feb, Mar" in found["statement_gap"]["message"]  # only January was uploaded
    gap = found["statement_gap"]["id"]
    assert client.post(f"/api/checks/{gap}/acknowledge", json={"note": "Card opened in January, closed in February"}).status_code == 200


def test_period_covers_months_without_transactions(client):
    p = preview(client, STATEMENTS / "checking_2026-02.docx")
    confirm(client, p, "checking")
    [row] = db.rows("SELECT period_start, period_end, difference FROM statements")
    assert (row["period_start"], row["period_end"], row["difference"]) == ("2026-02-01", "2026-02-28", 0.0)


def test_bank_statement_with_sections_and_totals():
    text = "\n".join([
        "CHECKING SUMMARY        Account 000000123",
        "Statement Period 06/01/2026 through 06/30/2026",
        "Beginning Balance                 $5,000.00",
        "Deposits and Additions            $3,250.00",
        "Electronic Withdrawals            $1,180.45",
        "Ending Balance                    $7,069.55",
        "",
        "DEPOSITS AND ADDITIONS",
        "06/03     STRIPE TRANSFER ST-99812                  2,250.00",
        "06/18     REMOTE ONLINE DEPOSIT 1                   1,000.00",
        "Total Deposits and Additions                    $3,250.00",
        "",
        "ELECTRONIC WITHDRAWALS",
        "06/01     ZELLE PAYMENT TO OAK ST APTS              1,100.00",
        "06/14     ADOBE *CREATIVE CLOUD                        59.99",
        "06/22     GOOGLE *GSUITE                               20.46",
        "Total Electronic Withdrawals                    $1,180.45",
        "Page 1 of 2",
    ])
    p = parse_statement(text)
    assert p["kind"] == "bank" and p["check"]["ok"], (p["check"], p["rows"])
    assert [r["sign_from"] for r in p["rows"]] == ["section"] * 5
    assert by_desc(p, "REMOTE ONLINE DEPOSIT")["amount"] == 1000 and by_desc(p, "ZELLE PAYMENT TO")["amount"] == -1100
