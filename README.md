# TaxPal

Local, privacy-first tax prep for US freelancers (Tax Year 2026, Schedule C).

- **AI runs in your browser on your GPU** via WebGPU ([WebLLM](https://github.com/mlc-ai/web-llm)) — one small model (Hermes-3 Llama 3.2 3B) categorizes transactions, learns from your corrections, and drives an agent that asks you about anything it isn't sure of.
- **IRS guidance search** over 1,600+ passages from IRS publications and form instructions, with deep links to irs.gov. Keyword search works out of the box; building the index embeds the passages on your GPU for meaning-based search.
- **A local web API** (FastAPI, `127.0.0.1` only) stores your data in SQLite and does every calculation with deterministic, unit-tested IRS rules — the model never does math.

> TaxPal is not tax advice and does not file returns. Review its worksheets with a qualified tax professional.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design, IRS rules and roadmap.

## Run

```bash
uv sync
uv run taxpal            # http://127.0.0.1:8000  (API docs at /docs)
```

Open the page in a WebGPU browser (recent Chrome or Edge; ~2.3 GB GPU memory), load the model, then **Load sample data** → **Sort transactions** → review → results.

Without WebGPU everything except the AI still works: keyword rules sort most transactions and you review the rest by hand.

## Develop

```bash
uv run pytest                              # rules engine + API tests
uv run python scripts/make_sample_data.py  # regenerate synthetic data
uv run python scripts/make_sample_statements.py  # sample PDF/DOCX statements in data/sample/statements/
uv run python scripts/build_irs_corpus.py  # re-download + re-chunk IRS publications
```

Results download as a PDF summary and an Excel workbook (`/api/export/summary.pdf`, `/api/export/workbook.xlsx`) for your tax professional.

Data lives in `taxpal.db` (override with `TAXPAL_DB`). **Delete all data** in the UI wipes it.
