import pytest

from taxpal import engine


def txn(category, amount, **facts):
    return {"id": 1, "date": "2026-05-01", "description": category, "amount": amount, "category": category, **facts}


INCOME = txn("income", 10000)


def lines(result):
    return {k: v["amount"] for k, v in result["lines"].items()}


def test_meals_are_50_percent():
    r = engine.schedule_c([INCOME, txn("meals", -100, business_purpose="Client lunch")], {})
    assert lines(r)["24b"] == 50


def test_gifts_capped_at_25_per_recipient():
    r = engine.schedule_c([INCOME, txn("gifts", -20, recipient="A"), txn("gifts", -15, recipient="A"),
                           txn("gifts", -10, recipient="B")], {})
    assert lines(r)["27a"] == 35
    assert any("capped at $25" in f for f in r["flags"])


def test_mileage_rate_splits_on_july_1():
    trips = [{"date": "2026-06-30", "miles": 100}, {"date": "2026-07-01", "miles": 100}]
    m = engine.mileage_deduction(trips)
    assert m["amount"] == pytest.approx(72.50 + 76.00)
    assert [p["miles"] for p in m["periods"]] == [100, 100]


def test_mileage_goes_to_line_9_only_with_standard_method():
    trips = [{"date": "2026-03-01", "miles": 10}]
    assert lines(engine.schedule_c([INCOME], {"vehicle_method": "standard"}, trips))["9"] == pytest.approx(7.25)
    assert "9" not in lines(engine.schedule_c([INCOME], {"vehicle_method": "actual"}, trips))


def test_fuel_excluded_under_standard_mileage():
    r = engine.schedule_c([INCOME, txn("car_actual", -50)], {"vehicle_method": "standard"})
    assert "9" not in lines(r)
    assert r["excluded"][0]["category"] == "car_actual"


def test_fuel_uses_vehicle_business_pct_under_actual_method():
    r = engine.schedule_c([INCOME, txn("car_actual", -100)], {"vehicle_method": "actual", "vehicle_business_pct": 40})
    assert lines(r)["9"] == 40


def test_business_use_percentage():
    r = engine.schedule_c([INCOME, txn("phone_internet", -100, business_use_pct=60)], {})
    assert lines(r)["27a"] == 60


def test_business_use_without_pct_is_excluded_not_guessed():
    r = engine.schedule_c([INCOME, txn("phone_internet", -100)], {})
    assert "27a" not in lines(r)
    assert r["excluded"]


def test_home_office_simplified_and_capped_at_profit():
    r = engine.schedule_c([txn("income", 1000)], {"home_office_sqft": 400})
    assert r["home_office"] == 1000          # $1,500 max, limited to $1,000 tentative profit
    assert r["net_profit"] == 0
    full = engine.schedule_c([INCOME], {"home_office_sqft": 200})
    assert full["home_office"] == 1000       # 200 sq ft x $5


def test_non_schedule_c_items():
    r = engine.schedule_c([INCOME, txn("health_insurance", -400), txn("estimated_tax_payment", -1500),
                           txn("entertainment", -200), txn("capital_purchase", -2000), txn("transfer", -500)], {})
    assert r["total_expenses"] == 0
    assert r["schedule1_items"] == {"health_insurance": 400}
    assert r["estimated_tax_paid"] == 1500
    assert len(r["capital_purchases"]) == 1
    assert r["net_profit"] == 10000


def test_adjustments_gross_up_1099k():
    r = engine.schedule_c([INCOME], {}, adjustments=[{"line": "1", "amount": 300}, {"line": "10", "amount": 300}])
    assert r["gross_income"] == 10300 and lines(r)["10"] == 300 and r["net_profit"] == 10000


def test_se_tax():
    se = engine.se_tax(100000)
    assert se["net_earnings"] == pytest.approx(92350)
    assert se["social_security"] == pytest.approx(11451.40)
    assert se["medicare"] == pytest.approx(2678.15)
    assert se["se_tax"] == pytest.approx(14129.55)
    assert se["deductible_half"] == pytest.approx(7064.78, abs=0.01)


def test_se_tax_respects_wage_base():
    se = engine.se_tax(100000, w2_ss_wages=180000)
    assert se["social_security"] == pytest.approx(4500 * 0.124)
    assert se["flags"]  # Additional Medicare Tax


def test_no_se_tax_under_400():
    se = engine.se_tax(400)  # 400 x 0.9235 = 369.40
    assert not se["must_file_schedule_se"] and se["se_tax"] == 0


def test_qbi_limited_by_taxable_income():
    q = engine.qbi(50000, 3000, {}, {"taxable_income_before_qbi": 40000})
    assert q["qbi"] == 47000 and q["deduction"] == 8000


def test_qbi_minimum_deduction():
    q = engine.qbi(1500, 0, {}, {})
    assert q["deduction"] == 400


def test_qbi_above_threshold_needs_8995a():
    q = engine.qbi(300000, 0, {}, {"taxable_income_before_qbi": 250000})
    assert q["form"] == "8995-A" and q["deduction"] is None


def test_estimated_tax_uses_smaller_safe_harbor():
    plan = engine.estimated_tax_plan({"prior_year_total_tax": 10000, "prior_year_agi": 160000,
                                      "projected_tax": 11500, "expected_withholding": 2000})
    assert plan["options"] == {"90% of this year's projected tax": 10350, "110% of last year's tax": 11000}
    assert plan["required_annual_payment"] == 10350
    assert [q["amount"] for q in plan["quarters"]] == [2087.5] * 4
    assert plan["quarters"][-1]["due"] == "2028-01-18"


def test_estimated_tax_needs_inputs():
    assert engine.estimated_tax_plan({})["required_annual_payment"] is None
