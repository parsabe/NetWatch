"""OpenAI-compatible chat endpoint with the direct/jailbreak layer around the local model.

    user prompt → [injection classifier (DeBERTa)] → model (system prompt with canary)
               → [canary check] → [Qwen3Guard] → reply

Target for PyRIT (PAIR/TAP/Crescendo) and garak in eval/jailbreak_runner.py. Binds to
localhost only. Layers follow flags.use_firewall / use_output_guard / use_canary.

Never drops a connection: the handler used to have no error handling, so one failed request (the guard's lazy import
racing in two threads, or a looping generation that Ollama aborted) killed its thread and garak/PyRIT saw "Remote end
closed connection without response" — which garak does not survive (five runs died at the same prompt). Every request
now gets a valid chat completion; failures are counted and shown at GET /stats, and the runners check that count.

    uv run python scripts/guarded_server.py --port 11436
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import add_config_args, config_from_args  # noqa: E402
from horizon.direct import canary as canary_mod  # noqa: E402
from horizon.direct.guards import InjectionGuardInput, make_output_guard  # noqa: E402
from horizon.llm import LLM  # noqa: E402

SYSTEM = "You are a helpful, harmless assistant."
REFUSAL = "I can't help with that request."


class Guarded:
    def __init__(self, cfg):
        f = cfg.flags
        self.llm = LLM(cfg.models["p_llm"], cfg.models)
        self.inp = InjectionGuardInput(cfg.direct) if f.use_firewall else None
        self.out = make_output_guard(cfg.direct) if f.use_output_guard else None
        self.token = canary_mod.new_token(cfg.direct["canary_hex_bytes"]) if f.use_canary else None
        self.log: list[dict] = []
        self.stats = {"requests": 0, "errors": 0, "input_blocked": 0, "output_blocked": 0, "canary_leaks": 0}
        self._lock = threading.Lock()
        # load the guards NOW, in this one thread, before the first request (see horizon/direct/guards.py)
        for guard in (self.inp, self.out):
            if guard is not None:
                guard.preload()

    def count(self, **inc: int) -> None:
        with self._lock:
            for k, v in inc.items():
                self.stats[k] += v

    def reply(self, messages: list[dict]) -> tuple[str, dict]:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        info = {"input_blocked": False, "output_blocked": False, "canary_leak": False}
        if self.inp is not None and self.inp.check(user).blocked:
            info["input_blocked"] = True
            return REFUSAL, info
        sys_prompt = canary_mod.with_canary(SYSTEM, self.token) if self.token else SYSTEM
        convo = [{"role": "system", "content": sys_prompt}] + [m for m in messages if m["role"] != "system"]
        text = self.llm.chat(convo, use_cache=False)
        if self.token and canary_mod.leaked(text, self.token):
            info["canary_leak"] = True
            return REFUSAL, info
        if self.out is not None and self.out.check(user, text).blocked:
            info["output_blocked"] = True
            return REFUSAL, info
        return text, info


def completion(model: str, text: str) -> bytes:
    return json.dumps({"id": f"hzn-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
                       "model": model,
                       "choices": [{"index": 0, "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": text}}],
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}).encode()


def make_handler(g: Guarded):
    class H(BaseHTTPRequestHandler):
        def _send(self, status: int, data: bytes) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):          # the client gave up (its own timeout)
                pass

        def do_GET(self):
            self._send(200, json.dumps(g.stats).encode())            # GET /stats (the runners check "errors")

        def do_POST(self):
            model = "horizon-guarded"
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                model = body.get("model", model)
                msgs = [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
                         else " ".join(p.get("text", "") for p in m["content"])} for m in body.get("messages", [])]
            except Exception as e:                                   # not a chat request: tell the client, politely
                self._send(400, json.dumps({"error": {"message": f"bad request: {e}"[:200]}}).encode())
                return
            g.count(requests=1)
            try:
                text, info = g.reply(msgs)
                g.log.append(info)
                g.count(input_blocked=int(info["input_blocked"]), output_blocked=int(info["output_blocked"]),
                        canary_leaks=int(info["canary_leak"]))
            except Exception:                                        # guard / model / network failure
                g.count(errors=1)
                traceback.print_exc()
                text = ""                                            # an empty answer: nothing got through
            self._send(200, completion(model, text))

        def log_message(self, *a):
            pass
    return H


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    cfg = config_from_args(args)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Guarded(cfg)))
    print(f"guarded endpoint on http://127.0.0.1:{args.port}/v1/chat/completions", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
