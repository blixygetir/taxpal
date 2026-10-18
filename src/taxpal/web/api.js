async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
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
