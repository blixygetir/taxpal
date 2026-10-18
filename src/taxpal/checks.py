"""Checks that run before the user signs off.

Blockers have to be fixed, or (for some) explained in writing by the user. Warnings are
worth a look but don't stop sign-off. Nothing the model or a keyword decided reaches a
signed-off summary unless a person has looked at it.
"""

import hashlib
import json

from . import db, engine, reconcile, risks, service, statements
from .rules import tax_year, taxonomy
from .wording import plural

SPOT_CHECK_AMOUNT = 500
# lines are rounded one by one, totals from the raw sums, so allow a few cents
MATH_TOLERANCE = 0.10
STATEMENT = "I reviewed the categories, explanations and totals, and they match my records."

FINGERPRINT_SQL = [
    "SELECT id, date, description, amount, account FROM transactions ORDER BY id",
    "SELECT txn_id, category, business_purpose, business_use_pct, recipient FROM classifications ORDER BY txn_id",
    "SELECT form, payer, amount FROM info_returns ORDER BY id",
    "SELECT line, amount, note FROM adjustments ORDER BY id",
    "SELECT date, miles, purpose FROM mileage ORDER BY id",
    "SELECT key, value FROM profile ORDER BY key",
    "SELECT check_id, note FROM check_acks ORDER BY check_id",
]


def fingerprint() -> str:
    """Hash of everything that feeds the numbers. Any change after sign-off makes it stale."""
    h = hashlib.sha256()
    for sql in FINGERPRINT_SQL:
        h.update(json.dumps(db.rows(sql), sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def _items(where: str, params=()) -> list[dict]:
    return db.rows(f"""SELECT t.id AS txn_id, t.date, t.description, t.merchant, t.amount,
                              c.category, c.confidence, c.source, c.rationale
                       FROM transactions t JOIN classifications c ON c.txn_id = t.id
                       WHERE {where} ORDER BY ABS(t.amount) DESC""", params)


def _math_problems(sc: dict, txns: list[dict], adjustments: list[dict]) -> list[tuple[str, str]]:
    """Recompute a few numbers a different way than the engine does and compare."""
    problems = []
    amount = lambda line: sc["lines"].get(line, {}).get("amount", 0.0)
    adj = lambda line: sum(a["amount"] for a in adjustments if a["line"] == line)
    close = lambda a, b: abs(a - b) <= MATH_TOLERANCE

    if not close(sum(amount(l) for l in engine.EXPENSE_LINES), sc["total_expenses"]):
        problems.append(("expense_lines", "Expense lines don't add up to line 28."))
    if not close(sc["gross_income"] - sc["total_expenses"], sc["tentative_profit"]):
        problems.append(("line_29", "Line 7 minus line 28 doesn't equal line 29."))
    if not close(sc["tentative_profit"] - sc["home_office"], sc["net_profit"]):
        problems.append(("line_31", "Line 29 minus line 30 doesn't equal line 31."))

    tax = taxonomy()
    meals_pct = tax_year()["meals"]["deductible_pct"]
    income = sum(t["amount"] for t in txns if tax[t["category"]]["treatment"] == "income") + adj("1")
    if not close(income, amount("1")):
        problems.append(("line_1", "Line 1 doesn't match the deposits marked as income plus adjustments."))
    for key, cat in tax.items():
        if cat["treatment"] == "meals":
            spent = -sum(t["amount"] for t in txns if t["category"] == key)
            if not close(spent * meals_pct + adj(cat["line"]), amount(cat["line"])):
                problems.append(("meals", f"Line {cat['line']} doesn't match the deductible share of meal spending."))
    return problems


def run_checks() -> dict:
    acks = {r["check_id"]: r for r in db.rows("SELECT * FROM check_acks")}
    checks = []

    def add(check_id, severity, message, can_acknowledge=False, **extra):
        ack = acks.get(check_id) if can_acknowledge else None
        checks.append({"id": check_id, "severity": severity, "message": message, "can_acknowledge": can_acknowledge,
                       "acknowledged": {"note": ack["note"], "at": ack["acked_at"]} if ack else None, **extra})

    s = service.status()
    if s["uncategorized"]:
        add("uncategorized", "blocker", f"{plural(s['uncategorized'], 'transaction')} {'has' if s['uncategorized'] == 1 else 'have'} no category yet.")
    if s["needs_review"]:
        add("needs_review", "blocker", f"{plural(s['needs_review'], 'transaction')} {'is' if s['needs_review'] == 1 else 'are'} waiting for your review.")

    guesses = _items("c.source = 'llm'")
    if guesses:
        add("model_unconfirmed", "blocker",
            f"{plural(len(guesses), 'category', 'categories')} came from the AI. Confirm or correct each one.", items=guesses)

    # big rule matches: group by merchant so one click covers twelve rent payments
    groups: dict[tuple, list] = {}
    for t in _items("c.source = 'rule' AND ABS(t.amount) >= ?", (SPOT_CHECK_AMOUNT,)):
        groups.setdefault((t["merchant"], t["category"]), []).append(t)
    if groups:
        add("spot_check", "warning",
            f"{plural(len(groups), 'merchant')} with payments of ${SPOT_CHECK_AMOUNT} or more {'was' if len(groups) == 1 else 'were'} sorted by keyword. "
            "These are worth a quick look.",
            groups=[{"merchant": m, "category": c, "count": len(ts), "total": round(sum(t["amount"] for t in ts), 2),
                     "txn_ids": [t["txn_id"] for t in ts], "example": ts[0]["description"]}
                    for (m, c), ts in groups.items()])

    rec = reconcile.reconcile()
    for r in rec["info_returns"]:
        cid = f"reconcile:{r['info_return_id']}"
        if r.get("suggested_adjustment"):
            add(cid, "blocker", f"{r['form']} from {r['payer']} is ${r['gap']:,.2f} more than the deposits. "
                                f"{r['suggested_adjustment']['description']}, or explain why not.",
                can_acknowledge=True, suggested_adjustment=r["suggested_adjustment"])
        elif r["status"] == "underreported_in_deposits":
            add(cid, "blocker", f"{r['form']} from {r['payer']} shows ${r['gap']:,.2f} more than the deposits found. "
                                f"{r['explanation']}", can_acknowledge=True)
        elif r["status"] == "deposits_exceed_1099":
            add(cid, "warning", f"Deposits from {r['payer']} are more than its {r['form']}. {r['explanation']}")
        for i, w in enumerate(r.get("warnings", [])):
            add(f"{cid}:w{i}", "warning", f"{r['payer']}: {w}")

    sc = service.schedule_c()
    if sc["gross_income"] + MATH_TOLERANCE < rec["reported_on_1099s_total"]:
        add("income_below_1099", "blocker",
            f"Gross income (${sc['gross_income']:,.2f}) is less than the 1099s you got (${rec['reported_on_1099s_total']:,.2f}). "
            "The IRS matches 1099s against returns, so this usually gets a notice.", can_acknowledge=True)

    for s in db.rows("SELECT * FROM statements ORDER BY id"):
        label = f"{s['source_file']} ({s['account']})"
        if s["difference"] is None:
            add(f"statement_unverified:{s['id']}", "warning",
                f"{label}: no opening/closing balance found, so TaxPal couldn't check that every transaction was read.")
        elif abs(s["difference"]) >= 0.01:
            add(f"statement_unbalanced:{s['id']}", "blocker",
                f"{label}: the imported transactions are ${abs(s['difference']):,.2f} "
                f"{'more' if s['difference'] > 0 else 'less'} than the statement's balance change. A line was missed, "
                "read twice, or has the wrong sign. Fix it, or explain.", can_acknowledge=True)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for g in statements.coverage_gaps():
        missing = g["missing_months"]
        add(f"statement_gap:{g['account']}:{'-'.join(map(str, missing))}", "blocker",
            f"Account “{g['account']}” has no statement for {', '.join(months[m - 1] for m in missing)} {tax_year()['tax_year']}. "
            "Income or expenses from those months would be missing. Upload the statements, or explain why "
            "(for example, the account opened mid-year).",
            can_acknowledge=True)

    for r in risks.find():
        add(r["id"], "blocker", r["message"], can_acknowledge=True, items=r["items"])

    adjustments = db.rows("SELECT line, amount, note FROM adjustments")
    for key, message in _math_problems(sc, service.classified_txns(), adjustments):
        add(f"math:{key}", "blocker", f"Internal check failed: {message} Please report this as a bug.")

    open_blockers = [c for c in checks if c["severity"] == "blocker" and not c["acknowledged"]]
    return {"ok": not open_blockers, "open_blockers": len(open_blockers),
            "warnings": sum(c["severity"] == "warning" for c in checks),
            "checks": checks, "fingerprint": fingerprint(), "signoff": get_signoff()}


def acknowledge(check_id: str, note: str) -> dict:
    note = note.strip()
    if len(note) < 10:
        raise ValueError("Write a short explanation (at least 10 characters) so your tax pro knows why.")
    check = next((c for c in run_checks()["checks"] if c["id"] == check_id), None)
    if check is None:
        raise ValueError("That check is already resolved or no longer applies.")
    if not check["can_acknowledge"]:
        raise ValueError("This one has to be fixed, not explained.")
    with db.connect() as conn:
        conn.execute("INSERT INTO check_acks (check_id, note) VALUES (?, ?) ON CONFLICT (check_id) DO UPDATE"
                     " SET note = excluded.note, acked_at = datetime('now')", (check_id, note[:500]))
    return {"check_id": check_id, "note": note[:500]}


def get_signoff() -> dict | None:
    rows = db.rows("SELECT * FROM signoff WHERE id = 1")
    if not rows:
        return None
    r = rows[0]
    return {"signed_at": r["signed_at"], "statement": r["statement"], "current": r["fingerprint"] == fingerprint()}


def sign_off() -> dict:
    result = run_checks()
    if not result["ok"]:
        n = sum(c["severity"] == "blocker" and not c["acknowledged"] for c in result["checks"])
        raise ValueError(f"You can't sign off yet: {plural(n, 'open blocker')}. Fix or explain each one first.")
    with db.connect() as conn:
        conn.execute("INSERT INTO signoff (id, fingerprint, statement) VALUES (1, ?, ?) ON CONFLICT (id) DO UPDATE"
                     " SET fingerprint = excluded.fingerprint, statement = excluded.statement,"
                     " signed_at = datetime('now')", (result["fingerprint"], STATEMENT))
    return get_signoff()
