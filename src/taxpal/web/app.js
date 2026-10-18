import { api } from "./api.js";
import { MODELS, detectWebGPU, loadEngine } from "./llm.js";
import { categorizeAll } from "./categorizer.js";
import { Harness } from "./harness.js";
import { IrsSearch } from "./irs-search.js";
import { setupStatementImport } from "./statement.js";

const $ = (id) => document.getElementById(id);
// "1 transaction", "3 transactions"
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const money = (x) => (x === null || x === undefined ? "n/a" : x.toLocaleString("en-US", { style: "currency", currency: "USD" }));

// no innerHTML anywhere, descriptions come from bank CSVs
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c instanceof Node ? c : String(c));
  return node;
}

function table(headers, rows, numericCols = []) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: numericCols.includes(i) ? "num" : "" }, h)))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, i) => el("td", { class: numericCols.includes(i) ? "num" : "" }, c)))))));
}

let engine = null; // the one model: sorting + agent
let harness = null;
let taxonomy = {};
const irs = new IrsSearch();

function gpuPanel(kind, title, detail) {
  $("gpu-status").className = `status-panel ${kind}`;
  $("gpu-status").replaceChildren(el("b", {}, title), el("span", {}, detail));
}

async function setupEngine() {
  const gpu = await detectWebGPU();
  const select = $("model-select");
  for (const m of MODELS) {
    if (gpu.ok && m.f16 && !gpu.f16) continue;
    select.append(el("option", { value: m.id }, m.label));
  }
  if (!gpu.ok) {
    gpuPanel("warn", "WebGPU isn't available", `${gpu.reason} You can still sort and review transactions by hand.`);
    return;
  }
  gpuPanel("ok", "WebGPU is ready", "The model downloads once (about 2 GB). After that, your browser keeps a copy.");
  $("irs-build").disabled = false;
  $("load-model").disabled = false;
  $("load-model").onclick = async () => {
    const id = select.value;
    $("load-model").disabled = select.disabled = true;
    $("model-progress").hidden = false;
    try {
      engine = await loadEngine(id, (p) => {
        $("progress-bar").style.width = `${Math.round(p.progress * 100)}%`;
        $("progress-text").textContent = p.text;
      });
      $("progress-text").textContent = "";
      $("model-progress").hidden = true;
      gpuPanel("ok", "The AI is ready", "The model is loaded on your GPU. You can sort transactions and chat.");
      $("load-model").textContent = "Loaded";
      await startAgent();
      await refreshLearning();
      if (!$("page-home").hidden) await renderHome();
    } catch (err) {
      $("progress-text").textContent = `Couldn't load the model: ${err.message}`;
      $("load-model").disabled = select.disabled = false;
    }
  };
}

async function refreshLearning() {
  const f = await api.get("/api/feedback");
  const acc = f.model_accuracy === null ? "" : ` You kept ${Math.round(f.model_accuracy * 100)}% of the AI's answers that you reviewed.`;
  $("learn-status").textContent = f.total
    ? `The AI learns from ${plural(f.total, "answer")} you've given: ${f.corrected} corrected, ${f.confirmed} confirmed and ${f.answered} new.${acc}`
    : "The AI uses your answers and corrections as examples the next time it sorts.";
  $("recheck-btn").hidden = !(engine && f.corrected + f.answered > 0);
}

async function refreshIrsStatus() {
  const s = await irs.status();
  const n = (x) => x.toLocaleString("en-US");
  const all = s.embedded === s.chunks;
  $("irs-stats").replaceWith(Object.assign(stats([["IRS documents", n(s.documents.length)], ["Passages", n(s.chunks)],
    ["Indexed for meaning", all ? "All" : n(s.embedded), all]]), { id: "irs-stats" }));
  if (all) {
    $("irs-build").hidden = true;
    $("irs-status").textContent = "Meaning-based search is on.";
  }
  return s;
}

async function buildIrsIndex() {
  const btn = $("irs-build");
  btn.disabled = true;
  $("irs-progress").hidden = false;
  try {
    if (!irs.ready) {
      btn.textContent = "Loading the search model…";
      await irs.load((p) => { $("irs-progress-bar").style.width = `${Math.round(p.progress * 100)}%`; });
    }
    btn.textContent = "Indexing…";
    await irs.buildIndex((done, total) => {
      $("irs-progress-bar").style.width = `${Math.round((done / total) * 100)}%`;
      $("irs-status").textContent = `Indexing IRS passages on your GPU: ${done} of ${total}`;
    });
    btn.textContent = "Index ready";
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Build index";
    $("irs-status").textContent = `Indexing failed: ${err.message}`;
    return;
  }
  await refreshIrsStatus();
}

async function searchIrs(e) {
  e.preventDefault();
  const q = $("irs-query").value.trim();
  const box = $("irs-results");
  if (q.length < 2) return;
  box.replaceChildren(el("p", { class: "muted" }, "Searching…"));
  try {
    const r = await irs.search(q, 5);
    box.replaceChildren(
      el("p", { class: "muted small" }, r.mode === "hybrid" ? "Results from keyword and meaning-based search." : "Results from keyword search. Build the IRS index on the AI Engine page to add meaning-based search."),
      ...r.hits.map((h) => el("div", { class: "hit" },
        el("a", { href: h.url, target: "_blank", rel: "noopener noreferrer" }, h.citation),
        el("div", { class: "meta" }, `${h.revision ? `${h.revision} revision · ` : ""}matched by ${h.match}`),
        el("p", {}, h.text))),
      el("p", { class: "muted small" }, r.note));
    if (!r.hits.length) box.prepend(el("p", {}, "No matching IRS passages. Try different words."));
  } catch (err) {
    box.replaceChildren(el("p", {}, err.message));
  }
}

async function refreshStatus() {
  const s = await api.get("/api/status");
  $("status-line").textContent = s.transactions
    ? `So far: ${plural(s.transactions, "transaction")} (${s.needs_review} to review), ${plural(s.info_returns, "1099")} and ${plural(s.mileage_trips, "trip")}.`
    : "Nothing imported yet.";
}

// file pickers styled as drop boxes: show the chosen file's name, highlight while dragging
function setupImportForm() {
  for (const drop of document.querySelectorAll(".drop")) {
    const input = drop.querySelector("input[type=file]");
    const name = drop.querySelector(".drop-name");
    const show = () => {
      const f = input.files[0];
      drop.classList.toggle("has-file", Boolean(f));
      name.textContent = f ? `${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)` : name.dataset.empty;
    };
    input.addEventListener("change", () => { show(); importFile(input); });
    input.addEventListener("dragenter", () => drop.classList.add("dragging"));
    for (const ev of ["dragleave", "drop"]) input.addEventListener(ev, () => drop.classList.remove("dragging"));
    drop.reset = () => { input.value = ""; show(); };
  }
  for (const radio of document.querySelectorAll("input[name=txn-source]")) {
    radio.addEventListener("change", () => {
      $("src-csv").hidden = radio.value !== "csv";
      $("src-statement").hidden = radio.value !== "statement";
    });
  }
}

// no account name typed: name it after the file ("chase-2026.csv" -> "chase 2026")
const accountFromFile = (name) => name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || "Account";

const IMPORTS = {
  "txn-file": async (file) => {
    const r = await api.upload("/api/import/transactions", file, { account: $("txn-account").value.trim() || accountFromFile(file.name), purchases_positive: $("txn-positive").checked });
    const skipped = [r.duplicates_skipped && `${r.duplicates_skipped} already imported`, r.outside_tax_year_skipped && `${r.outside_tax_year_skipped} outside 2026`].filter(Boolean);
    return `${plural(r.imported, "transaction")} imported.${skipped.length ? ` Skipped ${skipped.join(" and ")}.` : ""}`;
  },
  "ir-file": async (file) => `${plural((await api.upload("/api/import/info-returns", file)).imported, "1099")} imported.`,
  "mi-file": async (file) => `${plural((await api.upload("/api/import/mileage", file)).imported, "trip")} imported.`,
};

// files import as soon as they're chosen; statements go through "Read statement" instead
async function importFile(input) {
  const file = input.files[0];
  if (!file || !IMPORTS[input.id]) return;
  let note;
  try {
    note = `${file.name}: ${await IMPORTS[input.id](file)}`;
    await afterChange();
  } catch (err) {
    note = `${file.name} wasn't imported: ${err.message}`;
    await refreshStatus();
  }
  input.closest(".drop").reset();
  $("status-line").textContent += `\n${note}`;
}

async function sortTransactions() {
  const log = $("cat-log");
  $("cat-log-box").hidden = false;
  $("cat-log-box").open = true;
  log.textContent = "";
  const write = (line) => { log.textContent += line + "\n"; log.scrollTop = log.scrollHeight; };
  $("categorize-btn").disabled = true;
  try {
    const r = await api.post("/api/auto-categorize");
    write(`Sorted by rules: ${r.from_keywords} by known merchant and ${r.from_decisions} from your earlier answers.`);
    if (r.left_for_model && engine) await categorizeAll(engine, write);
    else if (r.left_for_model) write(`${plural(r.left_for_model, "transaction")} left. Load the AI model (menu › AI Engine), or sort them by hand below.`);
  } catch (err) {
    write(`Error: ${err.message}`);
  }
  $("categorize-btn").disabled = false;
  await afterChange();
}

// the user corrected some answers: let the model redo its other unconfirmed guesses
async function recheckGuesses() {
  if (!engine) return;
  const log = $("cat-log");
  $("cat-log-box").hidden = false;
  $("cat-log-box").open = true;
  log.textContent = "";
  const write = (line) => { log.textContent += line + "\n"; log.scrollTop = log.scrollHeight; };
  $("recheck-btn").disabled = true;
  try {
    await categorizeAll(engine, write, { status: "model_guesses" });
  } catch (err) {
    write(`Error: ${err.message}`);
  }
  $("recheck-btn").disabled = false;
  await afterChange();
}

async function renderReview() {
  const items = await api.get("/api/transactions", { status: "needs_review", limit: 100 });
  $("review-count").textContent = items.length || "";
  const badge = $("nav-review-count");
  badge.textContent = items.length > 99 ? "99+" : items.length;
  badge.hidden = !items.length;
  badge.closest("a").setAttribute("aria-label", items.length ? `Review: ${items.length} need your answer` : "Review");
  const box = $("review");
  box.replaceChildren();
  if (!items.length) {
    const { transactions } = await api.get("/api/status");
    box.append(el("div", { class: "empty" }, transactions
      ? [el("b", {}, "All caught up."), " Nothing needs your review right now."]
      : [el("b", {}, "No transactions yet."), " Import them under Import Records in the menu, then select Sort transactions."]));
    return;
  }
  for (const t of items) box.append(reviewItem(t));
}

function reviewItem(t) {
  const fits = Object.entries(taxonomy).filter(([, c]) => c.direction === "any" || (c.direction === "in") === (t.amount > 0));
  const cat = el("select", {}, el("option", { value: "" }, "Pick a category"),
    fits.map(([k, c]) => el("option", { value: k, selected: k === t.category }, `${c.label}${c.line ? ` (line ${c.line})` : ""}`)));
  const purpose = el("input", { type: "text", placeholder: "Business purpose (who, what and why)", value: t.business_purpose ?? "" });
  const pct = el("input", { type: "number", min: 0, max: 100, placeholder: "Business-use %", value: t.business_use_pct ?? "" });
  const recipient = el("input", { type: "text", placeholder: "Gift recipient", value: t.recipient ?? "" });
  const remember = el("input", { type: "checkbox" });
  const msg = el("span", { class: "muted small" });
  const needs = () => taxonomy[cat.value]?.needs ?? [];
  const rule = el("div", { class: "rule" });
  const sync = () => {
    const c = taxonomy[cat.value];
    rule.textContent = c ? `IRS rule: ${c.rule} (${c.irs})` : "";
    purpose.hidden = !needs().includes("business_purpose");
    pct.hidden = !needs().includes("business_use_pct");
    recipient.hidden = !needs().includes("recipient");
  };
  cat.addEventListener("change", sync);
  sync();
  const save = el("button", { onclick: async () => {
    if (!cat.value) { msg.textContent = "Pick a category first."; return; }
    try {
      const r = await api.post("/api/decisions", {
        txn_id: t.txn_id, category: cat.value, apply_to_merchant: remember.checked,
        business_purpose: purpose.hidden ? null : purpose.value || null,
        business_use_pct: pct.hidden || pct.value === "" ? null : Number(pct.value),
        recipient: recipient.hidden ? null : recipient.value || null,
      });
      msg.textContent = r.still_missing.length ? `Still needed: ${r.still_missing.map((m) => FACT_LABELS[m] ?? m).join(", ")}.` : "Saved.";
      if (!r.still_missing.length) await afterChange();
    } catch (err) { msg.textContent = err.message; }
  } }, "Save");
  return el("div", { class: "review-item" },
    el("div", { class: "head" }, el("strong", {}, t.description), el("span", { class: "num" }, `${t.date} · ${money(t.amount)}`)),
    el("div", { class: "why" }, `${t.reason}${t.rationale ? ` (${t.source === "llm" ? "model" : t.source}: ${t.rationale})` : ""}`),
    el("div", { class: "row" }, cat, purpose, pct, recipient),
    rule,
    el("div", { class: "row" }, el("label", { class: "checkbox" }, remember, "Remember for this merchant"), save, msg));
}

// what the agent is doing, in words (tool names are for the model)
const TOOL_WORDS = {
  get_status: "Checking your progress", list_needs_review: "Looking at what needs review", record_decision: "Saving a category",
  update_profile: "Updating your profile", log_mileage: "Logging a trip", reconcile_income: "Comparing 1099s with deposits",
  apply_adjustment: "Applying an adjustment", compute_schedule_c: "Working out Schedule C", compute_se_and_qbi: "Working out SE tax and QBI",
  plan_estimated_tax: "Planning estimated tax", run_checks: "Running the checks", explain_check: "Saving your explanation",
  search_irs_guidance: "Searching IRS guidance", lookup_irs_rule: "Looking up an IRS rule",
};
const toolWords = (name) => TOOL_WORDS[name] ?? name.replace(/_/g, " ");
const FACT_LABELS = { business_purpose: "business purpose", business_use_pct: "business-use %", recipient: "gift recipient" };
const RECONCILE_STATUS = { matched: "Matched", gross_vs_net: "Gross vs. net (fees)", underreported_in_deposits: "Less in deposits", deposits_exceed_1099: "More in deposits" };

function chatLine(cls, text) {
  $("chat-empty")?.remove();
  const chat = $("chat");
  chat.append(el("div", { class: `msg ${cls}` }, text));
  chat.scrollTop = chat.scrollHeight;
}

async function startAgent() {
  harness = new Harness(engine, {
    onEvent: (type, p) => {
      if (type === "tool_call") chatLine("tool", `${toolWords(p.name)}…`);
      else if (type === "tool_result" && p.result?.error) chatLine("error", `${toolWords(p.name)} didn't work: ${p.result.error}`);
      else if (type === "ask" || type === "say") chatLine("bot", p);
      else if (type === "error") chatLine("error", p);
      else if (type === "checks") chatLine("tool", `Checks: ${plural(p.open_blockers, "open blocker")} and ${plural(p.warnings, "warning")}.`);
    },
    augment: { search_irs_guidance: (args) => irs.withVector(args) },
    confirm: async (name, args) => window.confirm(`TaxPal wants to do this: ${toolWords(name).toLowerCase()}.\n\n` +
      Object.entries(args).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${FACT_LABELS[k] ?? k.replace(/_/g, " ")}: ${v}`).join("\n") +
      "\n\nAllow?"),
  });
  await harness.init();
  $("chat-input").disabled = $("chat-send").disabled = false;
  for (const chip of document.querySelectorAll("[data-prompt]")) chip.disabled = false;
  $("chat-hint").hidden = true;
}

// Chat / IRS guidance tabs on the Ask page, and the example questions
function setupAsk() {
  const tabs = [$("tab-chat"), $("tab-irs")];
  const select = (tab) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
  };
  for (const t of tabs) {
    t.onclick = () => select(t);
    t.onkeydown = (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const other = tabs[1 - tabs.indexOf(t)];
      select(other);
      other.focus();
    };
  }
  for (const chip of document.querySelectorAll("[data-prompt]")) {
    chip.onclick = () => { $("chat-input").value = chip.dataset.prompt; $("chat-form").requestSubmit(); };
  }
  for (const chip of document.querySelectorAll("[data-irs]")) {
    chip.onclick = () => { $("irs-query").value = chip.dataset.irs; $("irs-form").requestSubmit(); };
  }
}

async function sendChat(e) {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text || !harness) return;
  $("chat-input").value = "";
  chatLine("user", text);
  $("chat-input").disabled = $("chat-send").disabled = true;
  await harness.send(text);
  $("chat-input").disabled = $("chat-send").disabled = false;
  $("chat-input").focus();
  await Promise.all([refreshStatus(), renderReview(), renderChecks()]);
}

// layout for the profile form; field names and validation come from /api/profile
const PROFILE_GROUPS = [
  { title: "Filing", fields: {
    filing_status: { label: "Filing status", options: {
      single: "Single", married_joint: "Married filing jointly", married_separate: "Married filing separately",
      head_of_household: "Head of household", qualifying_surviving_spouse: "Qualifying surviving spouse" } } } },
  { title: "W-2 job", note: "Only if you also had a salaried job in 2026.", fields: {
    w2_ss_wages: { label: "Social Security wages", unit: "$", help: "Form W-2, box 3." },
    w2_medicare_wages: { label: "Medicare wages", unit: "$", help: "Form W-2, box 5. Leave blank to use box 3." } } },
  { title: "Home office and vehicle", fields: {
    home_office_sqft: { label: "Home office size", unit: "sq ft", help: "Space used only and regularly for work. Up to 300." },
    vehicle_method: { label: "Vehicle deduction", options: { standard: "Standard mileage rate", actual: "Actual expenses" } },
    vehicle_business_pct: { label: "Business use of vehicle", unit: "%", help: "Needed for actual expenses only." } } },
  { title: "Estimated tax for 2027", note: "Used to plan next year's quarterly payments.", fields: {
    prior_year_total_tax: { label: "Total tax for 2026", unit: "$", help: "Form 1040, total tax line." },
    prior_year_agi: { label: "Adjusted gross income for 2026", unit: "$", help: "Form 1040, AGI line." },
    projected_tax: { label: "Expected total tax for 2027", unit: "$", help: "Optional. Fill in if you know it." },
    expected_withholding: { label: "Expected 2027 withholding", unit: "$", help: "Federal income tax withheld from W-2 jobs." } } },
  { title: "Qualified business income (QBI)", note: "Only matters if your taxable income is high.", fields: {
    taxable_income_before_qbi: { label: "Taxable income before QBI", unit: "$", help: "Your estimate for 2026." },
    net_capital_gain: { label: "Net capital gain", unit: "$", help: "For 2026, if any." } } },
];

async function renderProfile() {
  const { profile, fields } = await api.get("/api/profile");
  const form = $("profile-form");
  form.replaceChildren();
  const placed = new Set();
  const field = (key, spec) => {
    placed.add(key);
    const id = `pf-${key}`;
    const input = spec.options
      ? el("select", { id, name: key }, Object.entries(spec.options).map(([v, text]) => el("option", { value: v, selected: profile[key] === v }, text)))
      : el("input", { id, type: "number", min: 0, step: "any", name: key, value: profile[key] ?? "", inputmode: "decimal" });
    const box = spec.unit
      ? el("div", { class: `unit-input ${spec.unit === "$" ? "prefix" : "suffix"}` }, el("span", { class: "unit" }, spec.unit), input)
      : input;
    return el("div", { class: "field" }, el("label", { for: id }, spec.label), box,
      spec.help ? el("span", { class: "muted small" }, spec.help) : null);
  };
  for (const g of PROFILE_GROUPS) {
    const keys = Object.keys(g.fields).filter((k) => k in fields);
    if (!keys.length) continue;
    form.append(el("fieldset", { class: "profile-group" }, el("legend", {}, g.title),
      g.note ? el("p", { class: "muted small" }, g.note) : null,
      el("div", { class: "grid2" }, keys.map((k) => field(k, g.fields[k])))));
  }
  // fields the API added that this layout doesn't know about yet
  const rest = Object.keys(fields).filter((k) => !placed.has(k));
  if (rest.length) form.append(el("fieldset", { class: "profile-group" }, el("legend", {}, "Other"),
    el("div", { class: "grid2" }, rest.map((k) => field(k, { label: k.replace(/_/g, " "), help: fields[k] })))));
  const method = form.elements.vehicle_method, pct = form.elements.vehicle_business_pct;
  if (method && pct) {
    const sync = () => { pct.disabled = method.value !== "actual"; };
    method.addEventListener("change", sync);
    sync();
  }
}

async function saveProfile() {
  const changes = {};
  for (const input of $("profile-form").elements) {
    if (!input.name || input.disabled || input.value === "") continue;
    changes[input.name] = input.tagName === "SELECT" ? input.value : Number(input.value);
  }
  try {
    await api.put("/api/profile", changes);
    $("profile-status").textContent = "Profile saved. Your results are up to date.";
    await showView();
  } catch (err) {
    $("profile-status").textContent = err.message;
  }
}

const flagsList = (flags) => (flags?.length ? el("ul", { class: "flags" }, flags.map((f) => el("li", {}, f))) : null);

// shown wherever a result can't be worked out yet: what's missing and where to add it
const notReady = (...parts) => el("div", { class: "empty" }, el("b", {}, "Not worked out yet."), " ", ...parts);
const link = (href, text) => el("a", { href }, text);
const stats = (items) => el("div", { class: "stats" }, items.map(([label, value, strong]) =>
  el("div", { class: `stat${strong ? " strong" : ""}` }, el("span", { class: "muted small" }, label), el("b", {}, value))));
const longDate = (d) => new Date(`${d}T00:00`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
const noTransactions = () => notReady("Import your bank or card transactions in ", link("#import", "Import Records"), ", then sort them under ", link("#review", "Review"), ".");

const views = {
  "schedule-c": (r, st) => {
    if (!st.transactions) return [noTransactions()];
    const lines = Object.values(r.lines);
    return [
      r.complete ? null : el("div", { class: "status-panel warn" }, el("b", {}, "Provisional"),
        el("span", {}, `${plural(st.needs_review + st.uncategorized, "transaction")} still ${st.needs_review + st.uncategorized === 1 ? "needs" : "need"} an answer. `, link("#review", "Go to Review"), ".")),
      stats([["Gross income", money(r.gross_income)], ["Total expenses", money(r.total_expenses)], ["Net profit", money(r.net_profit), true]]),
      lines.length
        ? table(["Income and expenses", "Amount"], [
            ...lines.map((v) => [v.name, money(v.amount)]),
            ...(r.home_office ? [["Business use of home (simplified)", money(r.home_office)]] : [])], [1])
        : el("p", { class: "muted" }, "Nothing sorted into Schedule C yet."),
      flagsList(r.flags)];
  },
  "se-qbi": (r, st) => {
    if (!st.transactions) return [noTransactions()];
    const se = r.schedule_se, q = r.qbi;
    return [
      stats([["Self-employment tax", money(se.se_tax), true],
             ["QBI deduction", q.deduction === null ? "See a tax professional" : money(q.deduction)]]),
      table(["Item", "Amount"], [
        ["Net profit", money(r.net_profit)], ["Net earnings from self-employment (\u00d7 92.35%)", money(se.net_earnings)],
        ["Social Security (12.4%)", money(se.social_security)], ["Medicare (2.9%)", money(se.medicare)],
        ["Deductible half of SE tax (Schedule 1)", money(se.deductible_half)],
        ["Qualified business income", money(q.qbi)],
        [`QBI deduction (Form ${q.form})`, q.deduction === null ? "Not worked out" : money(q.deduction)]], [1]),
      flagsList([...se.flags, ...q.flags])];
  },
  reconcile: (r) => {
    if (!r.info_returns.length) return [notReady("Import the 1099 forms you received in ", link("#import", "Import Records"), " to compare them with your deposits.")];
    return [
      stats([["Reported on your 1099s", money(r.reported_on_1099s_total)], ["Income deposits found", money(r.income_deposits_total)]]),
      table(["Form", "Payer", "Reported", "Deposits", "Status"], r.info_returns.map((x) => [x.form, x.payer, money(x.reported), money(x.deposits_found),
        el("span", { class: `pill ${x.status === "matched" ? "" : "pill-warn"}` }, RECONCILE_STATUS[x.status] ?? x.status)]), [2, 3]),
      ...r.info_returns.filter((x) => x.suggested_adjustment).map((x) => el("div", { class: "suggestion" },
        el("span", {}, x.suggested_adjustment.description),
        el("button", { class: "secondary small", onclick: async (e) => {
          e.target.disabled = true;
          await applyAdjustment(x.suggested_adjustment);
          e.target.textContent = "Applied";
        } }, "Apply"))),
      r.unexplained_deposits.length ? el("h3", {}, "Deposits not matched to a 1099") : null,
      r.unexplained_deposits.length ? table(["Date", "Description", "Amount"], r.unexplained_deposits.map((d) => [d.date, d.description, money(d.amount)]), [2]) : null,
      el("p", { class: "muted small" }, r.note)];
  },
  "estimated-tax": (r) => r.required_annual_payment === null
    ? [notReady("Add your 2026 total tax and adjusted gross income, or your expected 2027 tax, in your ", link("#profile", "Profile"), ".")]
    : [stats([["To pay in 2027", money(r.estimated_payments_needed), true], ["Each quarter", money(r.quarters[0]?.amount ?? 0)]]),
       el("p", { class: "muted small" }, `Based on ${r.safe_harbor_basis} (${money(r.required_annual_payment)}), minus ${money(r.expected_withholding)} of expected withholding.`),
       table(["Due date", "Amount"], r.quarters.map((q) => [longDate(q.due), money(q.amount)]), [1]), flagsList(r.notes)],
};

async function applyAdjustment(suggested) {
  for (const a of suggested.adjustments) await api.post("/api/adjustments", a);
  await renderChecks();
}

// after any fix: refresh everything that depends on it
async function afterChange() {
  await Promise.all([refreshStatus(), renderReview(), renderChecks(), refreshLearning(), renderFiles(),
    $("page-home").hidden ? null : renderHome()]);
}

function checkActions(c) {
  const out = [];
  const msg = el("span", { class: "muted small" });
  const run = (fn) => async (e) => {
    e.target.disabled = true;
    try { await fn(); await afterChange(); } catch (err) { msg.textContent = err.message; e.target.disabled = false; }
  };
  const confirm = (ids) => api.post("/api/confirm", { txn_ids: ids });

  if (c.id === "model_unconfirmed") {
    out.push(el("div", { class: "row" }, el("button", { onclick: run(() => confirm(c.items.map((t) => t.txn_id))) },
      `Confirm all ${c.items.length}`)));
    for (const t of c.items) {
      const slot = el("div", { class: "check-item" });
      slot.append(
        el("div", { class: "head" }, el("strong", {}, t.description), el("span", { class: "num" }, `${t.date} · ${money(t.amount)}`)),
        el("div", { class: "why" }, `The AI chose ${taxonomy[t.category]?.label ?? t.category} (${Math.round(t.confidence * 100)}% sure): ${t.rationale || "no reason given."}`),
        el("div", { class: "row" },
          el("button", { class: "secondary", onclick: run(() => confirm([t.txn_id])) }, "Looks right"),
          el("button", { class: "secondary", onclick: () => slot.replaceChildren(reviewItem({ ...t, reason: "You're correcting the AI's choice." })) }, "Change")));
      out.push(slot);
    }
  } else if (c.id === "spot_check") {
    for (const g of c.groups) {
      out.push(el("div", { class: "check-item row" },
        el("span", {}, `${g.example} · ${plural(g.count, "payment")}, ${money(g.total)} → ${taxonomy[g.category]?.label ?? g.category}`),
        el("button", { class: "secondary", onclick: run(() => confirm(g.txn_ids)) }, "Looks right")));
    }
  } else if (c.items?.length) {
    // the transactions behind a risk check
    out.push(el("ul", { class: "small" }, c.items.map((t) =>
      el("li", {}, `${t.date} · ${t.description} · ${money(t.amount)}${t.account ? ` · ${t.account}` : ""}${t.category ? ` → ${taxonomy[t.category]?.label ?? t.category}` : ""}`))));
  } else if (c.id === "uncategorized" || c.id === "needs_review") {
    out.push(el("p", { class: "muted small" }, "Answer these under Review in the top bar."));
  }

  if (c.suggested_adjustment) {
    out.push(el("div", { class: "row" }, el("button", { onclick: run(() => applyAdjustment(c.suggested_adjustment)) }, "Apply adjustment")));
  }
  if (c.can_acknowledge && !c.acknowledged) {
    const note = el("input", { type: "text", placeholder: "Or explain in your own words why this is correct", maxlength: 500 });
    out.push(el("div", { class: "row" }, note,
      el("button", { class: "secondary", onclick: run(() => api.post(`/api/checks/${encodeURIComponent(c.id)}/acknowledge`, { note: note.value })) }, "Save explanation")));
  }
  if (c.acknowledged) out.push(el("p", { class: "small" }, `Your explanation: ${c.acknowledged.note}`));
  out.push(msg);
  return out;
}

function signoffHint(ok) {
  $("signoff-hint").textContent = !ok ? "Fix or explain the checks above first."
    : $("signoff-agree").checked ? "" : "Tick the box to confirm.";
}

async function renderChecks() {
  let r;
  try { r = await api.get("/api/checks"); } catch (err) { $("checks").replaceChildren(el("p", {}, err.message)); return; }
  $("checks-count").textContent = r.open_blockers || "";
  const navBadge = $("nav-checks-count");
  navBadge.textContent = r.open_blockers;
  navBadge.hidden = !r.open_blockers;
  const s = r.signoff;
  const warns = r.warnings ? ` ${r.warnings} warning${r.warnings === 1 ? "" : "s"} to look at.` : "";
  const [kind, title, detail] = s?.current
    ? ["ok", `Signed off ${new Date(s.signed_at.replace(" ", "T") + "Z").toLocaleString()}`, "Your summary is final until something changes."]
    : s ? ["warn", "Back to draft", "Your data changed after you signed off. Check the items below and sign off again."]
    : r.ok ? ["ok", "Ready to sign off", `All blockers are fixed or explained.${warns}`]
    : ["warn", `${r.open_blockers} thing${r.open_blockers === 1 ? "" : "s"} to fix before you can sign off`, `Fix each one below, or explain it in your own words.${warns}`];
  $("signoff-status").className = `status-panel ${kind}`;
  $("signoff-status").replaceChildren(el("b", {}, title), el("span", {}, detail));

  const order = { blocker: 0, warning: 1 };
  const sorted = [...r.checks].sort((a, b) => (order[a.severity] - order[b.severity]) || (!!a.acknowledged - !!b.acknowledged));
  $("checks").replaceChildren(
    ...(sorted.length ? sorted.map((c) => el("div", { class: `check ${c.severity}${c.acknowledged ? " done" : ""}` },
      el("div", { class: "head" }, el("span", { class: "pill" }, c.acknowledged ? "explained" : c.severity), el("span", {}, c.message)),
      ...checkActions(c))) : [el("div", { class: "empty" }, el("b", {}, "All checks pass."), " Nothing to fix.")]));
  $("signoff-agree").disabled = !r.ok;
  if (!r.ok) $("signoff-agree").checked = false;
  $("signoff-btn").disabled = !(r.ok && $("signoff-agree").checked);
  signoffHint(r.ok);
}

async function signOff() {
  try {
    await api.post("/api/signoff", { confirm: true });
  } catch (err) {
    $("signoff-status").textContent = err.message;
    return;
  }
  $("signoff-agree").checked = false;
  await renderChecks();
}

const RESULT_VIEWS = {
  "schedule-c": ["Business Profit", "Schedule C: your business income minus business expenses, line by line."],
  "se-qbi": ["Self-Employment Tax", "Estimated self-employment tax (Schedule SE) and qualified business income (QBI) deduction."],
  reconcile: ["1099 Check", "The 1099s you received compared with the deposits in your accounts."],
  "estimated-tax": ["2027 Payments", "Quarterly estimated tax for next year (Form 1040-ES)."],
};
let currentView = "schedule-c";

async function showView(name = currentView) {
  if (!RESULT_VIEWS[name]) name = "schedule-c";
  currentView = name;
  const [title, desc] = RESULT_VIEWS[name];
  $("results-title").textContent = title;
  if (!$("page-results").hidden) $("topbar-title").textContent = title;
  $("results-desc").textContent = desc;
  const keys = Object.keys(RESULT_VIEWS);
  const next = keys[keys.indexOf(name) + 1];
  $("results-next").href = next ? `#results/${next}` : "#signoff";
  $("results-next").replaceChildren(`Next: ${next ? RESULT_VIEWS[next][0] : "Check and Sign Off"} `, el("span", { "aria-hidden": "true" }, "\u2192"));
  try {
    const [r, st] = await Promise.all([api.get(`/api/${name}`), api.get("/api/status")]);
    $("results").replaceChildren(...views[name](r, st).filter(Boolean));
  } catch (err) {
    $("results").replaceChildren(el("p", {}, err.message));
  }
}

let guideLoaded = false;

async function renderGuide() {
  if (guideLoaded) return;
  const docs = await api.get("/api/irs/documents");
  const SHORT = { i1040sc: "Schedule C", i1040sse: "Schedule SE", i8995: "Form 8995" };
  const docItem = (d) => el("details", { class: "doc-item" },
    el("summary", {},
      el("span", { class: "doc-code" }, SHORT[d.doc] ?? `Pub ${d.doc.replace(/^p/, "")}`),
      el("span", { class: "doc-head" }, el("b", {}, d.name ?? d.title), el("span", { class: "muted small" }, d.summary ?? ""))),
    el("div", { class: "doc-body" },
      d.key_points?.length ? el("ul", { class: "plain-list" }, d.key_points.map((k) => el("li", {}, k))) : null,
      d.used_for ? el("p", {}, el("b", {}, "Used for: "), d.used_for) : null,
      el("p", { class: "muted small" }, el("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, `Read ${d.title} on irs.gov`))));
  $("guide-docs").replaceChildren(el("div", { class: "doc-list" }, docs.map(docItem)));
  const rows = Object.values(taxonomy).filter((c) => c.line)
    .sort((a, b) => parseFloat(a.line) - parseFloat(b.line) || a.line.localeCompare(b.line))
    .map((c) => [c.label, `Line ${c.line}`]);
  $("guide-rules").replaceChildren(table(["Cost", "Schedule C line"], rows));
  guideLoaded = true;
}

const FILE_TYPES = { transactions: "Bank or card CSV", statement: "Statement", "1099s": "1099 list", mileage: "Mileage trips" };

async function renderFiles() {
  const files = await api.get("/api/files");
  const box = $("files-list");
  if (!files.length) {
    box.replaceChildren(el("div", { class: "empty" }, el("b", {}, "No files yet."), " ", el("a", { href: "#import" }, "Import your records"), " to get started."));
    return;
  }
  const dates = (f) => (f.first_date ? (f.first_date === f.last_date ? f.first_date : `${f.first_date} to ${f.last_date}`) : "");
  box.replaceChildren(table(["File", "Type", "Account", "Items", "Dates"],
    files.map((f) => [f.name, FILE_TYPES[f.type] || f.type, f.account || "", f.items, dates(f)]), [3]));
}

const PAGES = ["home", "engine", "import", "files", "review", "ask", "profile", "results", "signoff", "guide", "privacy"];

// one page at a time, picked by the URL hash (#results/se-qbi) so the back button works
function showPage(route, focus = true) {
  let [name, view] = route.split("/");
  if (!PAGES.includes(name)) name = "home";
  if (name === "results") view = RESULT_VIEWS[view] ? view : currentView;
  const current = view ? `${name}/${view}` : name;
  for (const p of document.querySelectorAll("[data-page]")) p.hidden = p.dataset.page !== name;
  for (const a of document.querySelectorAll("[data-page-link]")) {
    if (a.dataset.pageLink === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  const inResults = name === "results" || name === "signoff";
  $("results-toggle").classList.toggle("active", inResults);
  if (inResults) setResultsMenu(true);
  decoratePage(name);
  closeMenu();
  window.scrollTo(0, 0);
  if (focus) {
    const h = $(`page-${name}`).querySelector("h2");
    h.tabIndex = -1;
    h.focus({ preventScroll: true });
  }
  if (name === "results") showView(view);
  if (name === "files") renderFiles();
  if (name === "guide") renderGuide();
  if (name === "signoff") renderChecks();
  if (name === "home") renderHome();
}

// page header: the menu's icon for this page in the first card, and the page title in the top bar
function decoratePage(name) {
  const page = $(`page-${name}`);
  const linkFor = { results: "results-toggle", signoff: "results-toggle" }[name];
  const navIcon = (linkFor ? $(linkFor) : document.querySelector(`.drawer [data-page-link="${name}"]`))?.querySelector("svg");
  const card = page.querySelector(".card");
  if (name !== "home" && navIcon && card && !card.querySelector(".page-icon")) {
    card.prepend(el("span", { class: "page-icon", "aria-hidden": "true" }, navIcon.cloneNode(true)));
  }
  const title = page.querySelector("h2")?.textContent.trim();
  $("topbar-title").textContent = name === "home" ? "" : title ?? "";
}

// desktop: the menu is a sidebar that's always there; small screens: a drawer behind the menu button
const wide = window.matchMedia("(min-width: 1080px)");
function syncDrawerMode() {
  const drawer = $("drawer");
  if (wide.matches) {
    drawer.classList.remove("open");
    $("drawer-backdrop").hidden = true;
    drawer.removeAttribute("inert");
    drawer.setAttribute("aria-hidden", "false");
  } else if (!drawer.classList.contains("open")) {
    drawer.setAttribute("inert", "");
    drawer.setAttribute("aria-hidden", "true");
  }
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

async function renderHome() {
  const [st, sc, sq, signoff] = await Promise.all([api.get("/api/status"), api.get("/api/schedule-c"),
    api.get("/api/se-qbi"), api.get("/api/signoff")]);
  const open = st.uncategorized + st.needs_review;
  const steps = [
    { href: "#engine", title: "Load the AI", optional: true, done: Boolean(engine),
      text: engine ? "The AI is ready." : "Optional. Helps sort transactions and answer questions." },
    { href: "#import", title: "Import your records", done: st.transactions > 0,
      text: st.transactions ? `${plural(st.transactions, "transaction")} imported.` : "Add your bank or card files." },
    { href: "#review", title: "Sort and review", done: st.transactions > 0 && open === 0,
      text: !st.transactions ? "Comes after importing." : open ? `${plural(open, "transaction")} to check.` : "Everything is sorted." },
    { href: "#profile", title: "Fill in your profile", done: st.profile_missing.length === 0,
      text: st.profile_missing.length ? "A few answers change your tax." : "Your profile is complete." },
    { href: "#signoff", title: "Check and sign off", done: Boolean(signoff?.current),
      text: signoff?.current ? "Signed off. Your summary is final." : "Confirm everything is right." },
  ];
  const next = steps.find((s) => !s.done && !s.optional) ?? steps[steps.length - 1];
  $("home-steps").replaceChildren(...steps.map((s, i) => el("li", { class: `step-item${s.done ? " done" : ""}${s === next ? " current" : ""}` },
    el("span", { class: "step-dot", "aria-hidden": "true" }, s.done ? "\u2713" : String(i + 1)),
    el("span", { class: "step-text" }, el("b", {}, s.title, s.optional ? el("span", { class: "tag" }, "optional") : null), el("span", { class: "muted small" }, s.text)),
    s === next ? el("a", { class: "button small", href: s.href }, s.done ? "View" : "Start") : el("a", { class: "step-link", href: s.href, "aria-label": s.title }, "\u2192"))));
  $("home-greeting").textContent = st.transactions ? `${greeting()}!` : "Welcome to TaxPal";
  const cta = $("home-cta");
  cta.href = next.href;
  cta.firstChild.textContent = st.transactions ? `Continue: ${next.title} ` : "Get started ";
  $("home-stats").replaceChildren(...stats([
    ["Transactions", st.transactions.toLocaleString("en-US")],
    ["To review", String(open)],
    ["Net profit", money(sc.net_profit), true],
    ["Self-employment tax", money(sq.schedule_se.se_tax)],
  ]).children);
}

function setResultsMenu(open) {
  $("results-toggle").setAttribute("aria-expanded", String(open));
  $("results-sub").hidden = !open;
}

function openMenu() {
  $("drawer").classList.add("open");
  $("drawer").removeAttribute("inert");
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-backdrop").hidden = false;
  $("menu-btn").setAttribute("aria-expanded", "true");
  $("drawer").querySelector("[aria-current]")?.focus() ?? $("menu-close").focus();
}

function closeMenu() {
  if (!$("drawer").classList.contains("open") || wide.matches) return;
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("inert", "");
  $("drawer").setAttribute("aria-hidden", "true");
  $("drawer-backdrop").hidden = true;
  $("menu-btn").setAttribute("aria-expanded", "false");
  $("menu-btn").focus();
}

function setupNavigation() {
  $("menu-btn").onclick = openMenu;
  $("menu-close").onclick = closeMenu;
  $("drawer-backdrop").onclick = closeMenu;
  $("results-toggle").onclick = () => setResultsMenu($("results-sub").hidden);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  wide.addEventListener("change", syncDrawerMode);
  syncDrawerMode();
  window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));
  for (const a of document.querySelectorAll("a[href^='#']")) {  // same link twice: hashchange won't fire, close the menu anyway
    a.addEventListener("click", () => { if (a.getAttribute("href") === location.hash) showPage(location.hash.slice(1)); });
  }
}

async function main() {
  taxonomy = await api.get("/api/taxonomy");
  setupImportForm();
  $("sample-btn").onclick = async () => {
    try { await api.post("/api/import/sample"); } catch (err) { $("status-line").textContent = err.message; return; }
    await afterChange();
  };
  $("reset-btn").onclick = async () => {
    if (!window.confirm("Delete all imported data, your answers and your profile from this computer? This can't be undone.")) return;
    await api.post("/api/reset");
    await Promise.all([afterChange(), renderProfile()]);
    await showView();
  };
  setupStatementImport({ el, money, plural, getEngine: () => engine, afterChange });
  $("categorize-btn").onclick = sortTransactions;
  $("recheck-btn").onclick = recheckGuesses;
  $("chat-form").onsubmit = sendChat;
  setupAsk();
  $("profile-save").onclick = saveProfile;
  $("irs-build").onclick = buildIrsIndex;
  $("irs-form").onsubmit = searchIrs;
  $("checks-btn").onclick = renderChecks;
  $("signoff-btn").onclick = signOff;
  $("signoff-agree").onchange = () => {
    $("signoff-btn").disabled = !$("signoff-agree").checked;
    signoffHint(true);
  };
  setupNavigation();
  const start = location.hash.slice(1);
  if (PAGES.includes(start.split("/")[0])) showPage(start, false);
  await Promise.all([afterChange(), renderProfile(), setupEngine(), refreshIrsStatus()]);
  if (!PAGES.includes(start.split("/")[0])) showPage("home", false);
}

main();
