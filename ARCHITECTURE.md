# TaxPal — Local AI Tax-Prep Assistant for US Freelancers

> Hacktoberfest 2026 · DEV Open-Source AI Challenge, Week 2 ("Side Hustle")
> Submission deadline: **October 18, 2026**

---

## 1. Goal

Build a **privacy-first tax-prep assistant** for **US self-employed individuals** (freelancers, gig workers, single-member LLCs taxed as sole proprietors) that runs entirely on the user's machine:

- **All AI runs in the browser on the user's GPU via WebGPU** ([WebLLM](https://github.com/mlc-ai/web-llm), open source). No Ollama, no Python ML stack, no cloud AI API — open the page, the model downloads once and is cached.
- An **in-browser agent harness** drives one small open-weight model (**Hermes-3-Llama-3.2-3B**, Nous Research, ~2.3 GB VRAM) for both sorting and the agent. Tool calls are grammar-constrained JSON, so no large function-calling model is needed.
- A **local web API** (FastAPI on `127.0.0.1`) holds the data and runs every deterministic IRS calculation. The harness's tools are calls to this API.

**Target:** Tax Year **2026** (returns filed in the 2027 filing season), federal only.

The user drops their 2026 bank/card CSVs and information returns (Forms 1099-NEC, 1099-K, 1099-MISC) into a folder. TaxPal:

1. Ingests and normalizes all transactions and information returns.
2. Categorizes every transaction into a **Schedule C (Form 1040) expense line**, or marks it personal / non-deductible.
3. Asks the user **only** about uncertain items, and remembers the answers.
4. Reconciles reported income (1099s) against deposits so no income is missed.
5. Computes Schedule C totals, self-employment tax (Schedule SE) and a QBI estimate (Form 8995) with **deterministic code** (never the LLM).
6. Cites the IRS publication or form instruction behind each category.
7. Exports a **Schedule C worksheet + accountant summary**, plus a 1040-ES estimated-tax plan for next year.

### Non-goals (MVP)

- **No e-filing and no completed IRS forms.** TaxPal produces worksheets; the user or a tax professional files. TaxPal is not an IRS Authorized e-file Provider or a paid preparer.
- **No LLM arithmetic.** All numbers come from the rules engine.
- **Not tax advice.** Clear disclaimer in the UI and in every export.
- **Out of scope:** state returns; partnerships/S-corps/C-corps (Forms 1065, 1120-S, 1120); employees with payroll; inventory and cost of goods sold (Schedule C Part III); depreciation and Section 179 calculations (capital purchases are *flagged* for Form 4562, not computed); the regular (actual-expense) home-office method (Form 8829); full Form 1040 tax liability.

### Who pays & why

Freelancers who spend hours each spring sorting a year of statements into Schedule C lines, or pay an accountant to do it. TaxPal turns the pile into a categorized, cited, accountant-ready worksheet, and their financial data never leaves their laptop.

### Why open source matters here (for the post)

| Reason | How TaxPal uses it |
|---|---|
| **Privacy** | Bank data and taxpayer info never leave the machine — model runs on the user's GPU in the browser, API bound to localhost (in the spirit of IRS Pub 4557, *Safeguarding Taxpayer Data*) |
| **Cost** | No per-token API fees and no inference server — the user's own GPU does the work |
| **Zero install AI** | WebGPU means no Ollama/CUDA setup; any modern Chrome/Edge (or Safari/Firefox with WebGPU) runs it |
| **No lock-in / model swap** | Tools are a plain HTTP API (OpenAPI spec) → any WebLLM model, or any external harness, can drive them |
| **Fine-tuning** | A categorizer can later be fine-tuned on user-corrected labels |

---

## 2. IRS rules TaxPal implements

All year-specific values live in `rules/ty2026.yaml`, each with its IRS source. **Verify against final 2026 form instructions before release** — several 2026 instructions (e.g. Schedule SE) were still drafts as of Oct 2026.

### 2.1 Primary IRS references

| Topic | Source |
|---|---|
| Schedule C lines & what goes where | Instructions for Schedule C (Form 1040) |
| Business expense rules (general) | Pub 334, *Tax Guide for Small Business* |
| Travel, meals, gifts, car expenses | Pub 463, *Travel, Gift, and Car Expenses* |
| Home office | Pub 587, *Business Use of Your Home* |
| Depreciation / capital purchases (flag only) | Pub 946, *How To Depreciate Property*; Form 4562 |
| Self-employment tax | Schedule SE (Form 1040) & instructions |
| Qualified business income deduction | Form 8995 & instructions |
| Estimated tax | Form 1040-ES; Pub 505, *Tax Withholding and Estimated Tax* |
| Recordkeeping | Pub 583, *Starting a Business and Keeping Records* |
| Information returns | Instructions for Forms 1099-NEC, 1099-MISC, 1099-K |

### 2.2 Category taxonomy → Schedule C lines

| Schedule C line | Category | Key rules enforced |
|---|---|---|
| 1 | Gross receipts | Includes **all** business income whether or not a 1099 was received |
| 8 | Advertising | — |
| 9 | Car and truck expenses | Standard mileage **or** actual expenses, not both; commuting is not deductible; requires a mileage log (Pub 463) |
| 10 | Commissions and fees | Platform/payment processor fees |
| 11 | Contract labor | Payments to contractors (user may owe 1099-NEC if ≥ $2,000 to one payee in 2026) |
| 13 | Depreciation / Section 179 | **Flag only** — routed to accountant via Form 4562 |
| 15 | Insurance (other than health) | Self-employed health insurance is **not** here (see §2.4) |
| 16b | Interest (other) | Business loans/cards only |
| 17 | Legal and professional services | — |
| 18 | Office expense | — |
| 20b | Rent/lease — other business property | — |
| 21 | Repairs and maintenance | — |
| 22 | Supplies | — |
| 23 | Taxes and licenses | Federal income tax and SE tax are **not** deductible here |
| 24a | Travel | Away from tax home overnight (Pub 463) |
| 24b | Deductible meals | **50%** of business meal cost; entertainment is **0%** (not deductible) |
| 25 | Utilities | Business-use portion only (e.g. phone/internet %) |
| 27a | Other expenses (Part V) | Software subscriptions, education related to current business, bank fees, etc.; business gifts capped at **$25 per recipient per year** |
| 30 | Business use of home | **Simplified method only** in MVP: $5/sq ft, max 300 sq ft ($1,500); requires regular and exclusive use (Pub 587) |
| — | Personal / non-deductible | Personal spending, clothing suitable for everyday wear, fines and penalties, commuting, owner draws/transfers |

### 2.3 Tax Year 2026 values (`rules/ty2026.yaml`)

| Item | Value | Source |
|---|---|---|
| Business standard mileage rate | **72.5¢/mile** for miles before **Jul 1, 2026**; **76¢/mile** on or after Jul 1, 2026 | Notice 2026-10; Announcement 2026-11 |
| Social Security wage base | **$184,500** | IRS Topic 751; SSA |
| SE tax rate | 12.4% Social Security (up to wage base) + 2.9% Medicare (no cap) on **92.35%** of net SE earnings | Schedule SE |
| Schedule SE filing threshold | Net SE earnings **≥ $400** | Schedule SE instructions |
| Additional Medicare Tax | 0.9% above $200,000 ($250,000 MFJ) — **flag only** (Form 8959) | SSA / Form 8959 |
| Deduction for half of SE tax | 50% of SE tax, on Schedule 1 (not Schedule C) | Schedule SE |
| Form 1099-NEC / 1099-MISC threshold | **$2,000** per payee (payments after Dec 31, 2025) | One Big Beautiful Bill Act; 2026 Pub 1099 |
| Form 1099-K threshold | > **$20,000** and > **200** transactions (no threshold for payment-card transactions) | IRS 1099-K FAQs |
| Meals | 50% | Pub 463 |
| Business gifts | $25 per recipient per year | Pub 463 |
| Home office (simplified) | $5/sq ft, max 300 sq ft | Pub 587 |
| QBI deduction | 20% of QBI, limited to 20% of taxable income before QBI deduction (less net capital gain). MVP computes **Form 8995** only; flags Form 8995-A when taxable income exceeds **$201,750** (single/HOH), **$201,775** (MFS), **$403,500** (MFJ) | Rev. Proc. 2025-32; draft 2026 Form 8995 instructions |
| QBI minimum deduction (new for 2026) | **$400** if QBI from an active trade or business is **≥ $1,000** | Rev. Proc. 2025-32 (IRC §199A(i)) |
| 2027 estimated-tax due dates | Apr 15 2027 · Jun 15 2027 · Sep 15 2027 · **Jan 18 2028** (Jan 15 is a Saturday, Jan 17 is MLK Day) — confirm against the 2027 Form 1040-ES | Form 1040-ES |

### 2.4 Items handled outside Schedule C (flagged, shown in summary)

- **Self-employed health insurance** → Schedule 1 adjustment, not Schedule C line 15.
- **Half of SE tax** → Schedule 1 adjustment.
- **Retirement contributions** (SEP, SIMPLE, solo 401(k)) → Schedule 1; flagged for accountant.
- **Capital purchases** (equipment, computers) → Form 4562; flagged.
- **Estimated tax payments made in 2026** → captured from bank data (payments to "IRS USATAXPYMT"/EFTPS) and listed as credits paid.

### 2.5 Estimated tax plan (Form 1040-ES, for Tax Year 2027)

- Quarterly due dates: **April 15, June 15, September 15** of the tax year and **January 15** of the following year (shifted to the next business day when on a weekend/holiday).
- Safe harbor shown to user (Pub 505): pay the smaller of **90%** of current-year tax or **100%** of prior-year tax (**110%** if prior-year AGI > $150,000; $75,000 MFS).
- MVP uses the user's 2026 numbers as the basis and **clearly labels the result as an estimate** since full Form 1040 liability isn't computed.

### 2.6 Recordkeeping guidance surfaced to the user (Pub 583)

- Keep receipts/supporting records generally **3 years** from filing (longer in specific cases, e.g. 7 years for bad-debt/worthless-securities claims, and for as long as assets are held).
- Meals and travel need **amount, date, place and business purpose**; vehicles need a **mileage log**.
- TaxPal stores the business purpose the user gives during clarification alongside each transaction.

---


## 3. Architecture

All AI runs **in the browser on the user's GPU via WebGPU**. The Python side does **no AI at all**: it is a local web API that stores data and runs deterministic IRS calculations.

```
┌──────────────────────────── Browser (localhost page) ───────────────────────────┐
│                                                                                 │
│  UI: upload · review queue · agent chat · results/export                        │
│                                                                                 │
│  ┌───────────────────────── Web Worker ──────────────────────────┐              │
│  │  WebLLM engine (WebGPU)                                        │              │
│  │   • Hermes-3-Llama-3.2-3B-q4f16_1  — sorting + agent, tool    │              │
│  │     calls as constrained JSON; learns from user answers       │              │
│  │   • snowflake-arctic-embed-s        — IRS rule search          │              │
│  └───────────────────────────▲────────────────────────────────────┘              │
│                              │                                                  │
│  ┌───────────────────────────┴────────────────────────────────────┐             │
│  │  Agent harness (harness.js)                                     │             │
│  │   • loads tool specs from GET /api/tools                        │             │
│  │   • loop: model → tool_calls → fetch(/api/…) → tool results     │             │
│  │   • step limit, JSON validation, ask-the-user handoff           │             │
│  │  Categorizer (categorize.js)                                    │             │
│  │   • batches uncategorized txns → JSON-schema output             │             │
│  │   • POST /api/classifications                                   │             │
│  └───────────────────────────┬────────────────────────────────────┘             │
└──────────────────────────────┼──────────────────────────────────────────────────┘
                               │ HTTP (JSON) — 127.0.0.1 only
┌──────────────────────────────▼──────────────────────────────────────────────────┐
│  TaxPal web API  (Python · FastAPI · OpenAPI docs at /docs)                      │
│   import · transactions · classifications · decisions · profile · mileage       │
│   reconcile · schedule-c · se-qbi · estimated-tax · irs-rules · export · tools  │
│                                                                                 │
│   Rules engine (pure Python, unit-tested)  ←  rules/ty2026.yaml, taxonomy.yaml   │
│   SQLite (taxpal.db)                                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Where | Responsibility | Tech |
|---|---|---|---|
| **WebLLM engine** | Browser, Web Worker | Runs the open-weight model on the GPU via WebGPU; model cached in browser storage after first download | `@mlc-ai/web-llm@0.2.85` |
| **Model** | Browser | One model for sorting (JSON-schema output, learns from the user's answers in context) and the agent (one tool call per step as JSON, grammar ties each tool to its own arguments) | Hermes-3-Llama-3.2-3B q4f16 (~2.3 GB VRAM, 4k context); q4f32 build (~3 GB) for GPUs without shader-f16. WebLLM's built-in `tools` option only supports 7-8B Hermes models, so the harness doesn't use it |
| **Agent harness** | Browser | Plans the workflow, calls API tools, hands uncertain items to the user, step limits | `harness.js` (ours, ~200 lines); one JSON tool call per step via WebLLM's JSON-schema output |
| **Categorizer** | Browser | Batch-classifies transactions into Schedule C categories with confidence + rationale | WebLLM JSON mode with schema |
| **Web API** | Local server | Data storage, imports, deterministic IRS math, reconciliation, export, tool specs | Python 3.12, FastAPI, Uvicorn |
| **Rules engine** | Local server | Schedule C, SE tax, QBI, 1040-ES plan — every number | Pure Python + `ty2026.yaml` |
| **Storage** | Local server | Transactions, classifications, decisions, info returns, mileage, profile | SQLite |
| **IRS rule search** | API + browser | Taxonomy rules with citations, plus hybrid search over 1,607 IRS publication passages: BM25 on the server, embeddings computed in the browser | `snowflake-arctic-embed-s` via WebLLM (WebGPU) |
| **Anomaly detection** (optional) | Browser | Flag unusual/missed items | TabPFN (partner category) — TBD whether it can run client-side |

### Web API (v1)

| Method & path | Purpose | Used as agent tool |
|---|---|---|
| `POST /api/import/transactions` | Upload a bank/card CSV | — (UI) |
| `POST /api/import/info-returns` | Upload 1099s (CSV/JSON; PDF in Phase 3) | — (UI) |
| `POST /api/import/mileage` · `/api/import/sample` | Mileage CSV · load synthetic demo data | — (UI) |
| `POST /api/import/statement` | Read a PDF/DOCX statement into a preview (nothing saved) with a balance check | — (UI) |
| `POST /api/import/statement/check` · `/confirm` | Re-check edited rows against the balances · import after the user confirms | — (UI) |
| `GET/POST /api/adjustments` | e.g. 1099-K gross-up (gross on line 1, fees on line 10) | `apply_adjustment` (needs user OK) |
| `GET /api/taxonomy` · `POST /api/reset` | Categories for the UI/categorizer · delete all data | — |
| `GET /api/status` | Counts: imported, categorized, needs review, unmatched income | `get_status` |
| `GET /api/transactions?status=` | List transactions (`uncategorized`, `needs_review`, `all`) | `list_needs_review` |
| `POST /api/auto-categorize` | Deterministic pass: saved user decisions + keyword rules | — (pipeline) |
| `POST /api/classifications` | Save model classifications (low confidence → needs review) | — (categorizer) |
| `POST /api/decisions` | Save a user answer (+ business purpose, business-use %, apply to merchant) | `record_decision` (needs user OK when applied to a merchant) |
| `POST /api/examples` | The user's past answers most like a batch of transactions, corrections first | — (categorizer prompt) |
| `GET /api/feedback` | How many answers the AI has learned from; how often the user kept the model's answer | — (UI) |
| `POST /api/confirm` | User confirms categories chosen by the model or a keyword rule | — (UI only) |
| `GET /api/checks` | Pre-sign-off checks: blockers and warnings | `run_checks` |
| `POST /api/checks/{id}/acknowledge` | User's written explanation for a check they won't fix | `explain_check` (needs user OK) |
| `GET/POST /api/signoff` | Sign-off status · sign off (only with no open blockers) | — (UI only, never a tool) |
| `GET/PUT /api/profile` | Filing status, W-2 wages, home office sq ft, vehicle method, prior-year tax/AGI | `update_profile` |
| `GET/POST /api/mileage` | Mileage log | `log_mileage` |
| `GET /api/reconcile` | 1099s vs. deposits; unexplained deposits | `reconcile_income` |
| `GET /api/schedule-c` | Schedule C line totals + exclusions | `compute_schedule_c` |
| `GET /api/se-qbi` | Schedule SE + Form 8995 estimate | `compute_se_and_qbi` |
| `GET /api/estimated-tax` | 2027 Form 1040-ES plan | `plan_estimated_tax` |
| `GET /api/irs-rules?category=` | Rule text + IRS citation for a category | `lookup_irs_rule` |
| `POST /api/irs/search` | Hybrid IRS passage search with citations + irs.gov deep links | `search_irs_guidance` |
| `GET /api/irs/status` · `GET /api/irs/chunks` · `POST /api/irs/embeddings` | Index status; passages to embed; store browser-computed vectors | — (indexer) |
| `GET /api/export/summary.md` · `/transactions.csv` | Accountant package | — (UI) |
| `GET /api/tools` | OpenAI-format tool specs for the harness | — |
| `GET /docs` | OpenAPI docs | — |

### Agent workflow

```
1. LOAD       user uploads CSVs + 1099s in the UI → /api/import/*
2. SORT       /api/auto-categorize (saved decisions + keyword rules)
              → browser categorizer (WebGPU) for the rest → /api/classifications
3. CLARIFY    harness: list_needs_review → asks the user in chat → record_decision
              (business purpose, business-use %, gift recipient, apply to merchant)
4. RECONCILE  harness: reconcile_income → asks about unexplained deposits
5. PROFILE    harness asks for missing facts: filing status, home office sq ft,
              vehicle miles, prior-year tax/AGI → update_profile / log_mileage
6. JUSTIFY    search_irs_guidance / lookup_irs_rule → quoted IRS passage + link
7. COMPUTE    compute_schedule_c → compute_se_and_qbi → plan_estimated_tax
              (all math server-side, deterministic; the model only explains)
8. VERIFY     run_checks; the harness also runs it when the model tries to finish
              and sends open blockers back to the model once per turn
9. SIGN OFF   the user confirms model guesses, fixes or explains each blocker,
              then signs off in the UI (no agent tool can do this)
10. REPORT    UI export: summary.md + transactions.csv, marked DRAFT unless the
              sign-off still matches the data
```

### Statement import (PDF / DOCX)

1. **Read.** The server extracts text locally: pypdf in layout mode for PDFs, the document XML for DOCX (table rows become columns). Scanned image-only PDFs are reported, not guessed (no OCR).
2. **Parse.** Lines that start with a date and have an amount become transactions. The sign of each amount, in order of trust:
   1. the running-balance column
   2. an explicit sign (`-`, `( )`, `CR`)
   3. the section heading ("Deposits and Additions", "Purchases")
   4. a signed-amount column convention
   5. a guess, marked as such

   Statement type (bank or card) comes from the statement's own wording, not transaction text. MM/DD dates take their year from the statement period, so December lines on a Dec–Jan statement get the right year. Wrapped descriptions are joined.
3. **Check.** The parsed transactions must equal the balance change: closing − opening for a bank, opening − closing for a card. A sign sanity check catches a wrong bank/card choice, which the balance check alone can't, because every sign flips together.
4. **Preview and fix.** The user sees every row, can drop rows or flip a sign, and sees any dated lines with an amount that weren't read. If the parser fails, the model can read the text instead (in chunks, JSON-schema output), and its rows go through the same check.
5. **Import.** Only after the user confirms. An unbalanced statement can be imported only by ticking "import anyway"; it's then a sign-off blocker. Each statement's period is recorded, and missing months of the year are a blocker too.

### Learning from the user's answers (no training)

Each time the user answers, confirms or corrects a transaction, `feedback` stores the transaction, what the model or rule had suggested and the user's answer. Before each sorting batch the categorizer asks `POST /api/examples` for the ~6 most similar past answers (word overlap on the description, corrections ranked first, plus the 2 latest corrections) and puts them in the prompt. This is in-context learning: no fine-tuning, no gradients, no extra GPU work beyond a few hundred prompt tokens.

After the user corrects some answers, "Re-check AI answers" re-runs only the model's unconfirmed answers with the new examples. The model may replace its own earlier guesses, never the user's answers or confident rule matches. `GET /api/feedback` reports how often the user kept the model's answer, which is a running accuracy number for the post.

### Verification loops and human in the loop

Three loops, from fast to final:

1. **Model ↔ validator (categorizer).** The API checks every model answer (real category, right direction of money). Rejected or skipped items go back to the model with the reason, up to 3 tries in all, then to the review queue.
2. **Agent ↔ checks (harness).** The first `finish` in a turn triggers `GET /api/checks`. If blockers are open, the model gets them back and has to ask the user about them, or say clearly what's left.
3. **User ↔ checks (sign-off).** `checks.py` runs these before sign-off:

| Check | Severity | How it clears |
|---|---|---|
| Uncategorized transactions / review queue | blocker | User answers in the review queue |
| Category chosen by the model | blocker | User clicks "Looks right" or corrects it, even at high confidence |
| 1099-K gross vs. net gap with no adjustment | blocker | Apply the adjustment, or write an explanation |
| 1099 more than the deposits found | blocker | Fix the data, or write an explanation |
| Gross income below the 1099 total | blocker | Fix the data, or write an explanation |
| Internal math (lines add up, line 1 and meals recomputed separately) | blocker | Can't be explained away; it's a bug |
| Card CSV imported with charges as deposits (most rows money in, or 3+ known expense merchants as money in) | blocker | Re-import with "charges positive", or explain |
| Card payment / transfer counted as income or an expense (double counting) | blocker | Mark it as a transfer, or explain |
| Same purchase in two accounts (same amount, ≤3 days apart, shared merchant word) | blocker | Mark one as a transfer, or explain |
| Deposit of $100+ marked transfer with no matching outflow in another imported account, or a refund with no earlier purchase from that merchant | blocker | Import the other account, recategorize, or explain |
| Home rent/utilities deducted as business rent/utilities (worse with a home office: counted twice, Pub 587) | blocker | Recategorize, or explain |
| Imported statement doesn't add up to its balance change | blocker | Fix the rows, or explain |
| Months of the year with no statement for an account imported from statements | blocker | Upload them, or explain |
| Single item over $2,500 expensed as supplies/office/software/repairs (de minimis safe harbor, Reg. 1.263(a)-1(f)) | blocker | Mark as equipment, or explain |
| Lodging (any amount) or travel/meals/gifts of $75+ (Pub 463 ch. 5; Reg. 1.274-5(c)(2)(iii)) | blocker | Say where the receipts are |
| Keyword matches of $500+ (grouped by merchant) | warning | Spot-check, optional |
| Deposits above a 1099, adjustment applied twice | warning | Optional |

The risk checks live in `risks.py`. Group checks (receipts, home costs, big items) get an ID made from the exact transactions in them, so a new matching transaction reopens a check you already explained.

Sign-off stores a fingerprint of all inputs (transactions, categories, 1099s, adjustments, mileage, profile, explanations). Any later change makes the summary DRAFT again. The summary lists open checks and the user's explanations, so a tax pro sees them.

### Data model (SQLite)

```
transactions(id, date, description, merchant, amount, account, source_file)
classifications(txn_id, category, confidence, source[rule|llm|user], rationale,
                business_purpose, business_use_pct, recipient, needs_review)
info_returns(id, form[1099-NEC|1099-K|1099-MISC], payer, payer_tin_last4, amount, source_file)
mileage(id, date, miles, purpose)
decisions(merchant, category, business_use_pct, decided_at)          -- user memory
adjustments(id, line, amount, note)
statements(id, account, source_file, kind[bank|card], period_start, period_end,
           opening, closing, parsed_change, difference, rows)          -- for balance + coverage checks
feedback(txn_id, merchant, description, amount, suggested, suggested_by, answer,
         kind[confirmed|corrected|answered])                          -- examples for the model
check_acks(check_id, note, acked_at)                                  -- user's explanations
signoff(fingerprint, statement, signed_at)                            -- one row
profile(key, value)
irs_embeddings(chunk_id, model, corpus_version, vector)               -- browser-computed, not personal
```

### Trust, safety & compliance guardrails

- Model outputs are **schema-constrained JSON** (WebLLM grammar); the API re-validates every category; invalid → "needs review". The model never produces a dollar total.
- Classifications below a confidence threshold (0.8) always go to the user. Above it, model answers still need a user click before sign-off.
- Rejected model answers are retried with the reason, then left for the user.
- The agent can't declare itself done (checks gate) and can't sign off (no tool for it).
- Every number traces to transactions + a rule in `ty2026.yaml` + an IRS citation.
- Income is reported in full: unexplained deposits must be resolved, never dropped.
- Report includes **"Flags for your tax professional"**.
- API binds to **127.0.0.1 only**; after the one-time model download, no network calls. SSNs/TINs never stored (last 4 only). One-click data reset.
- Disclaimer: *"TaxPal is not tax advice and does not file returns. Review with a qualified tax professional."*
- WebGPU unsupported → the UI says so and still allows manual review (deterministic rules + user decisions work without AI).

---

## 4. Open decisions

- [x] **AI runtime** — WebGPU in the browser via WebLLM
- [x] **Harness** — custom in-browser harness; tool calls as JSON-schema output (works with any WebLLM model). External harnesses (Hermes Agent / OpenClaw) can still drive the same HTTP API later.
- [x] **Model** — one model, Hermes-3-Llama-3.2-3B q4f16 (~2.3 GB), for sorting and the agent. The IRS embedding model is optional; keyword search works without it
- [ ] **Partner category** — TabPFN and/or Render (hosting only the static page + API would break the local-data story; pick one narrative)

---

## 5. Work to be done

### Phase 0 — Setup
- [x] Python project (uv), FastAPI, pytest; git repo
- [ ] License file
- [x] Static frontend served by the API; WebGPU capability check
- [x] Project skeleton (section 6)

### Phase 1 — Rules & web API core
- [x] `ty2026.yaml` (§2.3) and `taxonomy.yaml` (§2.2) with IRS sources
- [x] Synthetic sample data: bank CSV, card CSV, 1099s, mileage — never real data
- [x] SQLite layer; CSV import with format detection
- [x] Deterministic auto-categorize (keyword rules + saved decisions)
- [x] Rules engine: Schedule C (50% meals, $25 gifts, business-use %, simplified home office with income cap, split mileage rate, standard-mileage exclusions)
- [x] Schedule SE, Form 8995 estimate (incl. $400 minimum), 1040-ES plan
- [x] Income reconciliation
- [x] All endpoints in §3 + `/api/tools`
- [x] Unit + API tests

### Phase 2 — WebGPU AI in the browser
- [x] Web Worker running WebLLM; model picker; download progress
- [x] Categorizer: batched JSON-schema classification → `/api/classifications`
- [x] Agent harness: tool loop over `/api/tools`, step limit, error handling
- [x] Chat UI with ask-the-user flow
- [ ] **Test the browser AI path on a real WebGPU machine** (not yet run: categorizer, harness, model loading)
- [x] Review queue UI (manual path when WebGPU is unavailable)

### Phase 3 — Documents & IRS search
- [x] Bank/card statement import from PDF (pypdf, local) and DOCX (zip + XML): parser, preview, balance check, AI fallback
- [ ] 1099 PDF import
- [x] IRS corpus: 10 IRS docs (Schedule C/SE + 8995 instructions; Pubs 334, 463, 505, 560, 583, 587, 946) → 1,607 cited chunks with section deep links (`scripts/build_irs_corpus.py`, noise filtered)
- [x] Hybrid search API: BM25 keyword (always on) + cosine over browser-computed vectors, reciprocal-rank fusion (`/api/irs/*`)
- [x] Browser indexing on WebGPU with `snowflake-arctic-embed-s` (separate ~1 GB engine; resumable, vectors stored via API)
- [x] `search_irs_guidance` agent tool (harness adds the query embedding) + IRS guidance search UI + rule shown on review items
- [x] Learning from user answers: feedback table, similar-example lookup, examples in the sorting prompt, re-check of the model's guesses
- [x] Verification loops: categorizer retry with rejection reasons, agent finish gate, pre-sign-off checks, user confirmation of model guesses, explanations, sign-off fingerprint
- [ ] Measure retrieval quality on a small labeled question set (keyword vs. hybrid)
- [ ] Optional: anomaly flags (TabPFN)

### Phase 4 — Demo
- [ ] Measure categorization accuracy on labeled sample data
- [ ] Hand-check one full sample against Schedule C / SE instructions
- [ ] Record demo: model loading on WebGPU, agent asking a question, 1099 reconciliation, export
- [ ] Optional: first real user; save agent session with DevRelay

### Phase 5 — DEV post & submission (before Oct 18)
Writing quality is weighted most heavily.
- [ ] What it is, who pays, the problem
- [ ] Demo video + repo
- [ ] How it's built: WebGPU/WebLLM, one 3B model, JSON tool calls, browser harness, local API, rules engine
- [ ] How it follows IRS rules (citations, deterministic math)
- [ ] Why open source matters (privacy, cost, zero-install AI, model swap)
- [ ] Limitations, disclaimer, what's next
- [ ] Publish with the challenge tag and submit

---

## 6. Repo layout

```
taxpal/
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
├── src/taxpal/
│   ├── api.py             # FastAPI app: REST endpoints + static frontend
│   ├── service.py         # glue: storage → rules engine; profile
│   ├── db.py              # SQLite
│   ├── ingest.py          # CSV / 1099 import
│   ├── categorize.py      # deterministic rules + validation of model output
│   ├── engine.py          # Schedule C, SE, QBI, 1040-ES (pure functions)
│   ├── reconcile.py       # 1099s vs. deposits
│   ├── checks.py          # pre-sign-off checks, explanations, sign-off fingerprint
│   ├── statements.py      # PDF/DOCX statements: text, parsing, balance check, coverage
│   ├── risks.py           # checks for easy-to-miss mistakes (double counts, hidden income, receipts...)
│   ├── irs_search.py      # BM25 + vector store + fusion over the IRS corpus
│   ├── export.py          # summary.md / transactions.csv
│   ├── tools.py           # agent tool specs (OpenAI format)
│   ├── rules/
│   │   ├── ty2026.yaml
│   │   ├── taxonomy.yaml
│   │   └── irs_corpus.jsonl   # chunked IRS publications (public domain)
│   └── web/               # browser app (no build step)
│       ├── index.html
│       ├── style.css
│       ├── app.js         # UI
│       ├── api.js         # web API client
│       ├── llm.js         # WebGPU detection + engine creation
│       ├── llm-worker.js  # WebLLM engine in a Web Worker (WebGPU)
│       ├── categorizer.js
│       ├── irs-search.js  # WebGPU embeddings + IRS search client
│       └── harness.js     # agent loop
├── data/sample/           # synthetic data only
├── scripts/make_sample_data.py
├── scripts/make_sample_statements.py   # + docgen.py (tiny PDF/DOCX writer)
├── scripts/build_irs_corpus.py
└── tests/
```

---

## 7. Success criteria

- `uv run taxpal` → open `http://127.0.0.1:8000` → load model on WebGPU → import `data/sample/` → Schedule C worksheet, SE/QBI estimates, 1040-ES plan.
- Every 1099 amount reconciled; no deposit left unexplained.
- ≥ 90% categorization accuracy on labeled sample data before clarification; 100% reviewed after.
- Rules engine passes unit tests built from IRS rules.
- Every total traces to transactions, a `ty2026.yaml` rule and an IRS citation.
- No AI runs outside the browser; no network calls after the model is cached.
- Demo video < 3 minutes; post published before **Oct 18, 2026**.
