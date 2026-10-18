import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount REAL NOT NULL,            -- positive = money in, negative = money out
    account TEXT NOT NULL,
    source_file TEXT NOT NULL,
    UNIQUE (date, description, amount, account)
);
CREATE TABLE IF NOT EXISTS classifications (
    txn_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('rule', 'llm', 'user')),
    rationale TEXT,
    business_purpose TEXT,
    business_use_pct REAL,
    recipient TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS decisions (
    merchant TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    business_use_pct REAL,
    business_purpose TEXT,
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS info_returns (
    id INTEGER PRIMARY KEY,
    form TEXT NOT NULL CHECK (form IN ('1099-NEC', '1099-K', '1099-MISC')),
    payer TEXT NOT NULL,
    payer_tin_last4 TEXT,
    amount REAL NOT NULL,
    source_file TEXT NOT NULL,
    UNIQUE (form, payer, amount)
);
CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY,
    line TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mileage (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    miles REAL NOT NULL CHECK (miles > 0),
    purpose TEXT NOT NULL
);
-- embeddings for the IRS chunks, computed in the browser
CREATE TABLE IF NOT EXISTS irs_embeddings (
    chunk_id TEXT NOT NULL,
    model TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    vector BLOB NOT NULL,            -- float32, normalized
    PRIMARY KEY (chunk_id, model)
);
-- each answer the user gave, next to what the model or a rule had suggested.
-- shown to the model as examples next time (no training, just the prompt)
CREATE TABLE IF NOT EXISTS feedback (
    txn_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    merchant TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    suggested TEXT,                  -- category before the user answered
    suggested_by TEXT,               -- rule | llm | NULL
    answer TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('confirmed', 'corrected', 'answered')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- statements imported from PDF/DOCX, with their own balances for checking
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    source_file TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('bank', 'card')),
    period_start TEXT,
    period_end TEXT,
    opening REAL,
    closing REAL,
    parsed_change REAL,
    difference REAL,                 -- parsed minus expected; NULL if balances weren't found
    rows INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- user's written explanation for a check they chose not to fix
CREATE TABLE IF NOT EXISTS check_acks (
    check_id TEXT PRIMARY KEY,
    note TEXT NOT NULL,
    acked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- fingerprint of the data at sign-off; a later change makes it stale
CREATE TABLE IF NOT EXISTS signoff (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fingerprint TEXT NOT NULL,
    statement TEXT NOT NULL,
    signed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("TAXPAL_DB", "taxpal.db"))


@contextmanager
def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset() -> None:
    with connect() as conn:
        for table in ("classifications", "transactions", "decisions", "info_returns",
                      "adjustments", "mileage", "profile", "check_acks", "signoff", "feedback", "statements"):
            conn.execute(f"DELETE FROM {table}")


def rows(sql: str, params=()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]
