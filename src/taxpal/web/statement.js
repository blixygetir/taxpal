// PDF/DOCX statement import: the server parses it into a preview, the user checks it
// (the balance check says whether every line was read), fixes anything off, then imports.
// If the layout defeats the parser, the model can read the text instead; its rows go
// through the same preview and balance check.
import { api } from "./api.js";

const CHUNK_CHARS = 2400; // the 3B model has a 4k context

function aiSchema() {
  return JSON.stringify({
    type: "object",
    properties: {
      transactions: {
        type: "array",
        items: {
          type: "object",
          properties: { date: { type: "string" }, description: { type: "string" }, amount: { type: "number" } },
          required: ["date", "description", "amount"],
        },
      },
    },
    required: ["transactions"],
  });
}

function chunks(text) {
  const out = [];
  let cur = "";
  for (const line of text.split("\n")) {
    if (cur.length + line.length > CHUNK_CHARS && cur) { out.push(cur); cur = ""; }
    cur += line + "\n";
  }
  if (cur.trim()) out.push(cur);
  return out;
}

export async function extractWithModel(engine, preview, onLog) {
  const kind = preview.kind === "card" ? "credit card" : "bank account";
  const period = preview.period ? ` covering ${preview.period[0]} to ${preview.period[1]}` : "";
  const rows = [];
  const parts = chunks(preview.text);
  for (const [i, part] of parts.entries()) {
    onLog(`The AI is reading part ${i + 1} of ${parts.length}…`);
    const reply = await engine.chat.completions.create({
      messages: [{ role: "user", content: `This is text from a ${kind} statement${period}. List every transaction in it.
- date as YYYY-MM-DD
- description as printed
- amount: positive for money INTO the account (${preview.kind === "card" ? "payments and refunds" : "deposits"}), negative for money OUT (${preview.kind === "card" ? "purchases, fees, interest" : "withdrawals, checks, fees"})
- skip balances, totals, summaries and headers
Return {"transactions": [...]}. If there are none, return an empty list.

${part}` }],
      response_format: { type: "json_object", schema: aiSchema() },
      temperature: 0,
      max_tokens: 1200,
    });
    try {
      for (const t of JSON.parse(reply.choices[0].message.content).transactions ?? []) {
        if (/^\d{4}-\d{2}-\d{2}$/.test(t.date) && t.description && Number.isFinite(t.amount)) {
          rows.push({ date: t.date, description: String(t.description).slice(0, 200), amount: Math.round(t.amount * 100) / 100, sign_from: "AI" });
        }
      }
    } catch { onLog(`Part ${i + 1} was skipped because the AI's answer couldn't be read.`); }
  }
  return rows;
}

export function setupStatementImport({ el, money, plural, getEngine, afterChange }) {
  const $ = (id) => document.getElementById(id);
  let preview = null;

  async function recheck() {
    const included = preview.rows.filter((r) => !r.excluded);
    preview.check = preview.opening === null || preview.closing === null ? null
      : await api.post("/api/import/statement/check", { kind: preview.kind, opening: preview.opening, closing: preview.closing,
          rows: included.map(({ date, description, amount }) => ({ date, description, amount })) });
    render();
  }

  function render() {
    const box = $("stmt-preview");
    if (!preview) { box.replaceChildren(); return; }
    if (preview.needs_ocr) {
      box.replaceChildren(el("p", { class: "disclaimer" }, "This file has no readable text; it's probably a scanned image. TaxPal can't read scans, so download a CSV or a text-based PDF from your bank instead."));
      return;
    }
    const included = preview.rows.filter((r) => !r.excluded);
    const c = preview.check;
    const checkLine = c === null
      ? el("p", { class: "check warning" }, "No opening or closing balance was found, so TaxPal can't confirm that every line was read. Compare the count with your statement.")
      : c.ok
        ? el("p", { class: "check done" }, `✓ All ${plural(included.length, "transaction")} add up to the statement's balance change (${money(c.expected_change)}).`)
        : el("p", { class: "check blocker" }, `✗ Off by ${money(Math.abs(c.difference))}. The transactions come to ${money(c.parsed_change)}, but the balance changed by ${money(c.expected_change)}. A line may be missing, read twice or have the wrong sign.`);

    const kindSelect = el("select", { onchange: async (e) => { await read(e.target.value); } },
      el("option", { value: "bank", selected: preview.kind === "bank" }, "Bank account"),
      el("option", { value: "card", selected: preview.kind === "card" }, "Credit card"));
    const rows = preview.rows.map((r) => [
      el("input", { type: "checkbox", checked: !r.excluded, onchange: async (e) => { r.excluded = !e.target.checked; await recheck(); } }),
      r.date, r.description, money(r.amount),
      el("button", { class: "secondary small", onclick: async () => { r.amount = -r.amount; r.sign_from = "you"; await recheck(); } }, "Flip sign"),
      r.sign_from]);

    const anyway = el("input", { type: "checkbox", id: "stmt-anyway" });
    const importBtn = el("button", { onclick: () => doImport(anyway.checked) }, `Import ${plural(included.length, "transaction")}`);
    const engine = getEngine();
    box.replaceChildren(
      el("div", { class: "row" }, el("span", {}, "Statement type"), kindSelect,
        el("span", { class: "muted small" }, `${preview.period ? `${preview.period[0]} to ${preview.period[1]} · ` : ""}` +
          `opening ${money(preview.opening)} · closing ${money(preview.closing)}`)),
      checkLine,
      ...preview.warnings.map((w) => el("p", { class: "check warning" }, w)),
      preview.unparsed.length ? el("details", {}, el("summary", {}, `${plural(preview.unparsed.length, "line")} with a date and amount couldn't be read`),
        el("ul", { class: "small" }, preview.unparsed.map((u) => el("li", {}, u.text)))) : null,
      table(["", "Date", "Description", "Amount", "", "Sign from"], rows, [3]),
      engine && (!c?.ok || !preview.rows.length)
        ? el("div", { class: "row" }, el("button", { class: "secondary", onclick: readWithAI }, "Let the AI read it instead"),
            el("span", { class: "muted small" }, "This uses the model on your GPU. Its rows go through the same balance check."))
        : null,
      el("div", { class: "row" },
        c && !c.ok ? el("label", { class: "checkbox" }, anyway, "Import anyway (you'll need to explain the difference before you sign off)") : null,
        importBtn),
      el("p", { id: "stmt-msg", class: "muted small" }));

    function table(headers, body, numeric) {
      return el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: numeric.includes(i) ? "num" : "" }, h)))),
        el("tbody", {}, body.map((r, ri) => el("tr", { class: preview.rows[ri].excluded ? "excluded" : "" },
          r.map((cell, i) => el("td", { class: numeric.includes(i) ? "num" : "" }, cell)))))));
    }
  }

  async function read(kind = $("stmt-kind").value) {
    const file = $("stmt-file").files[0];
    if (!file) { $("stmt-preview").replaceChildren(el("p", {}, "Choose a PDF or Word statement first.")); return; }
    $("stmt-preview").replaceChildren(el("p", { class: "muted" }, "Reading…"));
    try {
      preview = await api.upload("/api/import/statement", file, { kind });
    } catch (err) {
      preview = null;
      $("stmt-preview").replaceChildren(el("p", {}, err.message));
      return;
    }
    render();
  }

  async function readWithAI() {
    const msg = (t) => { const m = $("stmt-msg"); if (m) m.textContent = t; };
    try {
      const rows = await extractWithModel(getEngine(), preview, msg);
      preview.rows = rows;
      preview.unparsed = [];
      await recheck();
      msg(`The AI found ${plural(rows.length, "transaction")}. Check them against your statement before you import.`);
    } catch (err) { msg(`The AI couldn't read it: ${err.message}`); }
  }

  async function doImport(anyway) {
    const included = preview.rows.filter((r) => !r.excluded);
    if (preview.check && !preview.check.ok && !anyway) {
      $("stmt-msg").textContent = "The transactions don't add up to the balance change. Fix them, or tick “Import anyway.”";
      return;
    }
    try {
      const r = await api.post("/api/import/statement/confirm", {
        account: $("stmt-account").value || preview.kind, source_file: preview.source_file, kind: preview.kind,
        period: preview.period, opening: preview.opening, closing: preview.closing,
        rows: included.map(({ date, description, amount }) => ({ date, description, amount })),
      });
      preview = null;
      $("stmt-preview").replaceChildren(el("p", {}, `Imported ${plural(r.imported, "transaction")}. Skipped ${r.duplicates_skipped} already imported and ${r.outside_tax_year_skipped} outside 2026. Next, select Sort transactions under Review.`));
      await afterChange();
    } catch (err) { $("stmt-msg").textContent = err.message; }
  }

  $("stmt-read").onclick = () => read();
}
