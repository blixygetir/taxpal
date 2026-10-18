"""Things that are easy to get wrong when sorting a year of bank data, and that the IRS
cares about. Each finding is a blocker: the user fixes the data or explains it in writing.

- signs_flipped:     a card CSV imported with charges as deposits (all expenses become income)
- transfer_counted:  a card payment or transfer counted as income or an expense (double counting)
- duplicate:         the same purchase in two accounts (e.g. a PayPal export and the card it charged)
- untraced_deposit:  money in marked "transfer" or "refund" with nothing to match it.
                     Hiding income this way is the most common underreporting mistake.
- home_expense:      apartment rent or home utilities deducted as business rent/utilities.
                     With the home office deduction that's counting the home twice (Pub 587)
- over_de_minimis:   a single item over $2,500 expensed as supplies/office/software instead
                     of depreciated (de minimis safe harbor limit)
- receipts:          lodging, or travel/meals/gifts of $75+, need receipts (Pub 463, ch. 5)
"""

import hashlib
from datetime import date

from . import db
from .categorize import _words
from .rules import tax_year, taxonomy
from .wording import plural

TRANSFER_WORDS = ("AUTOPAY", "PAYMENT THANK YOU", "ONLINE TRANSFER", "TRANSFER FROM", "TRANSFER TO",
                  "CREDIT CARD PAYMENT", "CARD PAYMENT", "ONLINE PAYMENT THANK")
MATCH_DAYS = 5          # a transfer shows up in the other account within this many days
DUPLICATE_DAYS = 3
UNTRACED_MIN = 100      # deposits smaller than this aren't worth a blocker
SIGN_FLIP_SHARE = 0.6   # most transactions in a normal account are money out


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _short(t: dict) -> dict:
    return {k: t[k] for k in ("txn_id", "date", "description", "amount", "account", "category") if k in t}


def _group_id(prefix: str, txns: list[dict]) -> str:
    # tied to the exact transactions, so a new one reopens an explained check
    ids = ",".join(str(t["txn_id"]) for t in sorted(txns, key=lambda t: t["txn_id"]))
    return f"{prefix}:{hashlib.sha256(ids.encode()).hexdigest()[:8]}"


def _txns() -> list[dict]:
    return db.rows("""SELECT t.id AS txn_id, t.date, t.description, t.merchant, t.amount, t.account,
                             c.category, c.source
                      FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id ORDER BY t.date""")


def find() -> list[dict]:
    txns = _txns()
    tax, ty = taxonomy(), tax_year()
    treatment = lambda t: tax[t["category"]]["treatment"] if t["category"] else None
    found = []

    def add(check_id, message, items):
        found.append({"id": check_id, "message": message, "items": [_short(t) for t in items]})

    # signs flipped on import: mostly money in, or known expense merchants as deposits
    out_only = [(kw.upper(), key) for key, c in tax.items() if c["direction"] == "out" for kw in c.get("keywords", [])]
    for account in sorted({t["account"] for t in txns}):
        rows = [t for t in txns if t["account"] == account]
        positive = [t for t in rows if t["amount"] > 0]
        looks_like_spending = [t for t in positive if any(kw in t["description"].upper() for kw, _ in out_only)]
        if len(rows) >= 10 and (len(positive) / len(rows) > SIGN_FLIP_SHARE or len(looks_like_spending) >= 3):
            add(f"signs_flipped:{account}",
                f"In account “{account}”, {len(positive)} of {len(rows)} transactions are money in, including "
                f"{len(looks_like_spending)} from merchants you usually pay (for example, "
                f"{(looks_like_spending or positive)[0]['description']}). The CSV may list purchases as positive numbers. "
                "Delete the data and re-import it with “Purchases show as positive numbers” ticked.",
                looks_like_spending[:10] or positive[:10])

    # transfers and card payments counted as income or expenses
    counted = [t for t in txns if t["category"] and treatment(t) not in ("neutral",)
               and any(w in t["description"].upper() for w in TRANSFER_WORDS)]
    for t in counted:
        add(f"transfer_counted:{t['txn_id']}",
            f"{t['date']} {t['description']} ({t['amount']:+,.2f}) looks like a transfer or card payment but is "
            f"counted as {tax[t['category']]['label']}. If the purchases are already in a card export, this counts them twice.",
            [t])

    # same purchase in two accounts (bucket by amount so this isn't n^2 over the year)
    by_amount: dict[int, list] = {}
    for t in txns:
        if t["amount"] < 0 and t["category"] and treatment(t) not in ("neutral", "nondeductible"):
            by_amount.setdefault(round(t["amount"] * 100), []).append(t)
    for bucket in by_amount.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if (a["account"] != b["account"] and _days(a["date"], b["date"]) <= DUPLICATE_DAYS
                        and _words(a["description"]) & _words(b["description"])):
                    pair = tuple(sorted((a["txn_id"], b["txn_id"])))
                    add(f"duplicate:{pair[0]}-{pair[1]}",
                        f"Possible double count: {a['description']} ({a['account']}, {a['date']}) and "
                        f"{b['description']} ({b['account']}, {b['date']}), both {-a['amount']:,.2f}. "
                        "Mark one as a transfer, or explain why both are real.", [a, b])

    # money in marked transfer/refund with nothing to trace it to
    outflows: dict[int, list] = {}
    for o in txns:
        if o["amount"] < 0:
            outflows.setdefault(round(o["amount"] * 100), []).append(o)
    for t in txns:
        if t["amount"] < UNTRACED_MIN or t["category"] not in ("transfer", "refund"):
            continue
        if t["category"] == "transfer":
            matched = any(o["account"] != t["account"] and _days(o["date"], t["date"]) <= MATCH_DAYS
                          for o in outflows.get(round(-t["amount"] * 100), []))
            why = "no matching transfer out of another imported account"
        else:
            words = _words(t["description"]) - {"REFUND", "RETURN", "CREDIT"}
            paid = -sum(o["amount"] for o in txns if o["amount"] < 0 and o["date"] <= t["date"] and words & _words(o["description"]))
            matched = bool(words) and paid >= t["amount"] - 0.01
            why = "no earlier purchase from this merchant big enough to be refunded"
        if not matched:
            add(f"untraced_deposit:{t['txn_id']}",
                f"{t['date']} {t['description']} (+{t['amount']:,.2f}) is marked {t['category']}, but there's {why}. "
                "Income marked as a transfer is a common way returns end up underreported. Import the other account, "
                "recategorize it, or explain where the money came from.", [t])

    # home rent/utilities deducted as business expenses
    home_words = [w.upper() for w in ty["home_expense_keywords"]]
    home = [t for t in txns if t["category"] in ("rent", "utilities", "repairs", "insurance")
            and any(w in t["description"].upper() for w in home_words)]
    if home:
        sqft = db.rows("SELECT value FROM profile WHERE key = 'home_office_sqft'")
        twice = " You also claim a home office, which already covers home costs, so this counts them twice." if sqft and float(sqft[0]["value"] or 0) > 0 else ""
        add(_group_id("home_expense", home),
            f"{plural(len(home), 'payment')} {'looks' if len(home) == 1 else 'look'} like home rent or utilities but {'is' if len(home) == 1 else 'are'} deducted as business "
            f"{', '.join(sorted({tax[t['category']]['label'].lower() for t in home}))}.{twice} "
            "Home costs only count through the home office deduction (Pub 587).", home)

    # big items expensed instead of depreciated
    limit = ty["de_minimis_safe_harbor"]["limit"]
    big = [t for t in txns if t["category"] in ("supplies", "office", "software", "repairs") and -t["amount"] > limit]
    if big:
        add(_group_id("over_de_minimis", big),
            f"{plural(len(big), 'item')} over ${limit:,} {'is' if len(big) == 1 else 'are'} deducted as everyday expenses. Above that amount, an item usually has "
            f"to be depreciated or taken under section 179 (Form 4562) instead ({ty['de_minimis_safe_harbor']['source']}). "
            "Mark it as equipment, or explain why not (for example, it's several items on one invoice, each under the limit).", big)

    # receipts
    sub = ty["substantiation"]
    lodging = [w.upper() for w in sub["lodging_keywords"]]
    need = [t for t in txns if t["category"] in ("travel", "meals", "gifts")
            and (-t["amount"] >= sub["receipt_threshold"] or any(w in f" {t['description'].upper()}" for w in lodging))]
    if need:
        add(_group_id("receipts", need),
            f"{plural(len(need), 'travel, meal or gift expense')} {'needs' if len(need) == 1 else 'need'} a receipt: lodging at any amount, and anything else at "
            f"${sub['receipt_threshold']} or more ({sub['source']}). A bank statement alone isn't enough. "
            "Confirm you have them by explaining where they're kept.", need)
    return found
