"""Random test files for trying the browser model, written to data/test/ (or --out):
- bank_checking.csv     Posting Date, Description, Amount (signed)
- credit_card.csv       Transaction Date, Description, Debit, Credit
- business_card.csv     Trans. Date, Details, Amount (purchases positive: tick that box on import)
- card_statement.pdf    card statement, sections + unsigned amounts
- checking_statement.docx  bank statement table with a running balance
- labels.csv            expected category for every row above, and whether a keyword rule catches it

About half the merchants match no keyword rule, so the model has to sort them.
Some are ambiguous on purpose (Amazon, Costco, Apple) to see whether the agent asks.
Same --seed, same files."""

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from docgen import write_docx, write_pdf

ROOT = Path(__file__).resolve().parent.parent
YEAR = 2026

# (description, category, low, high). Amounts are positive here; the sign comes from the category.
KEYWORD = [
    ("STRIPE PAYOUT ST-{n}", "income", 400, 3200),
    ("UPWORK ESCROW INC {n}", "income", 300, 1800),
    ("GOOGLE ADS {n}", "advertising", 40, 400),
    ("ADOBE *CREATIVE CLOUD", "software", 59.99, 59.99),
    ("GITHUB INC", "software", 4, 21),
    ("DIGITALOCEAN.COM", "software", 12, 48),
    ("STAPLES {n}", "office", 15, 120),
    ("USPS PO {n}", "office", 8, 45),
    ("DELTA AIR {n}", "travel", 180, 640),
    ("MARRIOTT HOTELS {n}", "travel", 150, 520),
    ("STARBUCKS STORE {n}", "meals", 6, 28),
    ("CHIPOTLE {n}", "meals", 11, 35),
    ("VERIZON WIRELESS", "phone_internet", 85, 95),
    ("COMCAST CABLE", "phone_internet", 79.99, 79.99),
    ("SHELL OIL {n}", "car_actual", 35, 70),
    ("WEWORK MEMBERSHIP", "rent", 350, 350),
    ("NETFLIX.COM", "personal", 15.49, 15.49),
    ("WHOLE FOODS MKT {n}", "personal", 30, 180),
    ("TRADER JOE S #{n}", "personal", 20, 110),
    ("HISCOX INC", "insurance", 32, 32),
    ("UDEMY ONLINE COURSE", "education", 14.99, 89.99),
]
NO_KEYWORD = [
    ("WIRE IN BRIGHTLINE CREATIVE {n}", "income", 1500, 6000),
    ("ZELLE FROM MARISOL REYES", "income", 250, 900),
    ("MAILCHIMP *MONTHLY", "software", 20, 60),
    ("SQUARESPACE INC", "software", 23, 23),
    ("JETBRAINS S.R.O.", "software", 24.9, 24.9),
    ("OPENROUTER INC", "software", 10, 40),
    ("NAMECHEAP.COM", "software", 9.98, 18.48),
    ("B&H PHOTO VIDEO {n}", "supplies", 25, 400),
    ("MOO PRINT LTD", "advertising", 40, 140),
    ("PRINTFUL INC", "supplies", 30, 160),
    ("CALTRAIN CLIPPER", "travel", 8, 30),
    ("LYFT *RIDE {n}", "travel", 12, 48),
    ("UBER *TRIP {n}", "travel", 10, 55),
    ("BLUE BOTTLE COFFEE {n}", "meals", 6, 22),
    ("SWEETGREEN {n}", "meals", 13, 40),
    ("FIVERR {n}", "contract_labor", 50, 450),
    ("WAVE PAYROLL - J PATEL", "contract_labor", 400, 1200),
    ("FTB MYFTB PAYMENT", "estimated_tax_payment", 500, 1500),
    ("CA SOS BIZFILE", "taxes_licenses", 20, 20),
    ("GUSTO TAX ADVISORS", "legal_professional", 150, 450),
    ("APPLE STORE #R{n}", "capital_purchase", 1299, 2499),
    ("SPECIALIZED BIKES {n}", "personal", 80, 900),
    ("SEPHORA {n}", "personal", 25, 120),
    ("HULU 877-8244858", "personal", 17.99, 17.99),
    ("PETCO {n}", "personal", 20, 90),
    ("CVS PHARMACY {n}", "personal", 8, 60),
]
# could be business or personal; the agent should ask
AMBIGUOUS = [
    ("AMAZON.COM*{code}", "supplies", 12, 220),
    ("AMZN MKTP US*{code}", "personal", 9, 85),
    ("COSTCO WHSE #{n}", "personal", 60, 320),
    ("APPLE.COM/BILL", "software", 0.99, 9.99),
    ("BEST BUY {n}", "supplies", 25, 300),
    ("HOME DEPOT #{n}", "supplies", 15, 180),
    ("PAYPAL *ETSYSELLER", "supplies", 18, 75),
]
MONTHLY = [  # fixed bills every month, from checking
    ("RENT PAYMENT - BIRCH LN APTS", "home_utilities", 2350),
    ("BLUE CROSS CA PREMIUM", "health_insurance", 438),
]
INCOME = {"income", "refund"}


def fill(rng: random.Random, desc: str) -> str:
    code = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(9))
    return desc.format(n=rng.randint(1000, 99999), code=code)


def amount(rng: random.Random, cat: str, lo: float, hi: float) -> float:
    a = round(rng.uniform(lo, hi), 2) if lo != hi else lo
    return a if cat in INCOME else -a


def day_in(rng: random.Random, month: int) -> date:
    start = date(YEAR, month, 1)
    end = date(YEAR + month // 12, month % 12 + 1, 1)
    return start + timedelta(days=rng.randrange((end - start).days))


def month_end(month: int) -> str:
    return (date(YEAR + month // 12, month % 12 + 1, 1) - timedelta(days=1)).strftime("%m/%d/%Y")


def make_rows(rng: random.Random, months: range, per_month: int, pool, income_ok: bool) -> list[dict]:
    rows = []
    for m in months:
        for _ in range(per_month):
            desc, cat, lo, hi = rng.choice(pool)
            if not income_ok and cat in INCOME:
                continue
            rows.append({"date": day_in(rng, m), "description": fill(rng, desc), "amount": amount(rng, cat, lo, hi),
                         "category": cat})
    return sorted(rows, key=lambda r: r["date"])


def checking(rng: random.Random, months: range) -> list[dict]:
    pool = [p for p in KEYWORD + NO_KEYWORD if p[1] in INCOME or rng.random() < 0.3]
    rows = make_rows(rng, months, 9, pool + AMBIGUOUS[:2], income_ok=True)
    for m in months:
        for desc, cat, amt in MONTHLY:
            rows.append({"date": date(YEAR, m, 1), "description": desc, "amount": -amt, "category": cat})
        rows.append({"date": date(YEAR, m, 28), "description": "PAYMENT TO NORTHSTAR VISA AUTOPAY PAYMENT",
                     "amount": -round(rng.uniform(800, 2500), 2), "category": "transfer"})
    return sorted(rows, key=lambda r: r["date"])


def card(rng: random.Random, months: range, per_month: int) -> list[dict]:
    pool = [p for p in KEYWORD + NO_KEYWORD + AMBIGUOUS * 2 if p[1] not in INCOME]
    rows = make_rows(rng, months, per_month, pool, income_ok=False)
    if rows:  # one refund to check credits are read the right way round
        r = rng.choice(rows)
        rows.append({"date": r["date"] + timedelta(days=3), "description": f"RETURN CREDIT {r['description']}",
                     "amount": -r["amount"], "category": "refund"})
    return sorted(rows, key=lambda r: r["date"])


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def card_pdf(rows: list[dict], month: int) -> bytes:
    rows = [r for r in rows if r["date"].month == month]
    mmdd = lambda d: d.strftime("%m/%d")
    charges = round(-sum(r["amount"] for r in rows if r["amount"] < 0), 2)
    credits = round(sum(r["amount"] for r in rows if r["amount"] > 0), 2)
    previous = 1204.55
    new = round(previous + charges - credits, 2)
    line = lambda r: f"{mmdd(r['date'])}  {mmdd(r['date'])}  {r['description'][:44]:<44}{abs(r['amount']):>10,.2f}"
    lines = [
        "NORTHSTAR BANK VISA SIGNATURE            Credit Card Statement",
        "Account ending in 4417",
        f"Statement Period: {month:02d}/01/{YEAR} - {month_end(month)}",
        "",
        f"Previous Balance                         ${previous:,.2f}",
        f"Payments and Credits                    -${credits:,.2f}",
        f"Purchases                               +${charges:,.2f}",
        f"New Balance                              ${new:,.2f}",
        "",
        "PAYMENTS AND OTHER CREDITS",
        "Trans  Post   Description                                   Amount",
        *[line(r) for r in rows if r["amount"] > 0],
        "",
        "PURCHASES",
        "Trans  Post   Description                                   Amount",
        *[line(r) for r in rows if r["amount"] < 0],
        "",
        f"Total purchases for this period                 {charges:>10,.2f}",
    ]
    return write_pdf(lines)


def checking_docx(rows: list[dict], month: int) -> bytes:
    rows = [r for r in rows if r["date"].month == month]
    balance = opening = 9312.40
    table = [["Date", "Description", "Withdrawals", "Deposits", "Balance"]]
    for r in rows:
        a = r["amount"]
        balance = round(balance + a, 2)
        table.append([r["date"].strftime("%m/%d"), r["description"], f"{-a:,.2f}" if a < 0 else "",
                      f"{a:,.2f}" if a > 0 else "", f"{balance:,.2f}"])
    return write_docx(
        ["Oakland Community Credit Union", "Everyday Checking - Account ending 0298",
         f"Statement Period {month:02d}/01/{YEAR} through {month_end(month)}",
         f"Beginning Balance  ${opening:,.2f}"],
        table, [f"Ending Balance  ${balance:,.2f}"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: new each run)")
    ap.add_argument("--months", type=int, default=3, help="months of transactions per CSV, from January")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "test")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    args.out.mkdir(parents=True, exist_ok=True)

    months = range(1, args.months + 1)
    bank, cc = checking(rng, months), card(rng, months, 12)
    # statements and the business card cover the month after the CSVs, so nothing is imported twice
    m = args.months + 1
    biz = card(rng, months, 5)
    stmt_card, stmt_bank = card(rng, range(m, m + 1), 14), checking(rng, range(m, m + 1))

    write_csv(args.out / "bank_checking.csv", ["Posting Date", "Description", "Amount"],
              [[r["date"].strftime("%m/%d/%Y"), r["description"], f"{r['amount']:.2f}"] for r in bank])
    write_csv(args.out / "credit_card.csv", ["Transaction Date", "Description", "Debit", "Credit"],
              [[r["date"].isoformat(), r["description"], f"{-r['amount']:.2f}" if r["amount"] < 0 else "",
                f"{r['amount']:.2f}" if r["amount"] > 0 else ""] for r in cc])
    write_csv(args.out / "business_card.csv", ["Trans. Date", "Details", "Amount"],
              [[r["date"].strftime("%m/%d/%y"), r["description"], f"{-r['amount']:.2f}"] for r in biz])
    (args.out / "card_statement.pdf").write_bytes(card_pdf(stmt_card, m))
    (args.out / "checking_statement.docx").write_bytes(checking_docx(stmt_bank, m))

    keywords = [d.split("{")[0].strip(" #*") for d, *_ in KEYWORD + MONTHLY]
    keywords += ["AUTOPAY PAYMENT", "RETURN CREDIT"]
    labelled = [("bank_checking.csv", bank), ("credit_card.csv", cc), ("business_card.csv", biz),
                ("card_statement.pdf", stmt_card), ("checking_statement.docx", stmt_bank)]
    with open(args.out / "labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "date", "description", "amount", "expected_category", "keyword_rule"])
        for name, rows in labelled:
            for r in rows:
                hit = any(k in r["description"] for k in keywords)
                w.writerow([name, r["date"].isoformat(), r["description"], f"{r['amount']:.2f}", r["category"],
                            "yes" if hit else "no"])

    total = sum(len(rows) for _, rows in labelled)
    print(f"Wrote {total} transactions in 5 files + labels.csv to {args.out} (seed {seed})")


if __name__ == "__main__":
    main()
