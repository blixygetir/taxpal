import csv
import io
import json
import re
from datetime import date, datetime

from . import db
from .rules import tax_year

DATE_COLS = ("date", "posting date", "transaction date", "trans. date", "post date")
DESC_COLS = ("description", "details", "memo", "payee", "name")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
PREFIXES = ("POS PURCHASE ", "DEBIT CARD PURCHASE ", "CHECKCARD ", "ACH DEBIT ", "ACH CREDIT ", "SQ *", "TST* ")


class ImportFormatError(ValueError):
    pass


def normalize_merchant(description: str) -> str:
    m = description.upper().strip()
    for p in PREFIXES:
        if m.startswith(p):
            m = m[len(p):]
    m = re.sub(r"#\S+", " ", m)
    m = re.sub(r"\b\d{3,}\b", " ", m)          # store numbers, ref ids
    m = re.sub(r"\b\d{2}/\d{2}\b", " ", m)      # embedded dates
    return re.sub(r"\s+", " ", m).strip(" *-")


def _parse_date(value: str) -> date:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ImportFormatError(f"Couldn't read the date “{value}”. Use a format such as 2026-03-31 or 03/31/2026.")


def _money(value: str) -> float:
    v = value.strip().replace("$", "").replace(",", "")
    if not v:
        return 0.0
    if v.startswith("(") and v.endswith(")"):
        v = "-" + v[1:-1]
    return round(float(v), 2)


def _pick(headers: dict[str, str], options) -> str | None:
    return next((headers[o] for o in options if o in headers), None)


def parse_transactions_csv(text: str, purchases_positive: bool = False) -> list[dict]:
    """Rows come back with positive = money in. Some card exports show charges as
    positive numbers, that's what purchases_positive is for."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ImportFormatError("CSV has no header row")
    headers = {h.strip().lower(): h for h in reader.fieldnames}
    date_col, desc_col = _pick(headers, DATE_COLS), _pick(headers, DESC_COLS)
    amount_col = headers.get("amount")
    debit_col, credit_col = headers.get("debit"), headers.get("credit")
    if not date_col or not desc_col or not (amount_col or (debit_col and credit_col)):
        raise ImportFormatError(f"The CSV needs date, description and amount columns (or debit and credit). Found: {', '.join(reader.fieldnames)}.")

    out = []
    for row in reader:
        if not (row.get(date_col) or "").strip():
            continue
        if amount_col:
            amount = _money(row[amount_col])
            if purchases_positive:
                amount = -amount
        else:
            amount = _money(row[credit_col] or "") - abs(_money(row[debit_col] or ""))
        out.append({"date": _parse_date(row[date_col]).isoformat(),
                    "description": row[desc_col].strip(), "amount": round(amount, 2)})
    return out


def import_transactions(text: str, account: str, source_file: str, purchases_positive: bool = False) -> dict:
    return import_rows(parse_transactions_csv(text, purchases_positive), account, source_file)


def import_rows(parsed: list[dict], account: str, source_file: str) -> dict:
    """Rows of {date, description, amount}, positive = money in. Skips other years and duplicates."""
    year = str(tax_year()["tax_year"])
    imported = duplicates = other_year = 0
    with db.connect() as conn:
        for t in parsed:
            if not t["date"].startswith(year):
                other_year += 1
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions (date, description, merchant, amount, account, source_file)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (t["date"], t["description"], normalize_merchant(t["description"]), t["amount"], account, source_file))
            if cur.rowcount:
                imported += 1
            else:
                duplicates += 1
    return {"imported": imported, "duplicates_skipped": duplicates, "outside_tax_year_skipped": other_year}


def import_info_returns(text: str, source_file: str) -> dict:
    """CSV or a JSON list. We only keep the last 4 of the payer TIN."""
    stripped = text.strip()
    records = json.loads(stripped) if stripped.startswith("[") else list(csv.DictReader(io.StringIO(stripped)))
    imported = 0
    with db.connect() as conn:
        for r in records:
            # "Payer TIN" and "payer_tin" are the same column
            r = {re.sub(r"[\s_]+", "_", k.strip().lower()): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            form = str(r.get("form", "")).upper()
            if form not in ("1099-NEC", "1099-K", "1099-MISC"):
                raise ImportFormatError(f"Unsupported form “{form}”. Use 1099-NEC, 1099-K or 1099-MISC.")
            tin = re.sub(r"\D", "", str(r.get("payer_tin") or r.get("payer_tin_last4") or ""))
            cur = conn.execute(
                "INSERT OR IGNORE INTO info_returns (form, payer, payer_tin_last4, amount, source_file) VALUES (?, ?, ?, ?, ?)",
                (form, r["payer"], tin[-4:] or None, _money(str(r["amount"])), source_file))
            imported += cur.rowcount
    return {"imported": imported}


def import_mileage(text: str) -> dict:
    year = str(tax_year()["tax_year"])
    imported = skipped = 0
    with db.connect() as conn:
        for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
            when, miles, purpose = _parse_date(r["date"]).isoformat(), float(r["miles"]), r.get("purpose", "")
            if not when.startswith(year) or miles <= 0 or len(purpose) < 3:
                skipped += 1
                continue
            conn.execute("INSERT INTO mileage (date, miles, purpose) VALUES (?, ?, ?)", (when, miles, purpose))
            imported += 1
    return {"imported": imported, "skipped": skipped}
