// Embeddings get computed here on the GPU, the API stores them and does the ranking.
// Until the index is built, search is keyword-only.
import { api } from "./api.js";
import { loadEngine } from "./llm.js";

export const EMBED_MODEL = "snowflake-arctic-embed-s-q0f32-MLC-b32"; // 384 dims, ~1GB
// arctic-embed wants this prefix on queries only
const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";
const BATCH = 32;
const MAX_CHARS = 1800; // model maxes out at 512 tokens

const normalize = (v) => {
  const n = Math.hypot(...v) || 1;
  return v.map((x) => x / n);
};

export class IrsSearch {
  constructor() {
    this.engine = null;
  }

  get ready() {
    return this.engine !== null;
  }

  async load(onProgress) {
    this.engine = await loadEngine(EMBED_MODEL, onProgress);
  }

  status() {
    return api.get("/api/irs/status", { model: EMBED_MODEL });
  }

  async embed(texts) {
    const res = await this.engine.embeddings.create({ input: texts.map((t) => t.slice(0, MAX_CHARS)) });
    return res.data.sort((a, b) => a.index - b.index).map((d) => normalize(Array.from(d.embedding)));
  }

  // only embeds what the server doesn't have yet, so it can resume
  async buildIndex(onProgress) {
    let s = await this.status();
    onProgress(s.embedded, s.chunks);
    for (;;) {
      const chunks = await api.get("/api/irs/chunks", { model: EMBED_MODEL, limit: 128 });
      if (!chunks.length) break;
      for (let i = 0; i < chunks.length; i += BATCH) {
        const batch = chunks.slice(i, i + BATCH);
        const vectors = await this.embed(batch.map((c) => c.text));
        s = await api.post("/api/irs/embeddings", {
          model: EMBED_MODEL, corpus_version: s.corpus_version,
          items: batch.map((c, j) => ({ id: c.id, vector: vectors[j] })),
        });
        onProgress(s.embedded, s.chunks);
      }
    }
    return s;
  }

  async withVector(args) {
    if (!this.ready) return args;
    const [vector] = await this.embed([QUERY_PREFIX + args.query]);
    return { ...args, vector, model: EMBED_MODEL };
  }

  async search(query, k = 5) {
    return api.post("/api/irs/search", await this.withVector({ query, k }));
  }
}
