// The API, run in the browser with Pyodide for the hosted site. Same Python code as
// `uv run taxpal`; the SQLite file lives in IndexedDB, so data stays on this device.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PURE_WHEELS = ["python-multipart==0.0.32", "pypdf==6.20.1"];  // not in the Pyodide distribution

async function boot() {
  const py = await loadPyodide();
  await py.loadPackage(["fastapi", "pyyaml", "micropip"]);
  await py.pyimport("micropip").install(PURE_WHEELS);
  const code = await fetch(new URL("taxpal-py.zip", import.meta.url), { cache: "no-cache" });
  if (!code.ok) throw new Error(`Couldn't load taxpal-py.zip (${code.status})`);
  py.unpackArchive(await code.arrayBuffer(), "zip", { extractDir: "/app" });
  py.FS.mkdirTree("/data");
  py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
  await syncfs(py, true);
  py.runPython(`import os, sys; os.environ["TAXPAL_DB"] = "/data/taxpal.db"; sys.path.insert(0, "/app/src")`);
  return { py, backend: py.pyimport("taxpal.browser") };
}

const syncfs = (py, populate) => new Promise((resolve, reject) =>
  py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve())));

const ready = boot();
let saving = Promise.resolve();
ready.then(() => postMessage({ ready: true }), (err) => postMessage({ ready: false, error: String(err) }));

onmessage = async ({ data: { id, method, path, query, headers, body } }) => {
  try {
    const { py, backend } = await ready;
    const res = await backend.handle(method, path, query, py.toPy(headers), new Uint8Array(body));
    const out = res.toJs({ dict_converter: Object.fromEntries });
    res.destroy();
    if (method !== "GET") {
      saving = saving.then(() => syncfs(py, false));
      await saving;
    }
    const bytes = out.body.slice().buffer;
    postMessage({ id, status: out.status, headers: out.headers, body: bytes }, [bytes]);
  } catch (err) {
    postMessage({ id, error: String(err?.message ?? err) });
  }
};
