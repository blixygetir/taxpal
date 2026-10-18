import { CreateWebWorkerMLCEngine } from "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.85/+esm";

// One model does everything: sorting transactions and running the agent. Tool calls
// go through JSON-schema output (see harness.js), so it doesn't need WebLLM's built-in
// function calling, which only works with the 7-8B Hermes models.
// Pick the f32 build on GPUs without shader-f16.
export const MODELS = [
  { id: "Hermes-3-Llama-3.2-3B-q4f16_1-MLC", label: "Hermes 3 (Llama 3.2, 3B): about 2.3 GB of GPU memory", f16: true },
  { id: "Hermes-3-Llama-3.2-3B-q4f32_1-MLC", label: "Hermes 3 (Llama 3.2, 3B), for older GPUs: about 3 GB of GPU memory", f16: false },
];

// context the model is loaded with (WebLLM's default for it); harness.js trims history to fit
export const CONTEXT_TOKENS = 4096;
// transactions per sorting prompt; the category list alone is ~1k tokens
export const SORT_BATCH = 6;

export async function detectWebGPU() {
  if (!("gpu" in navigator)) return { ok: false, reason: "This browser doesn't support WebGPU. Try a recent version of Chrome or Edge." };
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) return { ok: false, reason: "WebGPU is available but no GPU adapter was found." };
  return { ok: true, f16: adapter.features.has("shader-f16") };
}

// separate workers so the optional IRS embedding model can load on its own
export async function loadEngine(modelId, onProgress) {
  const worker = new Worker(new URL("./llm-worker.js", import.meta.url), { type: "module" });
  return CreateWebWorkerMLCEngine(worker, modelId, { initProgressCallback: onProgress });
}
