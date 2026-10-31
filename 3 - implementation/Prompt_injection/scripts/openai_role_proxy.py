"""Tiny local proxy between OpenAI-style clients and Ollama's /v1 endpoint.

AgentDojo (and therefore CaMeL) sends the system prompt with role "developer";
Ollama silently drops that role, so the model never sees the system prompt. This
proxy rewrites "developer" → "system" and, when models.disable_thinking is set, adds
reasoning_effort="none" (Qwen3 thinking off, same as horizon.llm); everything else is unchanged.
It binds to localhost only.

Safety net for third-party clients (garak, PyRIT) that send NO token limit: a looping generation then runs for
10+ minutes and 100 KB (seen in the garak log) before Ollama aborts it with HTTP 500 ("token repeat limit reached"),
which garak did not survive. The proxy therefore
  * adds max_tokens = models.proxy_default_max_tokens when the request has none, and lowers any larger value to
    models.proxy_max_tokens_ceiling (the AgentDojo clients keep their own, smaller limits);
  * turns Ollama's "token repeat limit / prediction aborted" 500 into a normal 200 completion with EMPTY content
    (finish_reason "length"): the model produced nothing usable, which is what the client should see;
  * answers every other upstream failure (timeout, connection refused) with a JSON 502 instead of dropping the socket.

    uv run python scripts/openai_role_proxy.py   # port from models.proxy_port
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import add_config_args, config_from_args  # noqa: E402


LOOP_ERROR = re.compile(rb"token repeat limit|prediction aborted|repeat limit reached", re.IGNORECASE)


def empty_completion(model: str) -> bytes:
    """A valid chat.completion with no content: what a client should see when the model only looped."""
    return json.dumps({"id": f"hzn-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
                       "model": model,
                       "choices": [{"index": 0, "finish_reason": "length",
                                    "message": {"role": "assistant", "content": ""}}],
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}).encode()


def cap_tokens(payload: dict, default: int, ceiling: int) -> None:
    """max_tokens: default when absent, never above the ceiling (max_completion_tokens is the newer spelling)."""
    for key in ("max_tokens", "max_completion_tokens"):
        if payload.get(key):
            payload[key] = min(int(payload[key]), ceiling)
    if not payload.get("max_tokens") and not payload.get("max_completion_tokens"):
        payload["max_tokens"] = default


def make_handler(upstream: str, timeout: float, disable_thinking: bool, effort_off: str,
                 default_max_tokens: int = 2048, max_tokens_ceiling: int = 4096):
    class Handler(BaseHTTPRequestHandler):
        def _forward(self, body: bytes | None, model: str = "horizon"):
            req = urllib.request.Request(upstream + self.path, data=body, method=self.command,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data, status = r.read(), r.status
            except urllib.error.HTTPError as e:
                data, status = e.read(), e.code
                if status >= 500 and LOOP_ERROR.search(data) and self.command == "POST":
                    data, status = empty_completion(model), 200          # the model only looped: nothing usable
            except Exception as e:                                       # timeout, connection refused, ...
                data = json.dumps({"error": {"message": f"upstream: {type(e).__name__}: {e}"[:300],
                                             "type": "proxy_upstream_error"}}).encode()
                status = 502
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):              # the client gave up (its own timeout)
                pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            model = "horizon"
            try:
                payload = json.loads(body)
                model = str(payload.get("model", model))
                if disable_thinking and "messages" in payload:
                    payload.setdefault("reasoning_effort", effort_off)
                if "messages" in payload:
                    cap_tokens(payload, default_max_tokens, max_tokens_ceiling)
                for m in payload.get("messages", []):
                    if m.get("role") == "developer":
                        m["role"] = "system"
                body = json.dumps(payload).encode()
            except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
                pass
            self._forward(body, model)

        def do_GET(self):
            self._forward(None)

        def log_message(self, *a):
            pass

    return Handler


def main() -> None:
    args = add_config_args(argparse.ArgumentParser()).parse_args()
    cfg = config_from_args(args)
    port = cfg.models["proxy_port"]
    m = cfg.models
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(
        m["api_base"], m["request_timeout_s"], m["disable_thinking"], m["reasoning_effort_off"],
        int(m.get("proxy_default_max_tokens", 2048)), int(m.get("proxy_max_tokens_ceiling", 4096))))
    print(f"role proxy on http://127.0.0.1:{port} → {cfg.models['api_base']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
