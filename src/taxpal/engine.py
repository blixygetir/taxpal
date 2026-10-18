"""Tax math for TY2026. Plain functions over dicts, no db access, so they're easy to test."""

from collections import defaultdict

from .rules import category, tax_year, taxonomy

EXPENSE_LINES = ["8", "9", "10", "11", "13", "15", "16b", "17", "18", "20b", "21", "22", "23",
                 "24a", "24b", "25", "27a"]


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def mileage_deduction(trips: list[dict], ty: dict | None = None) -> dict:
    ty = ty or tax_year()
    by_period = []
    total = 0.0
    for p in ty["mileage"]["periods"]:
        start, end = str(p["start"]), str(p["end"])
        miles = sum(t["miles"] for t in trips if start <= t["date"] <= end)
        amount = miles * p["rate"]
        total += amount
        by_period.append({"from": start, "to": end, "rate": p["rate"], "miles": miles, "amount": _r(amount)})
    outside = [t for t in trips if not any(str(p["start"]) <= t["date"] <= str(p["end"]) for p in ty["mileage"]["periods"])]
    return {"amount": _r(total), "periods": by_period, "trips_outside_tax_year": len(outside),
            "source": ty["mileage"]["source"]}


def schedule_c(txns: list[dict], profile: dict, trips: list[dict] = (), adjustments: list[dict] = (),
               ty: dict | None = None) -> dict:
    # amounts are signed: + money in, - money out
    ty = ty or tax_year()
    lines: dict[str, float] = defaultdict(float)
    excluded, capital, flags = [], [], []
    schedule1: dict[str, float] = defaultdict(float)
    tax_paid = 0.0
    gifts_by_recipient: dict[str, float] = defaultdict(float)
    missing_purpose = 0
    vehicle_method = profile.get("vehicle_method", "standard")

    for t in txns:
        cat = category(t["category"])
        treatment, line = cat["treatment"], cat["line"]
        out = -t["amount"]  # outflow as a positive number
        if cat.get("needs") and "business_purpose" in cat["needs"] and not t.get("business_purpose"):
            missing_purpose += 1

        if treatment == "income":
            lines["1"] += t["amount"]
        elif treatment in ("deductible", "travel", "car_extra"):
            lines[line] += out
        elif treatment == "business_use":
            pct = t.get("business_use_pct")
            if pct is None:
                excluded.append({**_ref(t), "reason": "business-use % not provided yet"})
            else:
                lines[line] += out * pct / 100
        elif treatment == "meals":
            lines[line] += out * ty["meals"]["deductible_pct"]
        elif treatment == "gift":
            gifts_by_recipient[t.get("recipient") or "(recipient unknown)"] += out
        elif treatment == "car_actual":
            if vehicle_method == "actual":
                lines[line] += out * float(profile.get("vehicle_business_pct") or 0) / 100
            else:
                excluded.append({**_ref(t), "reason": "fuel/repairs aren't deductible with the standard mileage rate (Pub 463)"})
        elif treatment == "capital":
            capital.append(_ref(t))
        elif treatment == "schedule1":
            schedule1[t["category"]] += out
        elif treatment == "tax_payment":
            tax_paid += out
        elif treatment == "nondeductible":
            excluded.append({**_ref(t), "reason": cat["rule"]})
        # neutral (transfers, card payments, refunds): nothing to do

    limit = ty["gifts"]["per_recipient_limit"]
    for recipient, total in gifts_by_recipient.items():
        lines["27a"] += min(total, limit)
        if total > limit:
            flags.append(f"Gifts to {recipient} capped at ${limit} (Pub 463); ${_r(total - limit)} not deductible.")

    mileage = None
    if vehicle_method == "standard" and trips:
        mileage = mileage_deduction(list(trips), ty)
        lines["9"] += mileage["amount"]

    for a in adjustments:
        lines[a["line"]] += a["amount"]

    gross_income = _r(lines["1"])  # line 7: no returns/allowances or COGS in MVP
    total_expenses = _r(sum(lines[l] for l in EXPENSE_LINES))
    tentative_profit = _r(gross_income - total_expenses)

    sqft = min(float(profile.get("home_office_sqft") or 0), ty["home_office_simplified"]["max_sqft"])
    home_office_full = sqft * ty["home_office_simplified"]["rate_per_sqft"]
    home_office = _r(min(home_office_full, max(tentative_profit, 0)))
    if home_office < home_office_full:
        flags.append("The home office deduction is limited to your tentative profit (Pub 587, simplified method).")
    if float(profile.get("home_office_sqft") or 0) > ty["home_office_simplified"]["max_sqft"]:
        flags.append("The simplified home office method is capped at 300 sq ft. The regular method (Form 8829) may give a larger deduction.")

    if capital:
        flags.append(f"{len(capital)} equipment or capital {'purchase needs' if len(capital) == 1 else 'purchases need'} depreciation or section 179 on Form 4562. Ask your tax professional about these.")
    if missing_purpose:
        flags.append(f"{missing_purpose} meal, travel or parking {'item has' if missing_purpose == 1 else 'items have'} no business purpose recorded (Pub 463 recordkeeping).")
    if lines["11"] > 0:
        flags.append("You paid contract labor. If any one contractor received $2,000 or more, you may need to file a 1099-NEC.")

    return {
        "tax_year": ty["tax_year"],
        "lines": {l: {"name": _line_name(l), "amount": _r(v)} for l, v in sorted(lines.items(), key=lambda kv: _line_sort(kv[0])) if _r(v)},
        "gross_income": gross_income,
        "total_expenses": total_expenses,
        "tentative_profit": tentative_profit,
        "home_office": home_office,
        "net_profit": _r(tentative_profit - home_office),
        "mileage": mileage,
        "schedule1_items": {k: _r(v) for k, v in schedule1.items()},
        "estimated_tax_paid": _r(tax_paid),
        "capital_purchases": capital,
        "excluded": excluded,
        "flags": flags,
    }


def se_tax(net_profit: float, w2_ss_wages: float = 0.0, filing_status: str = "single",
           w2_medicare_wages: float | None = None, ty: dict | None = None) -> dict:
    """Schedule SE, part I."""
    ty = ty or tax_year()
    se = ty["se_tax"]
    earnings = _r(max(net_profit, 0) * se["net_earnings_factor"])  # line 4a/6
    if earnings < se["filing_threshold"]:
        return {"net_earnings": earnings, "must_file_schedule_se": False, "social_security": 0.0,
                "medicare": 0.0, "se_tax": 0.0, "deductible_half": 0.0, "flags": [], "source": se["source"]}
    ss_room = max(se["social_security_wage_base"] - w2_ss_wages, 0)
    social_security = _r(min(earnings, ss_room) * se["social_security_rate"])
    medicare = _r(earnings * se["medicare_rate"])
    total = _r(social_security + medicare)
    flags = []
    am = ty["additional_medicare"]
    threshold = am["thresholds"].get(filing_status, am["thresholds"]["single"])
    medicare_wages = w2_ss_wages if w2_medicare_wages is None else w2_medicare_wages
    if medicare_wages + earnings > threshold:
        flags.append(f"Wages + SE earnings exceed ${threshold:,}: Additional Medicare Tax (0.9%, Form 8959) likely applies.")
    return {"net_earnings": earnings, "must_file_schedule_se": True, "social_security": social_security,
            "medicare": medicare, "se_tax": total, "deductible_half": _r(total * se["deductible_half"]),
            "flags": flags, "source": se["source"]}


def qbi(net_profit: float, deductible_half_se: float, schedule1_items: dict, profile: dict,
        ty: dict | None = None) -> dict:
    """Form 8995 (simplified). Includes the new $400 minimum for 2026."""
    ty = ty or tax_year()
    q = ty["qbi"]
    status = profile.get("filing_status", "single")
    qbi_amount = _r(net_profit - deductible_half_se - schedule1_items.get("health_insurance", 0)
                    - schedule1_items.get("retirement", 0))
    flags = ["This assumes you materially participate in an active business that isn't a specified service business. Confirm with your tax professional."]
    if qbi_amount <= 0:
        return {"qbi": qbi_amount, "deduction": 0.0, "form": "8995", "flags": flags + [
            "Qualified business loss. It carries forward to next year (Form 8995)."], "source": q["source"]}

    tentative = _r(qbi_amount * q["rate"])
    ti = profile.get("taxable_income_before_qbi")
    threshold = q["thresholds"].get(status, q["thresholds"]["single"])
    if ti is None:
        deduction = tentative
        flags.append("Your profile has no taxable income yet, so this is before the taxable income limit.")
    elif float(ti) > threshold:
        return {"qbi": qbi_amount, "deduction": None, "form": "8995-A", "flags": flags + [
            f"Your taxable income is above ${threshold:,}, so Form 8995-A is required. TaxPal doesn't calculate it."], "source": q["source"]}
    else:
        limit = _r(max(float(ti) - float(profile.get("net_capital_gain") or 0), 0) * q["rate"])
        deduction = min(tentative, limit)
    if qbi_amount >= q["minimum_deduction_qbi_floor"] and deduction < q["minimum_deduction"]:
        deduction = q["minimum_deduction"]
        flags.append(f"2026 minimum QBI deduction of ${q['minimum_deduction']} applied (IRC 199A(i)).")
    return {"qbi": qbi_amount, "tentative_20pct": tentative, "deduction": _r(deduction), "form": "8995",
            "flags": flags, "source": q["source"]}


def estimated_tax_plan(profile: dict, ty: dict | None = None) -> dict:
    """1040-ES: the safe harbor amount, split into 4 payments."""
    ty = ty or tax_year()
    e = ty["estimated_tax"]
    status = profile.get("filing_status", "single")
    prior_tax, prior_agi = profile.get("prior_year_total_tax"), profile.get("prior_year_agi")
    projected = profile.get("projected_tax")
    withholding = float(profile.get("expected_withholding") or 0)

    options = {}
    if projected is not None:
        options["90% of this year's projected tax"] = _r(float(projected) * e["current_year_pct"])
    if prior_tax is not None:
        high_agi = e["high_income_agi"].get(status, e["high_income_agi"]["default"])
        pct = e["prior_year_pct_high_income"] if prior_agi is not None and float(prior_agi) > high_agi else e["prior_year_pct"]
        options[f"{int(pct * 100)}% of last year's tax"] = _r(float(prior_tax) * pct)
    if not options:
        return {"plan_year": e["plan_year"], "required_annual_payment": None,
                "missing": ["your 2026 total tax and adjusted gross income", "or your expected 2027 tax"], "source": e["source"]}

    basis, required = min(options.items(), key=lambda kv: kv[1])
    to_pay = _r(max(required - withholding, 0))
    quarterly = _r(to_pay / 4)
    return {
        "plan_year": e["plan_year"], "options": options, "safe_harbor_basis": basis,
        "required_annual_payment": required, "expected_withholding": withholding,
        "estimated_payments_needed": to_pay,
        "quarters": [{"due": str(d), "amount": quarterly} for d in e["due_dates"]],
        "notes": [f"No underpayment penalty if you'd owe less than ${e['no_penalty_below']:,} after withholding (Pub 505).",
                  "Due dates that fall on a weekend or holiday move to the next business day. Confirm them on the 2027 Form 1040-ES."],
        "source": e["source"],
    }


def _ref(t: dict) -> dict:
    return {"txn_id": t.get("id") or t.get("txn_id"), "date": t.get("date"), "description": t.get("description"),
            "amount": t["amount"], "category": t["category"]}


_LINE_NAMES = {"1": "Gross receipts or sales", "30": "Business use of home"}


def _line_name(line: str) -> str:
    if line in _LINE_NAMES:
        return _LINE_NAMES[line]
    return next(c["line_name"] for c in taxonomy().values() if c["line"] == line)


def _line_sort(line: str) -> tuple[int, str]:
    digits = "".join(ch for ch in line if ch.isdigit())
    return int(digits), line
