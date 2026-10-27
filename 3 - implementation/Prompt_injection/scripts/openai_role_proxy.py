"""Tiny local proxy between OpenAI-style clients and Ollama's /v1 endpoint.

AgentDojo (and therefore CaMeL) sends the system prompt with role "developer";
Ollama silently drops that role, so the model never sees the system prompt. This
proxy rewrites "developer" → "system" and, when models.disable_thinking is set, adds
reasoning_effort="none" (Qwen3 thinking off, same as horizon.llm); everything else is unchanged.
It binds to localhost only.

    uv run python scripts/openai_role_proxy.py   # port from models.proxy_port
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import add_config_args, config_from_args  # noqa: E402


def make_handler(upstream: str, timeout: float, disable_thinking: bool, effort_off: str):
    class Handler(BaseHTTPRequestHandler):
        def _forward(self, body: bytes | None):
            req = urllib.request.Request(upstream + self.path, data=body, method=self.command,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data, status = r.read(), r.status
            except urllib.error.HTTPError as e:
                data, status = e.read(), e.code
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                payload = json.loads(body)
                if disable_thinking and "messages" in payload:
                    payload.setdefault("reasoning_effort", effort_off)
                for m in payload.get("messages", []):
                    if m.get("role") == "developer":
                        m["role"] = "system"
                body = json.dumps(payload).encode()
            except json.JSONDecodeError:
                pass
            self._forward(body)

        def do_GET(self):
            self._forward(None)

        def log_message(self, *a):
            pass

    return Handler


def main() -> None:
    args = add_config_args(argparse.ArgumentParser()).parse_args()
    cfg = config_from_args(args)
    port = cfg.models["proxy_port"]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg.models["api_base"], cfg.models["request_timeout_s"],
                                                                cfg.models["disable_thinking"], cfg.models["reasoning_effort_off"]))
    print(f"role proxy on http://127.0.0.1:{port} → {cfg.models['api_base']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
