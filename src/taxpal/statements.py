"""Bank and card statements as PDF or DOCX.

Flow: read the text, parse transaction lines into a preview, check them against the
statement's own opening/closing balance, and only import after the user confirms. If the
parsed lines don't add up to the balance change, something was missed or a sign is wrong,
and that's the strongest check we have that the import is complete.

No OCR: a scanned (image-only) PDF has no text and is reported as such.
"""

import io
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

from . import db
from .ingest import ImportFormatError, import_rows
from .rules import tax_year
from .wording import plural

MAX_BYTES = 20 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}

DATE = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2}|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?"
LINE_START = re.compile(rf"^\s*(?P<d1>{DATE})\s+(?:(?P<d2>{DATE})\s+)?(?P<rest>.+)$", re.I)
MONEY = re.compile(r"(?<![\w.,/])(?P<neg>-|\()?\s?\$?(?P<num>\d{1,3}(?:,\d{3})+|\d+)\.(?P<cents>\d{2})"
                   r"(?P<close>\))?(?P<suffix>\s?(?:CR|DR)\b|-(?!\d))?(?![\d.])", re.I)
FULL_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
PERIOD = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to|through|thru)\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
OPENING = re.compile(r"(?:beginning|opening|previous|starting|prior)\s+balance", re.I)
CLOSING = re.compile(r"(?:ending|closing|new)\s+balance", re.I)
# statement wording, not transaction text ("AUTOPAY PAYMENT CREDIT CARD" is on bank statements too)
CARD_HINTS = re.compile(r"minimum payment|credit limit|available credit|new balance|previous balance|payment due date|"
                        r"annual percentage rate|credit card statement", re.I)
BANK_HINTS = re.compile(r"checking|savings|beginning balance|ending balance|opening balance|closing balance|"
                        r"deposits and|withdrawals|routing|credit union|account summary", re.I)
SECTION_IN = re.compile(r"\b(payments?|credits?|deposits?|additions|refunds)\b", re.I)
SECTION_OUT = re.compile(r"\b(purchases|withdrawals?|subtractions|checks? paid|fees charged|debits?|charges|cash advances)\b", re.I)
SKIP_DESC = re.compile(r"^(total|subtotal)\b|\bbalance\b", re.I)
DEPOSIT_WORDS = re.compile(r"DEPOSIT|CREDIT|PAYOUT|TRANSFER FROM|REFUND|INTEREST PAID|ZELLE FROM|DIRECT DEP", re.I)


def extract_text(data: bytes, filename: str) -> str:
    if len(data) > MAX_BYTES:
        raise ImportFormatError("File is over 20 MB")
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_text(data)
    if name.endswith(".docx") or data[:2] == b"PK":
        return _docx_text(data)
    raise ImportFormatError("Upload a PDF or DOCX statement (or a CSV in the CSV box)")


def _pdf_text(data: bytes) -> str:
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ImportFormatError("This PDF is password-protected. Save an unlocked copy and try again.")
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text(extraction_mode="layout"))  # keeps columns apart
            except Exception:
                pages.append(page.extract_text())
    except ImportFormatError:
        raise
    except Exception as e:
        raise ImportFormatError(f"Couldn't read this PDF: {e}") from e
    return "\n".join(pages)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise ImportFormatError("Couldn't read this DOCX file") from e
    text = lambda el: "".join(t.text or "" for t in el.iter(f"{W}t"))
    lines = []
    for block in root.find(f"{W}body"):
        if block.tag == f"{W}p":
            lines.append(text(block))
        elif block.tag == f"{W}tbl":
            # table cells joined with wide gaps, like columns in a PDF
            for row in block.iter(f"{W}tr"):
                lines.append("    ".join(text(c).strip() for c in row.iter(f"{W}tc")))
    return "\n".join(lines)


def _money(m: re.Match) -> float:
    value = float(m["num"].replace(",", "") + "." + m["cents"])
    negative = bool(m["neg"]) or (m["suffix"] or "").strip().upper() in ("CR", "-")
    return -value if negative else value


def _full_date(s: str) -> date | None:
    m = FULL_DATE.fullmatch(s.strip())
    if not m:
        return None
    month, day, year = int(m[1]), int(m[2]), int(m[3])
    return date(year + 2000 if year < 100 else year, month, day)


def _line_date(s: str, period: tuple[date, date] | None, default_year: int) -> date:
    s = s.strip().upper().replace(",", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    if full := _full_date(s):
        return full
    if "/" in s:
        month, day = (int(x) for x in s.split("/"))
    else:
        parts = s.replace(".", "").split()
        month, day = MONTHS[parts[0][:3]], int(parts[1])
        if len(parts) > 2:
            return date(int(parts[2]), month, day)
    if period:
        # pick the year that puts the date inside the statement period (Dec/Jan statements)
        for year in (period[1].year, period[0].year):
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if period[0] - timedelta(days=10) <= d <= period[1] + timedelta(days=10):
                return d
    return date(default_year, month, day)


def _balance(text: str, label: re.Pattern) -> float | None:
    for line in text.splitlines():
        if label.search(line):
            after = line[label.search(line).end():]
            if m := MONEY.search(after):
                return _money(m)
    return None


def _detect_kind(text: str) -> str:
    header = "\n".join(l for l in text.splitlines() if not LINE_START.match(l.strip()))
    return "card" if len(CARD_HINTS.findall(header)) > len(BANK_HINTS.findall(header)) else "bank"


def _sign_warnings(kind: str, rows: list[dict]) -> list[str]:
    """Catch a wrong card/bank guess: the balance check can't, since flipping every sign
    flips the expected change too."""
    looks_in = [r for r in rows if re.search(r"PAYOUT|ACH CREDIT|DIRECT DEP|DEPOSIT|PAYMENT THANK YOU|REFUND", r["description"], re.I)]
    wrong = [r for r in looks_in if r["amount"] < 0]
    if looks_in and len(wrong) > len(looks_in) / 2:
        other = "bank account" if kind == "card" else "credit card"
        return [f"{plural(len(wrong), 'line')} that look like deposits came out as money out (for example, {wrong[0]['description']}). "
                f"If this is a {other} statement, switch the statement type and read it again."]
    return []


def parse_statement(text: str, kind: str | None = None) -> dict:
    """Preview of what's in the statement. Positive = money into this account, like CSV imports.
    For a card that means payments and refunds are positive, charges negative."""
    if len(text.strip()) < 20:
        return {"rows": [], "needs_ocr": True, "kind": kind or "bank", "unparsed": [], "check": None, "warnings": [],
                "period": None, "opening": None, "closing": None, "text": ""}
    detected = _detect_kind(text)
    kind = kind or detected
    period = None
    if m := PERIOD.search(text):
        start, end = _full_date(m[1]), _full_date(m[2])
        if start and end and start <= end:
            period = (start, end)
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", text)]
    default_year = period[1].year if period else (max(set(years), key=years.count) if years else tax_year()["tax_year"])
    opening, closing = _balance(text, OPENING), _balance(text, CLOSING)

    rows, unparsed = [], []
    section = None
    running = opening
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        m = LINE_START.match(line)
        tokens = list(MONEY.finditer(m["rest"])) if m else []
        if not m or not tokens:
            # section headers decide the sign of unsigned amounts below them
            # headings are short and have no numbers ("PAYMENTS AND OTHER CREDITS"); "Payment Due Date 01/25" isn't one
            is_heading = not re.search(r"\d", line) and len(line.split()) <= 6
            if len(line) < 70 and not MONEY.search(line) and not OPENING.search(line) and not CLOSING.search(line):
                if is_heading and SECTION_OUT.search(line):
                    section = "out"
                elif is_heading and SECTION_IN.search(line):
                    section = "in"
                elif rows and rows[-1]["line"] == n - 1 and len(line) < 40 and not re.search(r"page|continued", line, re.I):
                    rows[-1]["description"] += " " + line  # description wrapped onto the next line
            elif (MONEY.search(line) and re.search(DATE, line, re.I) and not (OPENING.search(line) or CLOSING.search(line))
                  and not PERIOD.search(line)):
                # has a date and an amount but didn't parse: likely a missed transaction
                unparsed.append({"line": n, "text": line[:200]})
            continue

        description = m["rest"][:tokens[0].start()].strip(" -$\t")
        if not description or SKIP_DESC.search(description):
            continue
        amount_tok = tokens[-2] if len(tokens) >= 2 else tokens[-1]
        balance = _money(tokens[-1]) if len(tokens) >= 2 else None
        value = abs(_money(amount_tok))
        explicit_negative = _money(amount_tok) < 0

        # sign: running balance beats everything, then explicit signs, then the section
        if balance is not None and running is not None and abs(abs(balance - running) - value) < 0.01:
            change = balance - running
            amount = change if kind == "bank" else -change
            sign_from = "balance"
        elif explicit_negative:
            amount = -value if kind == "bank" else value
            sign_from = "sign"
        elif section:
            amount = value if section == "in" else -value
            sign_from = "section"
        elif kind == "card":
            amount = -value
            sign_from = "card default"
        else:
            amount = value if DEPOSIT_WORDS.search(description) else -value
            sign_from = "guess"
        if balance is not None:
            running = balance

        try:
            when = _line_date(m["d1"], period, default_year)
        except (ValueError, KeyError):
            unparsed.append({"line": n, "text": line[:200]})
            continue
        rows.append({"line": n, "date": when.isoformat(), "description": re.sub(r"\s{2,}", " ", description)[:200],
                     "amount": round(amount, 2), "sign_from": sign_from})

    # a bank statement with a single signed Amount column: if some amounts carry a minus,
    # the unsigned ones are deposits, which beats guessing from the description
    if kind == "bank" and any(r["sign_from"] == "sign" for r in rows):
        for r in rows:
            if r["sign_from"] == "guess":
                r["amount"], r["sign_from"] = abs(r["amount"]), "signed column"

    return {"kind": kind, "detected_kind": detected, "warnings": _sign_warnings(kind, rows),
            "period": [period[0].isoformat(), period[1].isoformat()] if period else None,
            "opening": opening, "closing": closing, "rows": rows, "unparsed": unparsed[:50],
            "check": balance_check(kind, opening, closing, rows), "needs_ocr": False,
            "text": text[:60000]}


def balance_check(kind: str, opening: float | None, closing: float | None, rows: list[dict]) -> dict | None:
    """Do the transactions explain the change in balance? On a card the balance is what you
    owe, so money in (payments) lowers it."""
    if opening is None or closing is None:
        return None
    parsed = round(sum(r["amount"] for r in rows), 2)
    expected = round(closing - opening if kind == "bank" else opening - closing, 2)
    diff = round(parsed - expected, 2)
    return {"expected_change": expected, "parsed_change": parsed, "difference": diff, "ok": abs(diff) < 0.01}


def read_statement(data: bytes, filename: str, kind: str | None = None) -> dict:
    preview = parse_statement(extract_text(data, filename), kind)
    preview["source_file"] = filename
    return preview


def confirm_import(account: str, source_file: str, kind: str, rows: list[dict], period: list[str] | None,
                   opening: float | None, closing: float | None) -> dict:
    if kind not in ("bank", "card"):
        raise ValueError("kind must be 'bank' or 'card'")
    clean = []
    for r in rows:
        when = date.fromisoformat(str(r["date"]))
        desc = str(r["description"]).strip()
        if not desc:
            raise ValueError("Every transaction needs a description")
        clean.append({"date": when.isoformat(), "description": desc[:200], "amount": round(float(r["amount"]), 2)})
    check = balance_check(kind, opening, closing, clean)
    result = import_rows(clean, account, source_file)
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO statements (account, source_file, kind, period_start, period_end, opening, closing,
                                       parsed_change, difference, rows)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account, source_file, kind, period[0] if period else None, period[1] if period else None,
             opening, closing, check["parsed_change"] if check else None, check["difference"] if check else None, len(clean)))
    return {**result, "statement_id": cur.lastrowid, "check": check}


def coverage_gaps() -> list[dict]:
    """Months of the tax year with no statement, per account imported from statements."""
    year = tax_year()["tax_year"]
    out = []
    for account in [r["account"] for r in db.rows("SELECT DISTINCT account FROM statements ORDER BY account")]:
        covered = set()
        for s in db.rows("SELECT * FROM statements WHERE account = ?", (account,)):
            if s["period_start"] and s["period_end"]:
                d, end = date.fromisoformat(s["period_start"]), date.fromisoformat(s["period_end"])
                while d <= end:
                    if d.year == year:
                        covered.add(d.month)
                    d += timedelta(days=1)
            else:
                covered |= {int(r["date"][5:7]) for r in db.rows(
                    "SELECT date FROM transactions WHERE account = ? AND source_file = ?", (account, s["source_file"]))}
        missing = [m for m in range(1, 13) if m not in covered]
        if missing:
            out.append({"account": account, "missing_months": missing})
    return out
