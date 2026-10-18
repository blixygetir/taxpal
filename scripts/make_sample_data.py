"""Fake 2026 data for a made-up freelance designer, written to data/sample/.
labels.csv has the correct category for every transaction (for checking accuracy)."""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
rng = random.Random(2026)


def d(month: int, day: int) -> date:
    return date(2026, month, min(day, 28))


bank, card, labels = [], [], []
card_payments = []


def add(rows, when, desc, amount, label):
    rows.append((when, desc, round(amount, 2)))
    # labels use the API's signs (+ = money in); card rows store charges as positive
    signed = amount if rows is bank else -amount
    labels.append((when.isoformat(), desc, round(signed, 2), label))


# income
stripe_gross, stripe_fees = 0.0, 0.0
for m in range(1, 13):
    gross = rng.choice([1450, 1800, 2100, 2600, 1950])
    fee = round(gross * 0.029 + 0.30 * rng.randint(3, 8), 2)
    stripe_gross += gross
    stripe_fees += fee
    add(bank, d(m, 3), f"STRIPE PAYOUT ST-{rng.randint(100000, 999999)}", gross - fee, "income")
for m in (2, 6, 10):
    add(bank, d(m, 12), "ACH CREDIT ACME DESIGN LLC PAYROLL VENDOR PMT", 6000, "income")
for m, amt in ((4, 2000), (8, 2500)):
    add(bank, d(m, 20), "NORTHWIND STUDIOS INC ACH DEPOSIT", amt, "income")
add(bank, d(5, 9), "MOBILE DEPOSIT REF 88213", 1200, "income")           # check from a small client, no 1099
add(bank, d(7, 4), "ZELLE FROM SAM P", 60, "personal")
add(bank, d(3, 1), "ONLINE TRANSFER FROM SAVINGS", 3000, "transfer")
add(bank, d(9, 14), "ADOBE REFUND", 54.99, "refund")

# recurring bank expenses
for m in range(1, 13):
    add(bank, d(m, 1), "RENT PAYMENT - OAK ST APTS", -2100, "home_utilities")
    add(bank, d(m, 6), "PG&E WEB ONLINE PAYMENT", -rng.uniform(70, 140), "home_utilities")
    add(bank, d(m, 8), "VERIZON WIRELESS PAYMENT", -85, "phone_internet")
    add(bank, d(m, 10), "COMCAST XFINITY INTERNET", -79.99, "phone_internet")
    add(bank, d(m, 2), "BLUE CROSS CA PREMIUM", -412, "health_insurance")
    add(bank, d(m, 15), "WEWORK MEMBERSHIP", -299, "rent")
    card_payments.append(round(rng.uniform(600, 1100), 2))
    add(bank, d(m, 25), "AUTOPAY PAYMENT CREDIT CARD", -card_payments[-1], "transfer")
    add(card, d(m, 4), "ADOBE *CREATIVE CLOUD", 59.99, "software")
    add(card, d(m, 5), "FIGMA MONTHLY", 15, "software")
    add(card, d(m, 7), "GITHUB INC", 4, "software")
    add(card, d(m, 9), "NETFLIX.COM", 15.49, "personal")
    add(card, d(m, 11), "WHOLE FOODS MKT #10234", rng.uniform(80, 160), "personal")
for m, day in ((1, 15), (4, 15), (6, 15), (9, 15)):
    add(bank, d(m, day), "IRS USATAXPYMT 2026 ES", -1500, "estimated_tax_payment")
add(bank, d(1, 20), "CITY OF OAKLAND BUSINESS LICENSE", -95, "taxes_licenses")
add(bank, d(3, 22), "HISCOX INSURANCE LIABILITY POLICY", -540, "insurance")
add(bank, d(2, 28), "MONTHLY SERVICE FEE", -12, "bank_fees")
add(bank, d(11, 3), "SEP-IRA CONTRIB VANGUARD", -4000, "retirement")

# card one-offs
add(card, d(2, 3), "GOOGLE ADS 3381-2210", 250, "advertising")
add(card, d(5, 3), "GOOGLE ADS 3381-2210", 180, "advertising")
add(card, d(1, 18), "APPLE STORE R102 MACBOOK PRO", 2399, "capital_purchase")
add(card, d(3, 9), "STAPLES 00123", 64.20, "office")
add(card, d(3, 10), "USPS PO 0532", 18.40, "office")
add(card, d(4, 2), "UDEMY ONLINE COURSES", 89.99, "education")
add(card, d(4, 18), "LEGALZOOM.COM", 149, "legal_professional")
add(card, d(6, 21), "DELTA AIR 0062381 SFO-AUS", 412.60, "travel")
add(card, d(6, 22), "MARRIOTT AUSTIN DOWNTOWN", 689.10, "travel")
add(card, d(6, 22), "TST* FRANKLIN BBQ AUSTIN", 74.50, "meals")
add(card, d(6, 23), "UBER TRIP HELP.UBER.COM", 31.20, "travel")
add(card, d(2, 14), "SQ *BLUE BOTTLE CAFE", 18.75, "meals")
add(card, d(8, 7), "CHIPOTLE 2291", 23.10, "meals")
add(card, d(10, 2), "STARBUCKS STORE 1182", 12.85, "personal")
add(card, d(7, 30), "SHELL OIL 5744", 52.30, "car_actual")
add(card, d(8, 12), "SFO PARKING GARAGE", 48, "car_extra")
add(card, d(9, 3), "TICKETMASTER WARRIORS", 220, "entertainment")
add(card, d(11, 28), "AMAZON MKTPLACE PMTS", 46.99, "supplies")             # sketchbooks + markers
add(card, d(12, 10), "AMAZON MKTPLACE PMTS", 129.00, "personal")
add(card, d(12, 15), "SEE'S CANDIES GIFT BOX", 90, "gifts")                  # 3 boxes for 3 clients
add(card, d(7, 19), "THE UPWORK FREELANCER - J. LEE", 1800, "contract_labor")
# the card side of the same payments, a day later
for m, amount in enumerate(card_payments, start=1):
    add(card, d(m, 26), "PAYMENT THANK YOU", -amount, "transfer")
add(card, d(8, 1), "INTEREST CHARGE PURCHASES", 18.22, "interest")


def write(name, header, rows):
    with open(OUT / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


OUT.mkdir(parents=True, exist_ok=True)
write("bank_checking.csv", ["Posting Date", "Description", "Amount"],
      [(w.strftime("%m/%d/%Y"), desc, f"{amt:.2f}") for w, desc, amt in sorted(bank)])
# card export has Debit/Credit columns instead of a signed amount
write("credit_card.csv", ["Transaction Date", "Description", "Debit", "Credit"],
      [(w.isoformat(), desc, f"{amt:.2f}" if amt > 0 else "", f"{-amt:.2f}" if amt < 0 else "")
       for w, desc, amt in sorted(card)])
write("info_returns.csv", ["form", "payer", "payer_tin", "amount"], [
    ("1099-K", "Stripe, Inc.", "XX-XXX4521", f"{stripe_gross:.2f}"),
    ("1099-NEC", "Acme Design LLC", "XX-XXX7710", "18000.00"),
    ("1099-NEC", "Northwind Studios Inc", "XX-XXX0943", "4500.00"),
])
write("mileage.csv", ["date", "miles", "purpose"], [
    ("2026-03-04", 42, "Client kickoff at Acme Design, San Jose"),
    ("2026-05-19", 38, "Photo shoot location scouting for Northwind"),
    ("2026-08-12", 27, "Drive to SFO for client conference"),
    ("2026-10-08", 42, "Acme Design brand review"),
])
write("labels.csv", ["date", "description", "amount", "expected_category"], sorted(labels))
print(f"Wrote {len(bank)} bank + {len(card)} card transactions to {OUT}")
print(f"Stripe gross {stripe_gross:.2f}, fees {stripe_fees:.2f}")
