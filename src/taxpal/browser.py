"""Runs the API inside the browser (Pyodide), so the website needs no server.

The page's web worker calls handle() with each request and gets the response back;
nothing goes over the network. Pyodide has no threads, so FastAPI's threadpool for
sync endpoints is replaced with a direct call.
"""
import anyio.to_thread


async def _run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
    return func(*args)


anyio.to_thread.run_sync = _run_sync

from . import db  # noqa: E402
from .api import app  # noqa: E402

db.init()


async def handle(method: str, path: str, query: str, headers: list, body: bytes) -> dict:
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": ("127.0.0.1", 0), "server": ("taxpal", 443),
    }
    sent = False
    response = {"status": 500, "headers": [], "body": bytearray()}

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = [(k.decode(), v.decode()) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    await app(scope, receive, send)
    response["body"] = bytes(response["body"])
    return response
