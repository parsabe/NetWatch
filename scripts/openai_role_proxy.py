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

Structured output (PyRIT): PyRIT ≥ 1.1 asks for JSON with `response_format = {"type": "json_schema", ...}` — the
adversarial chat (PAIR / TAP / Crescendo need next_message, rationale, last_response_summary) and every self-ask scorer
(score_value, description, rationale). Ollama's OpenAI endpoint did not hold mistral:7b to that schema: in all four
1–2 Nov pair runs it left keys out or answered with a copy of the schema itself, every attack hit PyRIT's JSON retries,
and the pyrit phase finished 1 test in 4 × 25 min. Such requests now go to Ollama's NATIVE /api/chat with
`format = <schema>` (grammar-constrained decoding: the answer IS valid against the schema), `json_object` with
`format = "json"`; the reply is turned back into an OpenAI chat.completion. Requests without response_format are
forwarded unchanged.

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
from http import HTTPStatus
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


def _text(content) -> str:
    """OpenAI message content (a string or a list of parts) → plain text for Ollama's native API."""
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type", "text") == "text")
    return "" if content is None else str(content)


def wants_native_json(payload: dict) -> bool:
    rf = payload.get("response_format")
    return isinstance(rf, dict) and rf.get("type") in ("json_schema", "json_object") and "messages" in payload


def to_native(payload: dict, think_off: bool) -> dict:
    """OpenAI chat request with response_format → Ollama /api/chat request with `format` (schema-constrained)."""
    rf = payload["response_format"]
    schema = (rf.get("json_schema") or {}).get("schema") if rf.get("type") == "json_schema" else None
    opts = {"temperature": payload.get("temperature"), "top_p": payload.get("top_p"), "seed": payload.get("seed"),
            "num_predict": payload.get("max_tokens") or payload.get("max_completion_tokens")}
    if payload.get("frequency_penalty"):
        opts["repeat_penalty"] = 1.0 + float(payload["frequency_penalty"]) / 2      # OpenAI [-2, 2] → Ollama ≥ 1
    native = {"model": payload["model"], "stream": False, "format": schema or "json",
              "messages": [{"role": "system" if m.get("role") == "developer" else m.get("role", "user"),
                            "content": _text(m.get("content"))} for m in payload["messages"]],
              "options": {k: v for k, v in opts.items() if v is not None}}
    if think_off:
        native["think"] = False
    return native


def from_native(data: bytes, model: str) -> bytes:
    """Ollama /api/chat reply → OpenAI chat.completion."""
    r = json.loads(data)
    msg = r.get("message") or {}
    return json.dumps({"id": f"hzn-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
                       "model": r.get("model", model),
                       "choices": [{"index": 0, "finish_reason": "length" if r.get("done_reason") == "length" else "stop",
                                    "message": {"role": "assistant", "content": msg.get("content", "")}}],
                       "usage": {"prompt_tokens": r.get("prompt_eval_count", 0),
                                 "completion_tokens": r.get("eval_count", 0),
                                 "total_tokens": r.get("prompt_eval_count", 0) + r.get("eval_count", 0)}}).encode()


def make_handler(upstream: str, timeout: float, disable_thinking: bool, effort_off: str,
                 default_max_tokens: int = 2048, max_tokens_ceiling: int = 4096,
                 thinking_models: tuple[str, ...] = ("qwen3",), routes: dict | None = None):
    routes = routes or {}                     # model name → its own Ollama server (models.model_ports)

    class Handler(BaseHTTPRequestHandler):
        def _forward(self, body: bytes | None, model: str = "horizon", native: bool = False):
            path = "/api/chat" if native else self.path
            req = urllib.request.Request(routes.get(model, upstream) + path, data=body, method=self.command,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data, status = r.read(), r.status
                if native and status == 200:
                    data = from_native(data, model)
            except urllib.error.HTTPError as e:
                data, status = e.read(), e.code
                if status >= HTTPStatus.INTERNAL_SERVER_ERROR and LOOP_ERROR.search(data) and self.command == "POST":
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
            native = False
            try:
                payload = json.loads(body)
                model = str(payload.get("model", model))
                if disable_thinking and "messages" in payload and any(t in model.lower() for t in thinking_models):
                    payload.setdefault("reasoning_effort", effort_off)
                if "messages" in payload:
                    cap_tokens(payload, default_max_tokens, max_tokens_ceiling)
                for m in payload.get("messages", []):
                    if m.get("role") == "developer":
                        m["role"] = "system"
                if wants_native_json(payload):        # schema-constrained JSON (see the module docstring)
                    think_off = disable_thinking and any(t in model.lower() for t in thinking_models)
                    payload, native = to_native(payload, think_off), True
                body = json.dumps(payload).encode()
            except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
                pass
            self._forward(body, model, native)

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
        int(m.get("proxy_default_max_tokens", 2048)), int(m.get("proxy_max_tokens_ceiling", 4096)),
        tuple(m.get("thinking_models", ["qwen3"])),
        {name: f"http://127.0.0.1:{port}" for name, port in (m.get("model_ports") or {}).items()}))
    print(f"role proxy on http://127.0.0.1:{port} → {cfg.models['api_base']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
