"""Fake statements built from the sample CSVs, for trying the PDF/DOCX import:
- data/sample/statements/card_2026-01.pdf      card statement, sections + unsigned amounts
- data/sample/statements/checking_2026-02.docx  bank statement, a table with a running balance
Run make_sample_data.py first."""

import csv
from pathlib import Path

from docgen import write_docx, write_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
OUT = SAMPLE / "statements"


def card_rows(month: int) -> list[tuple[str, str, float]]:
    """(date, description, charge>0 / credit<0) from the card CSV."""
    rows = []
    with open(SAMPLE / "credit_card.csv") as f:
        for r in csv.DictReader(f):
            if r["Transaction Date"][5:7] == f"{month:02d}":
                amt = float(r["Debit"]) if r["Debit"] else -float(r["Credit"])
                rows.append((r["Transaction Date"], r["Description"], amt))
    return rows


def bank_rows(month: int) -> list[tuple[str, str, float]]:
    rows = []
    with open(SAMPLE / "bank_checking.csv") as f:
        for r in csv.DictReader(f):
            m, d, y = r["Posting Date"].split("/")
            if int(m) == month:
                rows.append((f"{y}-{m}-{d}", r["Description"], float(r["Amount"])))
    return sorted(rows)


def card_pdf() -> bytes:
    rows = card_rows(1)
    previous = 812.40
    charges = round(sum(a for _, _, a in rows if a > 0), 2)
    credits = round(-sum(a for _, _, a in rows if a < 0), 2)
    new = round(previous + charges - credits, 2)
    mmdd = lambda d: f"{d[5:7]}/{d[8:10]}"
    lines = [
        "NORTHSTAR BANK VISA SIGNATURE            Credit Card Statement",
        "Account ending in 4417",
        "Statement Period: 01/01/2026 - 01/31/2026        Payment Due Date: 02/25/2026",
        "",
        f"Previous Balance                         ${previous:,.2f}",
        f"Payments and Credits                    -${credits:,.2f}",
        f"Purchases                               +${charges:,.2f}",
        f"New Balance                              ${new:,.2f}",
        f"Minimum Payment Due                      ${max(35, round(new * 0.02, 2)):,.2f}",
        "",
        "PAYMENTS AND OTHER CREDITS",
        "Trans  Post   Description                                   Amount",
    ]
    lines += [f"{mmdd(d)}  {mmdd(d)}  {desc:<44}{-a:>10,.2f}" for d, desc, a in rows if a < 0]
    lines += ["", "PURCHASES", "Trans  Post   Description                                   Amount"]
    lines += [f"{mmdd(d)}  {mmdd(d)}  {desc:<44}{a:>10,.2f}" for d, desc, a in rows if a > 0]
    lines += ["", f"Total purchases for this period                 {charges:>10,.2f}",
              "Page 1 of 1"]
    return write_pdf(lines)


def checking_docx() -> bytes:
    rows = bank_rows(2)
    opening = 8421.17
    balance = opening
    table = [["Date", "Description", "Withdrawals", "Deposits", "Balance"]]
    for d, desc, a in rows:
        balance = round(balance + a, 2)
        table.append([f"{d[5:7]}/{d[8:10]}", desc, f"{-a:,.2f}" if a < 0 else "", f"{a:,.2f}" if a > 0 else "", f"{balance:,.2f}"])
    return write_docx(
        ["Oakland Community Credit Union", "Everyday Checking - Account ending 0298",
         "Statement Period 02/01/2026 through 02/28/2026", f"Beginning Balance on 02/01/2026  ${opening:,.2f}"],
        table,
        [f"Ending Balance on 02/28/2026  ${balance:,.2f}", "Questions? Call 1-800-555-0101"])


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "card_2026-01.pdf").write_bytes(card_pdf())
    (OUT / "checking_2026-02.docx").write_bytes(checking_docx())
    print(f"Wrote 2 statements to {OUT}")
