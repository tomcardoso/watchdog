"""A stand-in for the Anthropic Messages API, for tests that run the bundled Claude Code CLI for
real (tests/test_session_live.py): no network and no account.

Each conversation's next reply is scripted by how many tool results it already holds: with fewer
than `len(script)`, the reply is the next `[tool name, input]` as a `tool_use`; after that, the text
"done". Every request body is kept in `bodies`, so a test can see which tools the CLI offered."""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockAPI:
    def __init__(self, script: list | None = None):
        self.script = list(script or [])
        self.bodies: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def tool_results(self) -> list[dict]:
        """Every tool_result block the CLI sent back, in order (the last request holds them all)."""
        out = []
        for body in self.bodies:
            for m in body.get("messages") or []:
                for c in m.get("content") if isinstance(m.get("content"), list) else []:
                    if c.get("type") == "tool_result" and c not in out:
                        out.append(c)
        return out

    def offered_tools(self) -> set[str]:
        """The tool names the main conversation was offered."""
        names: set[str] = set()
        for body in self.bodies:
            names |= {t.get("name") for t in body.get("tools") or []}
        return names

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._json({})

            def do_HEAD(self):
                self._json({})

            def _json(self, data):
                out = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
                if self.headers.get("content-encoding") == "gzip":
                    raw = gzip.decompress(raw)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
                    return self._json({"input_tokens": 10})
                api.bodies.append(body)
                results = sum(1 for m in body.get("messages") or []
                              for c in (m.get("content") if isinstance(m.get("content"), list) else [])
                              if c.get("type") == "tool_result")
                if results < len(api.script) and body.get("tools"):
                    name, inp = api.script[results]
                    blocks, stop = [{"type": "tool_use", "id": f"toolu_{results}", "name": name, "input": inp}], "tool_use"
                else:
                    blocks, stop = [{"type": "text", "text": "done"}], "end_turn"
                msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model", "m"),
                       "content": [], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 10, "output_tokens": 1}}
                if not body.get("stream"):
                    return self._json({**msg, "content": blocks, "stop_reason": stop})
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()

                def event(kind, data):
                    self.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode())

                event("message_start", {"message": msg})
                for i, b in enumerate(blocks):
                    if b["type"] == "text":
                        event("content_block_start", {"index": i, "content_block": {"type": "text", "text": ""}})
                        event("content_block_delta", {"index": i, "delta": {"type": "text_delta", "text": b["text"]}})
                    else:
                        event("content_block_start", {"index": i, "content_block": {
                            "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
                        event("content_block_delta", {"index": i, "delta": {
                            "type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
                    event("content_block_stop", {"index": i})
                event("message_delta", {"delta": {"stop_reason": stop, "stop_sequence": None},
                                        "usage": {"output_tokens": 5}})
                event("message_stop", {})
                self.wfile.flush()

        return Handler
