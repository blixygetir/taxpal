// Agent loop: the model picks a tool, we call the API, feed the result back.
//
// Tool calling is done with JSON-schema output instead of WebLLM's `tools` option,
// so it works with a 3B model. Each step the model must answer with
//   {"tool": "<name>", "args": {...}}
// and the grammar only allows a real tool name with that tool's arguments. The API
// validates the arguments again. Results go back as user messages ("Result of x: ...")
// because not every chat template has a tool role.
//
// The model doesn't get to decide it's done: the first `finish` in a turn runs the
// pre-sign-off checks, and if any are open the result goes back to the model so it
// can ask the user about them. Sign-off itself has no tool; only the user can do it.
import { api } from "./api.js";
import { CONTEXT_TOKENS } from "./llm.js";

const MAX_STEPS = 8;
const MAX_OUTPUT_TOKENS = 300;
const MAX_RESULT_CHARS = 1200;
const CHARS_PER_TOKEN = 3.5; // rough, errs on the safe side for JSON

const INSTRUCTIONS = `You are TaxPal, a tax-prep assistant helping a US freelancer prepare their 2026 Schedule C (Form 1040).
Every reply is ONE tool call as JSON: {"tool": "<name>", "args": {...}}.

Rules:
- Use tools to read data. Never do arithmetic or state a dollar total that didn't come from a compute_* or reconcile tool.
- To get a fact from the user (category, business purpose, business-use %, gift recipient, profile values), use ask_user with ONE short, specific question that includes the transaction's date, description and amount.
- Only call record_decision, update_profile or log_mileage with facts the user actually stated.
- Typical flow: get_status, list_needs_review, ask_user about each item, record_decision, reconcile_income, compute_schedule_c, compute_se_and_qbi.
- When explaining a rule, call search_irs_guidance and cite the returned citation and url. Quoted IRS text may be a 2025 revision; for 2026 dollar amounts rely on the compute tools.
- This is not tax advice. Point flagged items to a tax professional.
- Before you finish, call run_checks. For each open blocker, ask the user about it; don't decide for them.
- You can't sign off. When no blockers are left, tell the user to review and sign off on the Results page (Check and sign off).
- When you have nothing else to do, use finish with a short plain-English summary.`;

const LOCAL_TOOLS = [
  { name: "ask_user", description: "Ask the user one question and wait for the answer.",
    parameters: { type: "object", properties: { question: { type: "string" } }, required: ["question"] } },
  { name: "finish", description: "End your turn with a message to the user.",
    parameters: { type: "object", properties: { message: { type: "string" } }, required: ["message"] } },
];

// short form, to save context: "- name(arg: type, opt?: type): description"
function describeTool(t) {
  const props = t.parameters?.properties ?? {};
  const required = new Set(t.parameters?.required ?? []);
  const args = Object.entries(props)
    .map(([k, v]) => `${k}${required.has(k) ? "" : "?"}: ${k === "category" ? "category" : v.type}`)
    .join(", ");
  return `- ${t.name}(${args}): ${t.description}`;
}

// one branch per tool, so the grammar ties each tool name to its own arguments
function stepSchema(tools) {
  return JSON.stringify({
    anyOf: tools.map((t) => ({
      type: "object",
      properties: { tool: { type: "string", enum: [t.name] }, args: t.parameters },
      required: ["tool", "args"],
    })),
  });
}

// looser fallback in case a grammar engine can't compile the anyOf version
function simpleSchema(tools) {
  return JSON.stringify({
    type: "object",
    properties: { tool: { type: "string", enum: tools.map((t) => t.name) }, args: { type: "object" } },
    required: ["tool", "args"],
  });
}

// the context is small, strip the big stuff
function compact(name, result) {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const { excluded, source, capital_purchases, corpus_version, fingerprint, ...rest } = result;
    if (excluded) rest.excluded_count = excluded.length;
    if (capital_purchases) rest.capital_purchases_count = capital_purchases.length;
    if (name === "run_checks" && rest.checks) {
      rest.checks = rest.checks.map(({ id, severity, message, can_acknowledge, acknowledged }) =>
        ({ id, severity, message, can_acknowledge, explained: Boolean(acknowledged) }));
    }
    result = rest;
  }
  const text = JSON.stringify(result);
  return text.length > MAX_RESULT_CHARS ? text.slice(0, MAX_RESULT_CHARS) + "...(truncated)" : text;
}

export class Harness {
  constructor(engine, { onEvent, confirm, augment = {} }) {
    this.engine = engine;
    this.augment = augment; // per-tool arg rewriting, used to attach the query embedding
    this.onEvent = onEvent;
    this.confirm = confirm;
    this.messages = []; // without the system message
  }

  async init() {
    const specs = await api.get("/api/tools");
    this.http = Object.fromEntries(specs.map((s) => [s.function.name, s]));
    this.tools = [...specs.map((s) => s.function), ...LOCAL_TOOLS];
    const categories = this.tools.find((t) => t.name === "record_decision").parameters.properties.category.enum;
    this.system = `${INSTRUCTIONS}\n\nTools (? = optional):\n${this.tools.map(describeTool).join("\n")}\n\n` +
      `Categories: ${categories.join(", ")}`;
    this.format = { type: "json_object", schema: stepSchema(this.tools) };
    this.fallbackFormat = { type: "json_object", schema: simpleSchema(this.tools) };
  }

  needsConfirm(spec, args) {
    const c = spec["x-confirm"];
    return c === true || (typeof c === "object" && c !== null && Boolean(args[c.when]));
  }

  async callApi(name, args) {
    const spec = this.http[name];
    if (!spec) throw new Error(`Unknown tool ${name}`);
    const { method, query } = spec["x-http"];
    if (this.needsConfirm(spec, args) && !(await this.confirm(name, args))) return { cancelled: "The user declined this action." };
    // fill {params} in the path from the args
    args = { ...args };
    const path = spec["x-http"].path.replace(/\{(\w+)\}/g, (_, key) => {
      const v = args[key];
      delete args[key];
      if (v === undefined || v === null || v === "") throw new Error(`Missing ${key}`);
      return encodeURIComponent(v);
    });
    if (this.augment[name]) args = await this.augment[name](args);
    if (name === "search_irs_guidance") args.k ??= 3;
    if (method === "GET") {
      const q = { ...(query || {}), ...Object.fromEntries(Object.entries(args).filter(([, v]) => v !== undefined && v !== null)) };
      if (name === "list_needs_review") q.limit ??= 5;
      return api.get(path, Object.keys(q).length ? q : undefined);
    }
    return method === "PUT" ? api.put(path, args) : api.post(path, args);
  }

  // newest messages that fit the context, always starting with a user message
  trimmed() {
    const budget = (CONTEXT_TOKENS - MAX_OUTPUT_TOKENS) * CHARS_PER_TOKEN - this.system.length;
    const out = [];
    let used = 0;
    for (let i = this.messages.length - 1; i >= 0; i--) {
      used += this.messages[i].content.length;
      if (used > budget && out.length) break;
      out.unshift(this.messages[i]);
    }
    while (out.length > 1 && out[0].role !== "user") out.shift();
    return [{ role: "system", content: this.system }, ...out];
  }

  async nextStep() {
    const request = { messages: this.trimmed(), temperature: 0.1, max_tokens: MAX_OUTPUT_TOKENS };
    try {
      return await this.engine.chat.completions.create({ ...request, response_format: this.format });
    } catch (err) {
      if (this.format === this.fallbackFormat) throw err;
      this.format = this.fallbackFormat; // grammar didn't compile; try the loose one from now on
      return this.engine.chat.completions.create({ ...request, response_format: this.format });
    }
  }

  // Called when the model wants to finish. Returns a message to send back to the
  // model if there's still something open, or null to let it finish.
  async gateFinish() {
    let result;
    try {
      result = await api.get("/api/checks");
    } catch {
      return null; // don't trap the user in a loop because the check endpoint failed
    }
    this.onEvent("checks", result);
    if (result.ok) return null;
    const open = result.checks
      .filter((c) => c.severity === "blocker" && !c.acknowledged)
      .map((c) => ({ id: c.id, message: c.message, can_acknowledge: c.can_acknowledge }));
    return JSON.stringify({
      not_done: "These checks are still open. Help the user resolve them (ask, record their answers, or save their explanation), or finish and tell them exactly what's left.",
      open_blockers: open,
    });
  }

  // runs until the model asks something, finishes, or hits MAX_STEPS
  async send(userText) {
    this.messages.push({ role: "user", content: userText });
    let gated = false; // only push back once per turn, then let it finish

    for (let step = 0; step < MAX_STEPS; step++) {
      let reply;
      try {
        reply = await this.nextStep();
      } catch (err) {
        this.onEvent("error", `Model error: ${err.message}`);
        return;
      }
      const text = reply.choices[0].message.content ?? "";
      let call;
      try {
        call = JSON.parse(text);
      } catch {
        call = null;
      }
      if (!call?.tool || !this.tools.some((t) => t.name === call.tool)) {
        this.messages.push({ role: "assistant", content: text.slice(0, 500) });
        this.messages.push({ role: "user", content: 'That wasn\'t a valid tool call. Reply with {"tool": "<name>", "args": {...}} using a tool from the list.' });
        continue;
      }
      const { tool: name } = call;
      const args = call.args && typeof call.args === "object" ? call.args : {};
      this.messages.push({ role: "assistant", content: JSON.stringify({ tool: name, args }) });

      if (name === "ask_user") { this.onEvent("ask", args.question); return; }
      if (name === "finish") {
        const pushBack = gated ? null : await this.gateFinish();
        gated = true;
        if (!pushBack) { this.onEvent("say", args.message); return; }
        this.onEvent("tool_call", { name: "run_checks", args: {} });
        this.messages.push({ role: "user", content: `Not finished yet. Result of run_checks: ${pushBack}` });
        continue;
      }

      this.onEvent("tool_call", { name, args });
      let result;
      try {
        result = await this.callApi(name, args);
      } catch (err) {
        result = { error: err.message };
      }
      this.onEvent("tool_result", { name, result });
      this.messages.push({ role: "user", content: `Result of ${name}: ${compact(name, result)}` });
    }
    this.onEvent("say", "I've hit my step limit for this turn. Say “continue” to keep going.");
  }
}
