import csv
import io
from datetime import date

from . import checks, db, pdfdoc, reconcile, service
from .rules import taxonomy
from .xlsx import BOLD, BOLD_MONEY, HEADER, MONEY, MUTED, TITLE, Sheet, write_xlsx
from .wording import plural

DISCLAIMER = ("TaxPal is not tax advice and does not file returns. These are worksheets and estimates "
              "for Tax Year 2026. Go over them with a tax professional before you file.")


def _money(x) -> str:
    return "n/a" if x is None else f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


RECONCILE_STATUS = {"matched": "Matched", "gross_vs_net": "Gross vs. net (fees)",
                    "underreported_in_deposits": "Less in deposits", "deposits_exceed_1099": "More in deposits"}


def _label(key: str) -> str:
    """deductible_half_se_tax -> Deductible half SE tax"""
    words = ["SE" if w == "se" else w for w in key.split("_")]
    return " ".join(words)[:1].upper() + " ".join(words)[1:]


def _signoff(signoff: dict | None, review: dict) -> tuple[str, str]:
    """(status text, box kind)."""
    if signoff and signoff["current"]:
        return f"Reviewed and signed off by you on {signoff['signed_at']} UTC. {signoff['statement']}", "ok"
    if signoff:
        return "DRAFT: data changed after you signed off. Run the checks and sign off again.", "warn"
    return f"DRAFT: not signed off. {plural(review['open_blockers'], 'check')} still open.", "warn"


def summary_blocks() -> list[tuple]:
    """The summary as blocks, rendered to PDF or Markdown:
    ("title"|"meta"|"h2"|"h3"|"p", text), ("box", text, kind), ("bullets", items),
    ("table", headers, rows, numeric_cols, bold_rows)."""
    sc = service.schedule_c()
    sq = service.se_and_qbi()
    est = service.estimated_tax()
    rec = reconcile.reconcile()
    tax = taxonomy()
    used_categories = {t["category"] for t in service.classified_txns()}
    review = checks.run_checks()
    status, kind = _signoff(review["signoff"], review)

    b = [("title", f"TaxPal summary: Tax Year {sc['tax_year']} (Schedule C)"),
         ("meta", f"Generated {date.today().isoformat()}." + ("" if sc["complete"] else " Provisional: some items still need review.")),
         ("box", status, kind),
         ("meta", DISCLAIMER)]

    lines = [[line, v["name"], _money(v["amount"])] for line, v in sc["lines"].items()]
    totals = [["7", "Gross income", _money(sc["gross_income"])],
              ["28", "Total expenses", _money(sc["total_expenses"])],
              ["29", "Tentative profit", _money(sc["tentative_profit"])],
              ["30", "Business use of home (simplified)", _money(sc["home_office"])],
              ["31", "Net profit", _money(sc["net_profit"])]]
    b += [("h2", "Schedule C (Form 1040) worksheet"),
          ("table", ["Line", "Description", "Amount"], lines + totals, {2}, {len(lines) + 4})]
    if sc["mileage"]:
        b += [("h3", "Mileage (standard rate, line 9)"),
              ("bullets", [f"{p['from']} to {p['to']}: {p['miles']:,.1f} mi \u00d7 ${p['rate']} = {_money(p['amount'])}"
                           for p in sc["mileage"]["periods"]])]

    se, q = sq["schedule_se"], sq["qbi"]
    b += [("h2", "Self-employment tax (Schedule SE, estimate)"),
          ("table", ["Item", "Amount"],
           [["Net earnings from self-employment (\u00d7 92.35%)", _money(se["net_earnings"])],
            ["Social Security (12.4%)", _money(se["social_security"])],
            ["Medicare (2.9%)", _money(se["medicare"])],
            ["SE tax", _money(se["se_tax"])],
            ["Deductible half (Schedule 1)", _money(se["deductible_half"])]], {1}, {3}),
          ("h2", "QBI deduction (Form 8995, estimate)"),
          ("table", ["Item", "Amount"],
           [["Qualified business income", _money(q["qbi"])],
            [f"Deduction (Form {q['form']})", _money(q["deduction"])]], {1}, {1})]

    b += [("h2", "Schedule 1 items (not Schedule C)"),
          ("bullets", [f"{_label(k)}: {_money(v)}" for k, v in sq["schedule1_adjustments"].items()]
           + [f"Federal estimated tax already paid in 2026: {_money(sc['estimated_tax_paid'])}"])]

    b += [("h2", "Income reconciliation (1099s vs. deposits)")]
    if rec["info_returns"]:
        b.append(("table", ["Form", "Payer", "Reported", "Deposits", "Status"],
                  [[r["form"], r["payer"], _money(r["reported"]), _money(r["deposits_found"]), RECONCILE_STATUS.get(r["status"], _label(r["status"]))]
                   for r in rec["info_returns"]], {2, 3}, set()))
    else:
        b.append(("p", "No 1099s imported."))
    if rec["unexplained_deposits"]:
        b.append(("p", f"{plural(len(rec['unexplained_deposits']), 'unexplained deposit')}. Check whether {'it is' if len(rec['unexplained_deposits']) == 1 else 'they are'} income."))

    b.append(("h2", f"Estimated tax plan for {est['plan_year']} (Form 1040-ES)"))
    if est.get("required_annual_payment") is None:
        b.append(("p", "Not computed. Add your 2026 total tax and AGI (or a 2027 projection) to your profile."))
    else:
        b += [("p", f"Safe-harbor basis: {est['safe_harbor_basis']} = {_money(est['required_annual_payment'])}; "
                    f"after withholding: {_money(est['estimated_payments_needed'])}."),
              ("table", ["Due", "Amount"], [[x["due"], _money(x["amount"])] for x in est["quarters"]], {1}, set())]

    flags = [(f, []) for f in sc["flags"] + se["flags"] + q["flags"]]
    if sc["capital_purchases"]:
        flags.append(("Capital purchases to review for depreciation or section 179:",
                      [f"{c['date']} {c['description']} {_money(-c['amount'])}" for c in sc["capital_purchases"]]))
    b += [("h2", "Flags for your tax professional"), ("bullets", flags or ["None"])]

    open_checks = [c for c in review["checks"] if c["severity"] == "blocker" and not c["acknowledged"]]
    if open_checks:
        b += [("h2", "Open checks"), ("bullets", [c["message"] for c in open_checks])]
    explained = [c for c in review["checks"] if c["acknowledged"]]
    if explained:
        b += [("h2", "Explanations you gave"),
              ("bullets", [(c["message"], [f"Your note ({c['acknowledged']['at']}): {c['acknowledged']['note']}"]) for c in explained])]

    b += [("h2", "IRS sources"), ("bullets", sorted({f"{tax[c]['label']}: {tax[c]['irs']}" for c in used_categories}) or ["None"]),
          ("h2", "Recordkeeping (Pub 583)"),
          ("bullets", ["Keep receipts and records generally 3 years after filing (longer for assets you still own).",
                       "Meals and travel: keep amount, date, place and business purpose. Vehicles: keep a mileage log."])]
    return b


def summary_markdown() -> str:
    out = []
    for block in summary_blocks():
        kind = block[0]
        if kind == "title":
            out += [f"# {block[1]}", ""]
        elif kind == "meta":
            out += [f"> {block[1]}" if block[1] == DISCLAIMER else block[1], ""]
        elif kind == "box":
            out += [f"**{block[1]}**", ""]
        elif kind in ("h2", "h3"):
            out += [f"{'##' if kind == 'h2' else '###'} {block[1]}", ""]
        elif kind == "p":
            out += [block[1], ""]
        elif kind == "bullets":
            for item in block[1]:
                text, subs = item if isinstance(item, tuple) else (item, [])
                out += [f"- {text}"] + [f"  - {s}" for s in subs]
            out.append("")
        elif kind == "table":
            _, headers, rows, numeric, bold = block
            out += ["| " + " | ".join(headers) + " |",
                    "|" + "|".join("---:" if i in numeric else "---" for i in range(len(headers))) + "|"]
            out += ["| " + " | ".join(f"**{c}**" if n in bold else str(c) for c in row) + " |" for n, row in enumerate(rows)]
            out.append("")
    return "\n".join(out)


def summary_pdf() -> bytes:
    doc = pdfdoc.Document(footer=f"TaxPal summary, Tax Year 2026. Not tax advice. Generated {date.today().isoformat()}.")
    for block in summary_blocks():
        kind = block[0]
        if kind == "title":
            doc.title(block[1])
        elif kind == "meta":
            doc.paragraph(block[1], size=9, color=pdfdoc.MUTED)
            doc.space(4)
        elif kind == "box":
            doc.space(4)
            doc.box(block[1], block[2])
        elif kind == "h2":
            doc.heading(block[1])
        elif kind == "h3":
            doc.subheading(block[1])
        elif kind == "p":
            doc.paragraph(block[1])
            doc.space(4)
        elif kind == "bullets":
            doc.bullets(block[1])
        elif kind == "table":
            _, headers, rows, numeric, bold = block
            widths = [0.08, 0.72, 0.2] if headers == ["Line", "Description", "Amount"] else None
            doc.table(headers, rows, numeric, bold, widths)
    return doc.to_bytes()


def _transaction_rows() -> list[dict]:
    return db.rows("""SELECT t.id, t.date, t.description, t.amount, t.account, c.category, c.source, c.confidence,
                             c.business_purpose, c.business_use_pct, c.recipient, c.needs_review
                      FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id ORDER BY t.date""")


def workbook_xlsx() -> bytes:
    sc = service.schedule_c()
    sq = service.se_and_qbi()
    rec = reconcile.reconcile()
    tax = taxonomy()
    review = checks.run_checks()
    status, _ = _signoff(review["signoff"], review)

    summary = Sheet("Schedule C", [8, 48, 16])
    summary.add((f"TaxPal summary: Tax Year {sc['tax_year']} (Schedule C)", TITLE))
    summary.add((status, BOLD))
    summary.add((DISCLAIMER, MUTED))
    summary.add()
    summary.add(*[(h, HEADER) for h in ("Line", "Description", "Amount")])
    for line, v in sc["lines"].items():
        summary.add(line, v["name"], (v["amount"], MONEY))
    for line, name, amount in [("7", "Gross income", sc["gross_income"]), ("28", "Total expenses", sc["total_expenses"]),
                               ("29", "Tentative profit", sc["tentative_profit"]),
                               ("30", "Business use of home (simplified)", sc["home_office"]),
                               ("31", "Net profit", sc["net_profit"])]:
        bold = line == "31"
        summary.add((line, BOLD if bold else None), (name, BOLD if bold else None), (amount, BOLD_MONEY if bold else MONEY))
    se, q = sq["schedule_se"], sq["qbi"]
    summary.add()
    summary.add(None, ("Self-employment tax (estimate)", HEADER), ("", HEADER))
    for name, amount in [("Net earnings from self-employment", se["net_earnings"]), ("Social Security (12.4%)", se["social_security"]),
                         ("Medicare (2.9%)", se["medicare"]), ("SE tax", se["se_tax"]),
                         ("Deductible half (Schedule 1)", se["deductible_half"]),
                         ("Qualified business income", q["qbi"]), (f"QBI deduction (Form {q['form']})", q["deduction"])]:
        summary.add(None, name, (amount, MONEY))

    txns = Sheet("Transactions", [11, 40, 13, 14, 26, 9, 32, 10, 18, 12, 11, 8], header_row=0)
    txns.add(*[(h, HEADER) for h in ("Date", "Description", "Amount", "Account", "Category", "Sch. C line",
                                     "Business purpose", "Business use %", "Recipient", "Decided by", "Confidence", "Review")])
    for r in _transaction_rows():
        cat = tax.get(r["category"]) if r["category"] else None
        txns.add(date.fromisoformat(r["date"]), r["description"], (r["amount"], MONEY), r["account"],
                 cat["label"] if cat else "Uncategorized", cat["line"] if cat else None, r["business_purpose"],
                 r["business_use_pct"], r["recipient"], r["source"],
                 round(r["confidence"], 2) if r["confidence"] is not None else None,
                 "yes" if r["needs_review"] or not r["category"] else "no")

    forms = Sheet("1099 check", [10, 28, 14, 14, 14, 16, 70], header_row=0)
    forms.add(*[(h, HEADER) for h in ("Form", "Payer", "Reported", "Deposits", "Difference", "Status", "Explanation")])
    for r in rec["info_returns"]:
        forms.add(r["form"], r["payer"], (r["reported"], MONEY), (r["deposits_found"], MONEY), (r["gap"], MONEY),
                  RECONCILE_STATUS.get(r["status"], _label(r["status"])), r.get("explanation"))

    miles = Sheet("Mileage", [11, 10, 60], header_row=0)
    miles.add(*[(h, HEADER) for h in ("Date", "Miles", "Purpose")])
    trips = db.rows("SELECT date, miles, purpose FROM mileage ORDER BY date")
    for t in trips:
        miles.add(date.fromisoformat(t["date"]), t["miles"], t["purpose"])
    return write_xlsx([summary, txns, forms, miles])


def transactions_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    tax = taxonomy()
    w.writerow(["id", "date", "description", "amount", "account", "category", "schedule_c_line",
                "source", "confidence", "business_purpose", "business_use_pct", "recipient", "needs_review"])
    for r in _transaction_rows():
        line = tax[r["category"]]["line"] if r["category"] else None
        w.writerow([r["id"], r["date"], r["description"], r["amount"], r["account"], r["category"], line,
                    r["source"], r["confidence"], r["business_purpose"], r["business_use_pct"], r["recipient"],
                    "yes" if r["needs_review"] or not r["category"] else "no"])
    return buf.getvalue()
