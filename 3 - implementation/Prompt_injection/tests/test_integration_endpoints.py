"""Integration: every OpenAI-style client of the campaign reaches exactly /v1/chat/completions.

A fake OpenAI server records request paths; the role proxy, PyRIT's targets and garak's REST config are pointed at
it the same way the campaign does. This is the check that would have caught PyRIT's …/chat/completions/chat/
completions (404) before a cluster job ran.
"""
import asyncio
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from horizon.config import ROOT, load_config

sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "scripts"))

REPLY = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.fixture()
def fake_openai():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            seen.append((self.path, body))
            ok = self.path == "/v1/chat/completions"
            data = json.dumps(REPLY if ok else {"error": "404 page not found"}).encode()
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def _serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_role_proxy_forwards_path_and_rewrites_role(fake_openai):
    from openai_role_proxy import make_handler
    url, seen = fake_openai
    proxy = _serve(make_handler(url, 10, True, "none"))
    req = urllib.request.Request(f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
                                 data=json.dumps({"model": "m", "messages": [{"role": "developer", "content": "s"}]})
                                 .encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
    proxy.shutdown()
    path, body = seen[-1]
    assert path == "/v1/chat/completions"
    assert body["messages"][0]["role"] == "system" and body["reasoning_effort"] == "none"


def test_pyrit_targets_hit_chat_completions(fake_openai):
    pytest.importorskip("pyrit")
    from jailbreak_runner import pyrit_targets
    from pyrit.models import Message
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async
    url, seen = fake_openai
    port = int(url.rsplit(":", 1)[1])
    cfg = load_config(None, [f"models.proxy_port={port}"])

    async def go():
        await initialize_pyrit_async(memory_db_type=IN_MEMORY)
        targets = pyrit_targets(cfg, f"{url}/v1/chat/completions")
        for t in targets:
            msg = Message.from_prompt(prompt="hi", role="user")
            await t.send_prompt_async(message=msg)
    asyncio.run(go())
    assert [p for p, _ in seen] == ["/v1/chat/completions"] * 3


def test_garak_rest_config_has_timeout_and_path(tmp_path, monkeypatch):
    garak_rest = pytest.importorskip("garak.generators.rest")
    import jailbreak_runner as J
    monkeypatch.setattr(J, "GARAK_DIR", Path(tmp_path))
    cfg = load_config()
    p = J.garak_config(cfg, "http://127.0.0.1:1/v1/chat/completions", "m", "bare")
    gen = json.loads(p.read_text())["rest"]["RestGenerator"]
    assert gen["uri"].endswith("/v1/chat/completions")
    assert gen["request_timeout"] == cfg.models["request_timeout_s"]
    assert "request_timeout" in garak_rest.RestGenerator.DEFAULT_PARAMS
