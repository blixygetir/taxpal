// Served by `uv run taxpal`, requests go to the local server. The hosted build (see
// scripts/build_site.py) marks the page with <meta name="taxpal-backend" content="browser">
// and runs the same API in a web worker instead.
const IN_BROWSER = document.querySelector('meta[name="taxpal-backend"]')?.content === "browser";

let worker, nextId = 0;
const pending = new Map();
const workerReady = IN_BROWSER ? startWorker() : null;

function startWorker() {
  const banner = Object.assign(document.createElement("div"), {
    className: "boot-banner", role: "status",
    textContent: "Starting TaxPal in your browser… (first visit downloads ~30 MB; your data never leaves this device)",
  });
  document.body.append(banner);
  worker = new Worker(new URL("backend-worker.js", import.meta.url), { type: "module" });
  return new Promise((resolve, reject) => {
    worker.onmessage = ({ data }) => {
      if ("ready" in data) {
        if (data.ready) { banner.remove(); resolve(); }
        else { banner.textContent = `TaxPal couldn't start: ${data.error}`; banner.classList.add("error"); reject(new Error(data.error)); }
        return;
      }
      const p = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) p.reject(new Error(data.error));
      else p.resolve(new Response(data.body.byteLength ? data.body : null, { status: data.status, headers: data.headers }));
    };
  });
}

async function workerFetch(url, opts) {
  await workerReady;
  const req = new Request(new URL(url, location.href), opts);
  const { pathname, search } = new URL(req.url);
  const body = await req.arrayBuffer();
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, method: req.method, path: pathname, query: search.slice(1),
                         headers: [...req.headers], body }, [body]);
  });
}

const send = IN_BROWSER ? workerFetch : fetch;

if (IN_BROWSER) {
  // export links (<a href="/api/export/..." download>) have no server to hit; answer them from the worker
  document.addEventListener("click", async (e) => {
    const a = e.target.closest?.("a[href^='/api/']");
    if (!a) return;
    e.preventDefault();
    const res = await send(a.getAttribute("href"));
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "taxpal-export";
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await res.blob()), download: name });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
  });
}

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await send(path, opts);
  const text = await res.text();
  const data = text && res.headers.get("content-type")?.includes("json") ? JSON.parse(text) : text;
  if (!res.ok) throw new Error(typeof data === "object" ? JSON.stringify(data.detail ?? data) : data);
  return data;
}

export const api = {
  get: (path, query) => request("GET", query ? `${path}?${new URLSearchParams(query)}` : path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  upload: (path, file, fields = {}) => {
    const form = new FormData();
    form.append("file", file);
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    return request("POST", path, form);
  },
};
