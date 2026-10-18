# TaxPal progress

Last updated: Oct 13, 2026. Submission deadline: **Oct 18, 2026** (DEV Hacktoberfest Week 2, "Side Hustle").

Design and IRS rules are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Status at a glance

| Area | Status |
|---|---|
| Rules engine (Schedule C, SE, QBI, 1040-ES) | Done, tested |
| Local web API | Done, tested |
| Import, categorization bookkeeping, reconciliation, export | Done, tested |
| Checks and sign-off (human in the loop) | Done, tested (UI part not run in a browser yet) |
| PDF/DOCX statement import | Done, tested (preview UI and AI fallback not run in a browser) |
| One model (Hermes-3 3B) for sorting + agent, JSON tool calls | Written, not run yet |
| Learning from user answers | Server done and tested; browser part not run yet |
| IRS publication search (server side) | Done, tested |
| Browser UI | Written, page loads, not tested with a model |
| WebGPU AI (categorizer, agent, IRS embeddings) | Written, **not run yet** |
| Demo video, DEV post, submission | Not started |

71 tests pass (`uv run pytest`).

## Work done

### Plan
- Picked the idea, scoped it to US Schedule C filers for Tax Year 2026, federal only.
- Wrote `ARCHITECTURE.md`: goal, non-goals, IRS rules with sources, architecture, API, data model, guardrails.
- Checked the 2026 IRS numbers against irs.gov: mileage 72.5¢ / 76¢ split on Jul 1, SS wage base $184,500, 1099-NEC $2,000, 1099-K $20,000 / 200, QBI thresholds and the new $400 minimum, 2027 estimated tax dates.

### Rules and data
- `rules/ty2026.yaml`: every year-specific number, each with its IRS source.
- `rules/taxonomy.yaml`: 31 categories mapped to Schedule C lines, with the rule, the IRS source, keywords and the facts each one needs (business purpose, business-use %, gift recipient).
- `scripts/make_sample_data.py`: fake data for a made-up freelancer. 207 transactions, three 1099s, a mileage log, and the correct category for every transaction.

### Web API (FastAPI, localhost only, no AI)
- Import: bank/card CSVs (handles signed amounts or debit/credit columns, several date formats, skips duplicates and other years), 1099s (CSV/JSON, keeps only the last 4 of the TIN), mileage, sample data.
- Categorization bookkeeping:
  - keyword pass plus remembered answers (sorts 193 of the 207 sample transactions)
  - checks model output before saving it
  - user answers, with an option to apply them to every transaction from the same merchant
  - review queue saying what's missing for each item
- Rules engine:
  - 50% meals, $25 gift cap per person
  - business-use %
  - simplified home office (capped at profit)
  - mileage split on Jul 1
  - no fuel/repairs with the standard mileage rate
  - equipment, health insurance and retirement flagged instead of deducted
- Schedule SE (wage base, $400 filing threshold, Additional Medicare flag), Form 8995 estimate, 2027 Form 1040-ES plan.
- 1099 vs. deposit matching, including the 1099-K gross vs. net fee adjustment.
- Export: `summary.md` (worksheet, estimates, flags, citations, disclaimer) and `transactions.csv`.
- `/api/tools`: tool definitions for the agent, with which endpoint each one calls and whether the user must confirm it.

### IRS rule search
- `scripts/build_irs_corpus.py`: downloads 10 IRS docs (Schedule C/SE and 8995 instructions; Pubs 334, 463, 505, 560, 583, 587, 946) and splits them into 1,607 chunks, each linked to its section on irs.gov. Index pages, boilerplate and blank worksheets are dropped.
- Keyword search (BM25) on the server, with stemming and a small synonym list ("lunch" finds "meals").
- Stores embeddings computed in the browser and merges keyword and meaning-based results.
- Search box in the UI, agent tool `search_irs_guidance`, and the IRS rule shown on each review item.

### Browser (WebGPU via WebLLM)
- One model for everything: Hermes-3 Llama 3.2 3B (~2.3 GB GPU memory; f32 build for GPUs without shader-f16). Loaded in a Web Worker after a WebGPU check.
- Categorizer: batches of 8, output forced into the expected JSON format.
- Agent: tool loop over the API, `ask_user` and `finish` tools, history trimming, step limit, confirmation before anything that changes totals.
- Separate embedding engine for IRS search; indexing can resume where it stopped.
- UI: import, sort, review queue, agent chat, IRS guidance, profile, results, downloads. Works without WebGPU, just without the AI.

### Checks and human in the loop
- **Categorizer retry:** answers the API rejects (wrong category, wrong direction of money) or the model skips go back to the model with the reason, up to 3 tries, then to the review queue.
- **Agent can't just stop:** the first time the agent tries to finish in a turn, the harness runs the checks and sends any open blockers back to it. Merchant-wide decisions and explanations need the user's OK.
- **`checks.py`, before sign-off:**
  - blockers: unreviewed items, every AI-chosen category (needs a click even at high confidence), 1099 gaps, income below the 1099 total, internal math recomputed a second way
  - warnings: keyword matches over $500, grouped by merchant for a quick spot-check
- **Explain instead of fix:** for 1099 gaps the user can write why it's right instead of changing data. The note goes into the summary.
- **Sign-off:** only from the UI (no agent tool). It saves a fingerprint of all inputs; any later change puts the summary back to DRAFT.
- Step 9 in the UI. 8 new tests (shared fixtures moved to `tests/conftest.py`).

### Learning from your answers
- Every answer, confirmation or correction is saved next to what the AI or a keyword rule had picked (`feedback` table).
- Before each batch, the 6 most similar past answers go into the sorting prompt, corrections first. No training, just a few hundred extra prompt tokens, so it adds almost nothing to the work the GPU does.
- "Re-check AI answers" redoes the model's unconfirmed answers after you've corrected some. It never overwrites your answers or confident rule matches.
- The page shows how many answers the AI has learned from and how often you kept its answer.
- 7 new tests.

### PDF/DOCX statement import
- Upload a bank or card statement as PDF or DOCX. Text is extracted locally (pypdf for PDF, document XML for DOCX); scanned PDFs are reported, not guessed.
- The parser handles running-balance columns, `-` / `( )` / `CR` / trailing-minus signs, section headings, MM/DD dates across a Dec–Jan statement, month names, two-date card lines, and wrapped descriptions.
- **Balance check:** the transactions must add up to the statement's own balance change. A missed or doubled line, or a wrong sign, shows up as a difference. A separate sign check catches a bank statement read as a card, which the balance check can't.
- **Preview first:** nothing is saved until you confirm. You can drop rows or flip signs, and dated lines that weren't read are listed.
- **AI fallback:** if the layout defeats the parser, the model reads the text in chunks, and its rows go through the same check.
- **New blockers:** a statement that was imported unbalanced, and months of the year with no statement for that account.
- Sample statements: `scripts/make_sample_statements.py` writes a January card PDF and a February checking DOCX from the sample data, using a tiny PDF/DOCX writer (`scripts/docgen.py`). Both balance to the cent.
- 11 new tests. Two real bugs were found and fixed while writing them: "Payment Due Date" was read as a "Payments" section, and unsigned deposits on signed-column bank statements were guessed as withdrawals.

### Checks for easy-to-miss mistakes (`risks.py`)
Each of these blocks sign-off until it's fixed or explained in writing:
- **Flipped signs:** a card CSV imported with charges as deposits.
- **Transfers counted:** a card payment or transfer counted as income or an expense.
- **Duplicates:** the same purchase in two accounts.
- **Hidden income:** a deposit marked "transfer" with no matching transfer out, or a "refund" with no earlier purchase.
- **Home costs:** apartment rent or home utilities deducted as business costs, flagged as double counting when a home office is claimed (Pub 587).
- **Big items:** a single item over $2,500 deducted as supplies instead of depreciated (de minimis safe harbor).
- **Receipts:** lodging at any amount, and travel, meals or gifts of $75 or more (Pub 463).

A group check that's been explained reopens when a new transaction joins it. On the sample data, two of these fire for real: the $3,000 from an un-imported savings account, and receipts for the Delta flight and Marriott stay. The checks take 0.05 seconds on 6,000 transactions. 8 new tests, each setting up one mistake.

The sample generator now makes the card's "PAYMENT THANK YOU" credits match the checking account's autopay amounts, like real statements. Nothing else in the sample changed.

### One model
- Dropped the 8B agent model and the Gemma sorter. Hermes-3 Llama 3.2 3B (~2.3 GB) now does both jobs.
- WebLLM's built-in tool calling only works with 7-8B Hermes models, so the harness does its own: each step the model must output `{"tool", "args"}` JSON, and the grammar only allows a real tool name with that tool's own arguments. The API checks the arguments again.
- Tool list shortened to fit the 4k context (system prompt ~1,240 tokens). History is trimmed by size, not message count.
- The IRS embedding model is optional; keyword search works without it.

### Cleanup
- Simplified comments and docstrings, removed section-divider comments and em-dashes, plainer UI wording. Behavior unchanged, tests still pass.

## Work remaining

### Must do before Oct 18
- [ ] **Run the browser AI on a WebGPU machine** (Chrome or Edge, about 5 GB free GPU memory). Check:
  - [ ] Hermes-3 3B loads; agent steps come back as valid JSON tool calls (if the anyOf grammar fails, the harness falls back to a looser one; check which one runs)
  - [ ] Categorizer sorts the 14 leftover sample transactions
  - [ ] Agent loop: tool calls, `ask_user`, `record_decision`, confirmation prompt
  - [ ] IRS index builds and meaning-based search returns sensible hits
  - [ ] Categorizer retry loop and the agent's finish gate behave as intended
  - [ ] Correct a few answers, re-check, and see whether similar items change
  - [ ] Step 9: confirm, change, apply, explain, sign off
  - [ ] Statement preview: read both sample statements, flip a sign, drop a row, import anyway, AI fallback
  - [ ] Try 2-3 real statement PDFs (your own bank). Real layouts will find parser gaps the samples don't
  - [ ] Fix whatever breaks
- [ ] **Git:** first commit, add a LICENSE (MIT or Apache-2.0), push a public GitHub repo
- [ ] **Accuracy numbers:** score categorization against `data/sample/labels.csv` (keywords alone, with the model, with the model after a few corrections)
- [ ] **Hand-check** one full sample run against the Schedule C and SE instructions
- [ ] **Demo video** (under 3 min): model loading on GPU → sort → agent asks about a purchase → Stripe 1099-K fee fix → IRS citation → checks and sign-off → export
- [ ] **DEV post** (writing counts most in judging):
  - [ ] What it is, who would pay, the problem
  - [ ] Demo video and repo embed
  - [ ] How it's built: WebGPU/WebLLM, one 3B model, JSON tool calls, browser agent, local API, rules engine, IRS search
  - [ ] How it follows IRS rules (citations, no AI math) and how it checks itself (retry loop, finish gate, sign-off)
  - [ ] Why open source matters: privacy, cost, no install, swappable models
  - [ ] Limitations and "not tax advice" disclaimer
  - [ ] AI-assistance disclosure (required by DEV rules)
- [ ] **Publish and submit** with the challenge tag

### Decide
- [ ] Partner category: TabPFN (anomaly flags, extra work, not sure it runs in the browser), Render (conflicts with "data stays local"), or none

### Nice to have
- [ ] Import 1099s from PDFs (can reuse the statement text extraction)
- [ ] Score IRS search on a small question set (keyword vs. hybrid)
- [ ] Anomaly flags with TabPFN
- [ ] One real freelancer tries it (bonus "first user")
- [ ] Save the agent session with DevRelay

### Known limits (state in the post, don't fix now)
- No depreciation / Section 179, regular home office method (Form 8829), inventory, state returns, or full Form 1040 tax.
- Most IRS text is the 2025 revision until 2026 versions come out. The 2026 numbers come from `ty2026.yaml`.
- 2026 Schedule SE and 8995 instructions were drafts when the numbers were checked. Recheck before filing season.
- Keyword-only IRS search misses some questions, e.g. "safe harbor 110%".
