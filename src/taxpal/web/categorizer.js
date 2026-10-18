// Sorts whatever the keyword rules couldn't. WebLLM constrains output to the schema,
// and the API checks it again anyway. Anything the API rejects (or the model skips)
// goes back to the model with the reason, a couple of times, then to the review queue.
// Even accepted answers need a human click before sign-off (see checks.py).
//
// Learning from the user: each prompt includes the user's past answers that look like
// this batch (POST /api/examples), including where they corrected the model. That's
// in-context learning, nothing is trained, so it costs a few hundred prompt tokens.
import { api } from "./api.js";
import { SORT_BATCH } from "./llm.js";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const MAX_ATTEMPTS = 3;
const EXAMPLES = 6;

function describeExample(e) {
  const fix = e.kind === "corrected" ? ` (it was guessed as ${e.suggested}; the user corrected it)` : "";
  return `- "${e.description}" (money ${e.direction}) -> ${e.answer}${fix}`;
}

function buildPrompt(taxonomy, batch, feedback = {}, examples = []) {
  const cats = Object.entries(taxonomy)
    .map(([key, c]) => `- ${key} [${c.direction}]: ${c.label}. ${c.rule}`)
    .join("\n");
  const items = batch
    .map((t, i) => `${i}. ${t.date} | ${t.description} | ${t.amount > 0 ? "MONEY IN" : "MONEY OUT"} $${Math.abs(t.amount).toFixed(2)} | account: ${t.account}` +
      (feedback[t.txn_id] ? `\n   Your last answer was rejected: ${feedback[t.txn_id]}. Pick a different category.` : ""))
    .join("\n");
  return `You categorize bank and card transactions for a US freelancer's 2026 Schedule C (Form 1040).

Categories (direction in brackets: "in" = deposits only, "out" = payments only, "any" = either):
${cats}

Rules:
- Pick the single best category for each transaction. Never pick an "in" category for money out or vice versa.
- Deposits from clients, platforms or checks are usually "income"; unclear deposits get low confidence.
- Equipment that lasts more than a year (laptops, cameras, furniture) is "capital_purchase".
- Restaurants and coffee could be business meals or personal: use confidence below 0.7 unless the description makes it clear.
- confidence is 0 to 1. Use below 0.8 whenever a human should confirm.
- rationale: one short sentence.

${examples.length ? `This user's own answers for similar transactions. Follow them for the same merchant or kind of purchase:
${examples.map(describeExample).join("\n")}

` : ""}Transactions:
${items}

Return JSON {"results": [...]} with one entry per transaction, using its number as "i".`;
}

function schema(categoryKeys) {
  return JSON.stringify({
    type: "object",
    properties: {
      results: {
        type: "array",
        items: {
          type: "object",
          properties: {
            i: { type: "integer" },
            category: { type: "string", enum: categoryKeys },
            confidence: { type: "number" },
            rationale: { type: "string" },
          },
          required: ["i", "category", "confidence", "rationale"],
        },
      },
    },
    required: ["results"],
  });
}

async function ask(engine, taxonomy, responseFormat, batch, feedback, examples) {
  const reply = await engine.chat.completions.create({
    messages: [{ role: "user", content: buildPrompt(taxonomy, batch, feedback, examples) }],
    response_format: responseFormat,
    temperature: 0,
    max_tokens: 140 * batch.length,
  });
  let results = [];
  try {
    results = JSON.parse(reply.choices[0].message.content).results ?? [];
  } catch { /* treated as no answers, so everything is retried */ }
  return results
    .filter((r) => Number.isInteger(r.i) && batch[r.i])
    .map((r) => ({
      txn_id: batch[r.i].txn_id,
      category: r.category,
      confidence: Math.min(Math.max(Number(r.confidence) || 0, 0), 1),
      rationale: String(r.rationale ?? ""),
    }));
}

// status "uncategorized" for a normal run, "model_guesses" to redo the model's
// unconfirmed answers after the user has taught it something
export async function categorizeAll(engine, onLog, { batchSize = SORT_BATCH, status = "uncategorized" } = {}) {
  const taxonomy = await api.get("/api/taxonomy");
  const responseFormat = { type: "json_object", schema: schema(Object.keys(taxonomy)) };
  const pending = await api.get("/api/transactions", { status, limit: 2000 });
  let saved = 0, retried = 0, gaveUp = 0, changed = 0;
  const before = Object.fromEntries(pending.map((t) => [t.txn_id, t.category]));
  onLog(`Sending ${plural(pending.length, "transaction")} to the AI.`);

  for (let start = 0; start < pending.length; start += batchSize) {
    const n = start / batchSize + 1;
    let batch = pending.slice(start, start + batchSize);
    let feedback = {};
    const t0 = performance.now();
    const examples = await api.post("/api/examples", { descriptions: batch.map((t) => t.description), k: EXAMPLES });
    if (examples.length) onLog(`Batch ${n}: using ${plural(examples.length, "past answer")} as examples.`);

    for (let attempt = 1; attempt <= MAX_ATTEMPTS && batch.length; attempt++) {
      const items = await ask(engine, taxonomy, responseFormat, batch, feedback, examples);
      const res = items.length ? await api.post("/api/classifications", items) : { saved: 0, rejected: [] };
      saved += res.saved;
      const rejectedIds = new Set(res.rejected.map((r) => r.txn_id));
      changed += items.filter((i) => !rejectedIds.has(i.txn_id) && before[i.txn_id] && before[i.txn_id] !== i.category).length;

      const answered = new Set(items.map((i) => i.txn_id));
      feedback = Object.fromEntries(res.rejected.map((r) => [r.txn_id, r.reason]));
      for (const t of batch) if (!answered.has(t.txn_id)) feedback[t.txn_id] = "you didn't answer this one";
      batch = batch.filter((t) => feedback[t.txn_id]);

      const left = batch.length ? `, ${batch.length} sent back${attempt < MAX_ATTEMPTS ? " with the reason" : ""}` : "";
      onLog(`Batch ${n}, try ${attempt}: ${res.saved} saved${left}.`);
      if (batch.length && attempt < MAX_ATTEMPTS) retried += batch.length;
    }
    if (batch.length) {
      gaveUp += batch.length;
      onLog(`Batch ${n}: ${batch.length} still not right after ${MAX_ATTEMPTS} tries, so they're waiting for you under Review.`);
    }
    onLog(`Batch ${n} took ${((performance.now() - t0) / 1000).toFixed(1)}s on GPU.`);
  }
  if (status === "model_guesses") onLog(`Re-check finished. The AI changed ${plural(changed, "earlier answer")}.`);
  if (saved) onLog(`Saved ${plural(saved, "AI answer")}. You'll confirm each one before you sign off.`);
  return { saved, retried, gaveUp, changed };
}
