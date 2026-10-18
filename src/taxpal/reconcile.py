import re

from . import db
from .wording import plural

TOLERANCE = 1.00
STOPWORDS = {"THE", "INC", "LLC", "CORP", "CO", "LTD", "COMPANY"}


def payer_key(payer: str) -> str:
    words = [w for w in re.findall(r"[A-Z0-9]+", payer.upper()) if w not in STOPWORDS]
    return words[0] if words else payer.upper()


def reconcile() -> dict:
    returns = db.rows("SELECT * FROM info_returns ORDER BY amount DESC")
    deposits = db.rows(
        """SELECT t.id AS txn_id, t.date, t.description, t.merchant, t.amount, c.category, c.needs_review
           FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id WHERE t.amount > 0""")
    fees = db.rows(
        """SELECT t.merchant, t.amount FROM transactions t JOIN classifications c ON c.txn_id = t.id
           WHERE c.category = 'commissions_fees'""")
    adjustments = db.rows("SELECT line, amount, note FROM adjustments")

    matched_ids: set[int] = set()
    results = []
    for r in returns:
        key = payer_key(r["payer"])
        hits = [d for d in deposits if key in d["merchant"] or key in d["description"].upper()]
        matched_ids.update(d["txn_id"] for d in hits)
        received = round(sum(d["amount"] for d in hits), 2)
        gap = round(r["amount"] - received, 2)
        item = {"info_return_id": r["id"], "form": r["form"], "payer": r["payer"], "reported": r["amount"],
                "deposits_found": received, "deposit_count": len(hits), "gap": gap}
        item["warnings"] = []
        not_income = [d["txn_id"] for d in hits if d["category"] not in (None, "income")]
        if not_income:
            item["warnings"].append(f"{plural(len(not_income), 'deposit')} from this payer {'is' if len(not_income) == 1 else 'are'} not categorized as income.")
        if abs(gap) <= TOLERANCE:
            item["status"] = "matched"
        elif gap > 0 and r["form"] == "1099-K":
            recorded_fees = round(-sum(f["amount"] for f in fees if key in f["merchant"]), 2)
            item["status"] = "gross_vs_net"
            item["explanation"] = ("1099-K reports gross payments; deposits are usually net of platform fees and refunds. "
                                   "Report the gross on line 1 and the fees on line 10 (Schedule C instructions).")
            item["fees_already_recorded"] = recorded_fees
            grossed_up = round(sum(a["amount"] for a in adjustments if a["line"] == "1" and r["payer"] in a["note"]), 2)
            item["adjustments_applied"] = grossed_up
            unexplained = round(gap - recorded_fees - grossed_up, 2)
            if unexplained < -TOLERANCE:
                item["warnings"].append(f"Fees plus adjustments are ${-unexplained:,.2f} more than the gap. "
                                        "Was the adjustment applied twice?")
            if unexplained > TOLERANCE:
                item["suggested_adjustment"] = {
                    "description": f"Gross up {r['payer']} income by ${unexplained:,.2f} and record the same as fees",
                    "adjustments": [{"line": "1", "amount": unexplained, "note": f"1099-K gross-up: {r['payer']}"},
                                    {"line": "10", "amount": unexplained, "note": f"Platform fees withheld: {r['payer']}"}]}
        elif gap > 0:
            item["status"] = "underreported_in_deposits"
            item["explanation"] = "The 1099 shows more than the deposits found. Was some paid to another account, or in 2025/2027?"
        else:
            item["status"] = "deposits_exceed_1099"
            item["explanation"] = "More came in than the 1099 shows. That's fine as long as the extra is reported as income too."
        results.append(item)

    unexplained = [d for d in deposits
                   if d["txn_id"] not in matched_ids and (d["category"] is None or d["needs_review"])]
    income_total = round(sum(d["amount"] for d in deposits if d["category"] == "income"), 2)
    reported_total = round(sum(r["amount"] for r in returns), 2)
    return {
        "info_returns": results,
        "unexplained_deposits": [{k: d[k] for k in ("txn_id", "date", "description", "amount")} for d in unexplained],
        "income_deposits_total": income_total,
        "reported_on_1099s_total": reported_total,
        "note": "All business income is reportable even without a 1099 (Schedule C instructions, line 1).",
    }
