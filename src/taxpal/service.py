import json

from . import db, engine

PROFILE_FIELDS = {
    "filing_status": "One of: single, married_joint, married_separate, head_of_household, qualifying_surviving_spouse.",
    "w2_ss_wages": "Social Security wages from any W-2 job in 2026 (Form W-2, box 3).",
    "w2_medicare_wages": "Medicare wages from Form W-2, box 5. Defaults to box 3.",
    "home_office_sqft": "Square feet used regularly and exclusively for business (simplified method, max 300).",
    "vehicle_method": "One of: standard (mileage rate) or actual (actual expenses x business %).",
    "vehicle_business_pct": "Business-use percentage of the vehicle (actual method only).",
    "prior_year_total_tax": "Total tax on your 2026 Form 1040 (used for the 2027 estimated tax plan).",
    "prior_year_agi": "Adjusted gross income on your 2026 Form 1040.",
    "projected_tax": "Expected total tax for 2027, if you know it.",
    "expected_withholding": "Federal income tax expected to be withheld in 2027 (W-2 jobs).",
    "taxable_income_before_qbi": "Estimated 2026 taxable income before the QBI deduction.",
    "net_capital_gain": "Net capital gain for 2026 (for the QBI limit).",
}
DEFAULTS = {"filing_status": "single", "vehicle_method": "standard", "w2_ss_wages": 0}
STATUSES = {"single", "married_joint", "married_separate", "head_of_household", "qualifying_surviving_spouse"}


def get_profile() -> dict:
    stored = {r["key"]: json.loads(r["value"]) for r in db.rows("SELECT key, value FROM profile")}
    return {**DEFAULTS, **stored}


def update_profile(changes: dict) -> dict:
    unknown = set(changes) - set(PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown profile field(s): {', '.join(sorted(unknown))}.")
    if "filing_status" in changes and changes["filing_status"] not in STATUSES:
        raise ValueError("Filing status must be single, married filing jointly, married filing separately, "
                         "head of household or qualifying surviving spouse.")
    if "vehicle_method" in changes and changes["vehicle_method"] not in ("standard", "actual"):
        raise ValueError("Vehicle deduction must be the standard mileage rate or actual expenses.")
    for k, v in changes.items():
        if k not in ("filing_status", "vehicle_method") and v is not None and float(v) < 0:
            raise ValueError(f"{k.replace('_', ' ').capitalize()} can't be negative.")
    with db.connect() as conn:
        for k, v in changes.items():
            conn.execute("INSERT INTO profile (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                         (k, json.dumps(v)))
    return get_profile()


def classified_txns() -> list[dict]:
    return db.rows("""SELECT t.id, t.date, t.description, t.amount, c.category, c.business_purpose,
                             c.business_use_pct, c.recipient, c.needs_review
                      FROM transactions t JOIN classifications c ON c.txn_id = t.id""")


def status() -> dict:
    [s] = db.rows("""SELECT COUNT(*) AS transactions,
                            SUM(c.txn_id IS NOT NULL) AS categorized,
                            SUM(c.txn_id IS NULL) AS uncategorized,
                            SUM(COALESCE(c.needs_review, 0)) AS needs_review
                     FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id""")
    [ir] = db.rows("SELECT COUNT(*) AS n FROM info_returns")
    [mi] = db.rows("SELECT COUNT(*) AS n FROM mileage")
    profile = get_profile()
    return {**{k: v or 0 for k, v in s.items()}, "info_returns": ir["n"], "mileage_trips": mi["n"],
            "profile_missing": [k for k in ("home_office_sqft", "prior_year_total_tax") if k not in profile]}


def imported_files() -> list[dict]:
    """What came from which file: transaction files (CSV or statement) and 1099 lists."""
    files = db.rows("""SELECT t.source_file AS name, t.account, COUNT(*) AS items, MIN(t.date) AS first_date,
                              MAX(t.date) AS last_date, s.kind AS statement_kind, s.imported_at
                       FROM transactions t LEFT JOIN statements s ON s.source_file = t.source_file AND s.account = t.account
                       GROUP BY t.source_file, t.account ORDER BY MIN(t.date)""")
    for f in files:
        f["type"] = "statement" if f.pop("statement_kind") else "transactions"
    files += [{"name": r["name"], "type": "1099s", "account": None, "items": r["items"], "first_date": None,
               "last_date": None, "imported_at": None}
              for r in db.rows("SELECT source_file AS name, COUNT(*) AS items FROM info_returns GROUP BY source_file")]
    [mi] = db.rows("SELECT COUNT(*) AS n, MIN(date) AS first_date, MAX(date) AS last_date FROM mileage")
    if mi["n"]:  # trips aren't tied to a file (the agent can log them too)
        files.append({"name": "Mileage log", "type": "mileage", "account": None, "items": mi["n"],
                      "first_date": mi["first_date"], "last_date": mi["last_date"], "imported_at": None})
    return files


def schedule_c() -> dict:
    txns = classified_txns()
    result = engine.schedule_c(txns, get_profile(), db.rows("SELECT * FROM mileage"),
                               db.rows("SELECT line, amount, note FROM adjustments"))
    s = status()
    result["complete"] = s["uncategorized"] == 0 and s["needs_review"] == 0
    if not result["complete"]:
        result["flags"].insert(0, f"Provisional: {s['uncategorized']} uncategorized and {s['needs_review']} flagged "
                                  f"{'transaction' if s['uncategorized'] + s['needs_review'] == 1 else 'transactions'} still need review.")
    return result


def se_and_qbi() -> dict:
    sc = schedule_c()
    p = get_profile()
    se = engine.se_tax(sc["net_profit"], float(p.get("w2_ss_wages") or 0), p["filing_status"],
                       None if p.get("w2_medicare_wages") is None else float(p["w2_medicare_wages"]))
    q = engine.qbi(sc["net_profit"], se["deductible_half"], sc["schedule1_items"], p)
    return {"net_profit": sc["net_profit"], "schedule_se": se, "qbi": q,
            "schedule1_adjustments": {"deductible_half_se_tax": se["deductible_half"], **sc["schedule1_items"]},
            "complete": sc["complete"]}


def estimated_tax() -> dict:
    return engine.estimated_tax_plan(get_profile())
