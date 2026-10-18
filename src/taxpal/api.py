from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import categorize, checks, db, export, ingest, irs_search, reconcile, service, statements
from .rules import taxonomy, tax_year
from .tools import tool_specs

WEB_DIR = Path(__file__).parent / "web"
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"



@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="TaxPal API", version="0.1.0", lifespan=lifespan,
              description="Local API behind the TaxPal UI. Not tax advice.")


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})
@app.post("/api/import/transactions")
async def import_transactions(file: UploadFile = File(...), account: str = Form("checking"),
                              purchases_positive: bool = Form(False)):
    text = (await file.read()).decode("utf-8-sig")
    return ingest.import_transactions(text, account, file.filename or "upload.csv", purchases_positive)


@app.post("/api/import/statement")
async def read_statement(file: UploadFile = File(...), kind: Literal["auto", "bank", "card"] = Form("auto")):
    """Preview only. Nothing is saved until /api/import/statement/confirm."""
    data = await file.read()
    return statements.read_statement(data, file.filename or "statement", None if kind == "auto" else kind)


class StatementRow(BaseModel):
    date: date
    description: str = Field(min_length=1, max_length=200)
    amount: float


class StatementCheck(BaseModel):
    kind: Literal["bank", "card"]
    opening: float | None = None
    closing: float | None = None
    rows: list[StatementRow] = Field(max_length=5000)


@app.post("/api/import/statement/check")
def check_statement(s: StatementCheck):
    return statements.balance_check(s.kind, s.opening, s.closing, [r.model_dump() for r in s.rows])


class StatementImport(StatementCheck):
    account: str = Field(min_length=1, max_length=60)
    source_file: str = Field(min_length=1, max_length=200)
    period: list[date] | None = Field(default=None, min_length=2, max_length=2)


@app.post("/api/import/statement/confirm")
def confirm_statement(s: StatementImport):
    return statements.confirm_import(s.account, s.source_file, s.kind, [r.model_dump() for r in s.rows],
                                     [d.isoformat() for d in s.period] if s.period else None, s.opening, s.closing)


@app.post("/api/import/info-returns")
async def import_info_returns(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8-sig")
    return ingest.import_info_returns(text, file.filename or "upload")


@app.post("/api/import/mileage")
async def import_mileage(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8-sig")
    return ingest.import_mileage(text)


@app.post("/api/import/sample")
def import_sample():
    if not SAMPLE_DIR.exists():
        raise HTTPException(404, "Sample data not found. Run scripts/make_sample_data.py first.")
    read = lambda name: (SAMPLE_DIR / name).read_text()
    return {
        "checking": ingest.import_transactions(read("bank_checking.csv"), "checking", "bank_checking.csv"),
        "card": ingest.import_transactions(read("credit_card.csv"), "card", "credit_card.csv"),
        "info_returns": ingest.import_info_returns(read("info_returns.csv"), "info_returns.csv"),
        "mileage": ingest.import_mileage(read("mileage.csv")) if not db.rows("SELECT 1 FROM mileage LIMIT 1") else {"imported": 0},
    }
@app.get("/api/status")
def get_status():
    return service.status()


@app.get("/api/files")
def get_files():
    return service.imported_files()


@app.get("/api/transactions")
def list_transactions(status: Literal["all", "uncategorized", "needs_review", "model_guesses"] = "all", limit: int = 500):
    if status == "needs_review":
        return categorize.review_queue(limit)
    where = {"all": "", "uncategorized": "WHERE c.txn_id IS NULL", "model_guesses": "WHERE c.source = 'llm'"}[status]
    return db.rows(f"""SELECT t.id AS txn_id, t.date, t.description, t.merchant, t.amount, t.account,
                              c.category, c.confidence, c.source, c.needs_review
                       FROM transactions t LEFT JOIN classifications c ON c.txn_id = t.id {where}
                       ORDER BY t.date LIMIT ?""", (limit,))


@app.post("/api/auto-categorize")
def auto_categorize():
    return categorize.auto_categorize()


class ModelClassification(BaseModel):
    txn_id: int
    category: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


@app.post("/api/classifications")
def save_classifications(items: list[ModelClassification]):
    return categorize.save_model_classifications([i.model_dump() for i in items])


class Decision(BaseModel):
    txn_id: int
    category: str
    business_purpose: str | None = None
    business_use_pct: float | None = Field(default=None, ge=0, le=100)
    recipient: str | None = None
    apply_to_merchant: bool = False


@app.post("/api/decisions")
def record_decision(d: Decision):
    return categorize.record_decision(**d.model_dump(exclude={"category"}), category_key=d.category)
class ExampleQuery(BaseModel):
    descriptions: list[str] = Field(min_length=1, max_length=50)
    k: int = Field(6, ge=1, le=20)


@app.post("/api/examples")
def examples(q: ExampleQuery):
    return categorize.examples_for(q.descriptions, q.k)


@app.get("/api/feedback")
def feedback():
    return categorize.feedback_stats()


class Confirm(BaseModel):
    txn_ids: list[int] = Field(min_length=1, max_length=2000)


@app.post("/api/confirm")
def confirm(c: Confirm):
    return categorize.confirm_classifications(c.txn_ids)


@app.get("/api/checks")
def get_checks():
    return checks.run_checks()


class Ack(BaseModel):
    note: str = Field(max_length=500)


@app.post("/api/checks/{check_id}/acknowledge")
def acknowledge_check(check_id: str, a: Ack):
    return checks.acknowledge(check_id, a.note)


class SignOff(BaseModel):
    confirm: Literal[True]  # the UI checkbox; the agent has no tool for this


@app.get("/api/signoff")
def get_signoff():
    return checks.get_signoff()


@app.post("/api/signoff")
def sign_off(_: SignOff):
    return checks.sign_off()


@app.get("/api/profile")
def get_profile():
    return {"profile": service.get_profile(), "fields": service.PROFILE_FIELDS}


@app.put("/api/profile")
def update_profile(changes: dict):
    return service.update_profile(changes)


class Trip(BaseModel):
    date: date
    miles: float = Field(gt=0)
    purpose: str = Field(min_length=3)


@app.get("/api/mileage")
def list_mileage():
    return db.rows("SELECT * FROM mileage ORDER BY date")


@app.post("/api/mileage")
def log_mileage(trip: Trip):
    if trip.date.year != tax_year()["tax_year"]:
        raise ValueError(f"Trip date must be in {tax_year()['tax_year']}")
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO mileage (date, miles, purpose) VALUES (?, ?, ?)",
                           (trip.date.isoformat(), trip.miles, trip.purpose))
    return {"id": cur.lastrowid}


class Adjustment(BaseModel):
    line: str
    amount: float
    note: str = Field(min_length=3)


@app.post("/api/adjustments")
def add_adjustment(a: Adjustment):
    valid = {"1"} | {c["line"] for c in taxonomy().values() if c["line"]}
    if a.line not in valid:
        raise ValueError(f"line must be one of {sorted(valid)}")
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO adjustments (line, amount, note) VALUES (?, ?, ?)", (a.line, a.amount, a.note))
    return {"id": cur.lastrowid}


@app.get("/api/adjustments")
def list_adjustments():
    return db.rows("SELECT * FROM adjustments")
@app.get("/api/reconcile")
def get_reconcile():
    return reconcile.reconcile()


@app.get("/api/schedule-c")
def get_schedule_c():
    return service.schedule_c()


@app.get("/api/se-qbi")
def get_se_qbi():
    return service.se_and_qbi()


@app.get("/api/estimated-tax")
def get_estimated_tax():
    return service.estimated_tax()


@app.get("/api/irs-rules")
def irs_rules(category: str | None = None):
    tax = taxonomy()
    if category is None:
        return [{"category": k, "label": c["label"], "line": c["line"], "direction": c["direction"]} for k, c in tax.items()]
    if category not in tax:
        raise HTTPException(404, f"Unknown category “{category}”.")
    c = tax[category]
    return {"category": category, "label": c["label"], "schedule_c_line": c["line"], "line_name": c["line_name"],
            "rule": c["rule"], "irs_source": c["irs"], "needs": c.get("needs", [])}
@app.get("/api/irs/status")
def irs_status(model: str | None = None):
    return irs_search.status(model)


@app.get("/api/irs/documents")
def irs_documents():
    return irs_search.documents()


@app.get("/api/irs/chunks")
def irs_chunks(model: str, limit: int = Query(64, ge=1, le=512)):
    return irs_search.chunks_to_embed(model, limit)


class Embedding(BaseModel):
    id: str
    vector: list[float] = Field(min_length=8, max_length=4096)


class EmbeddingBatch(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    corpus_version: str
    items: list[Embedding] = Field(max_length=512)


@app.post("/api/irs/embeddings")
def irs_store_embeddings(batch: EmbeddingBatch):
    return irs_search.store_embeddings(batch.model, batch.corpus_version, [i.model_dump() for i in batch.items])


class IrsQuery(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(5, ge=1, le=20)
    vector: list[float] | None = Field(default=None, max_length=4096)
    model: str | None = None
    docs: list[str] | None = None


@app.post("/api/irs/search")
def irs_search_endpoint(q: IrsQuery):
    return irs_search.search(q.query, q.k, q.vector, q.model, q.docs)


@app.get("/api/taxonomy")
def get_taxonomy():
    return {k: {f: c.get(f) for f in ("label", "line", "line_name", "treatment", "direction", "rule", "irs", "needs")}
            for k, c in taxonomy().items()}


@app.get("/api/tools")
def get_tools():
    return tool_specs()
@app.get("/api/export/summary.md", response_class=PlainTextResponse)
def export_summary():
    return Response(export.summary_markdown(), media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="taxpal-summary-2026.md"'})


@app.get("/api/export/summary.pdf")
def export_summary_pdf():
    return Response(export.summary_pdf(), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="taxpal-summary-2026.pdf"'})


@app.get("/api/export/workbook.xlsx")
def export_workbook():
    return Response(export.workbook_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="taxpal-2026.xlsx"'})


@app.get("/api/export/transactions.csv")
def export_transactions():
    return Response(export.transactions_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="taxpal-transactions-2026.csv"'})


@app.post("/api/reset")
def reset():
    db.reset()
    return {"reset": True}


@app.middleware("http")
async def revalidate_ui(request: Request, call_next):
    # the UI changes with the code; make browsers check for a newer copy (cheap, StaticFiles sends ETags)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    import uvicorn

    # localhost only, this has people's bank data in it
    uvicorn.run("taxpal.api:app", host="127.0.0.1", port=8000)
