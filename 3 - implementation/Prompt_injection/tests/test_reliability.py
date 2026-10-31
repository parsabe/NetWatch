"""Reliability fixes, offline: the failures that sank the real campaign must not be able to happen again.

* Ollama aborts a looping generation ("token repeat limit reached"); the client retried the identical request and then
  raised, which killed autotune, XPIA and (through the guarded endpoint) five garak runs at the very same prompt.
* The role proxy and the guarded endpoint dropped the connection on a failed request.
* MLflow's SQLite migration raced between workers and failed phases that had nothing wrong with them.
"""
import json
import sys
import threading
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from horizon.config import ROOT, load_config
from horizon.llm import LLM

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "eval"))

CFG = load_config()
LOOP = RuntimeError('litellm.InternalServerError: Ollama_chatException - '
                    '{"error":"prediction aborted, token repeat limit reached"}')
MSG = [{"role": "user", "content": "x"}]


def fake_litellm(monkeypatch, behaviours):
    """A litellm whose k-th call raises or returns behaviours[k] (the last one repeats)."""
    calls = []

    def completion(**kw):
        calls.append(kw)
        b = behaviours[min(len(calls) - 1, len(behaviours) - 1)]
        if isinstance(b, Exception):
            raise b
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=b))])

    mod = types.ModuleType("litellm")
    mod.completion, mod.suppress_debug_info = completion, False
    monkeypatch.setitem(sys.modules, "litellm", mod)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def llm(tmp_path, **over):
    return LLM("ollama_chat/qwen3:8b", {**CFG.models, "cache_dir": str(tmp_path), **over})


# ------------------------------------------------------------------------------------------- LLM client
def test_a_looping_generation_is_retried_with_a_different_sample(tmp_path, monkeypatch):
    calls = fake_litellm(monkeypatch, [LOOP, "the answer"])
    m = llm(tmp_path)
    assert m.chat(MSG) == "the answer" and m.degenerate == 0
    first, second = calls
    assert "frequency_penalty" not in first                       # the first try is the normal request ...
    assert second["temperature"] > first["temperature"]           # ... the retry samples, penalises repeats, stops sooner
    assert second["frequency_penalty"] > 1.0 and second["max_tokens"] < first["max_tokens"]


def test_every_sample_looping_gives_an_empty_answer_not_an_exception(tmp_path, monkeypatch):
    calls = fake_litellm(monkeypatch, [LOOP])
    m = llm(tmp_path)
    assert m.chat(MSG) == "" and m.degenerate == 1 and len(calls) == CFG.models["llm_attempts"]
    assert list(tmp_path.rglob("*.json")) == []                   # never cached: a later run may do better
    with pytest.raises(RuntimeError, match="after retries"):
        llm(tmp_path, degenerate_policy="raise").chat(MSG)


def test_a_real_outage_still_raises(tmp_path, monkeypatch):
    calls = fake_litellm(monkeypatch, [ConnectionError("Ollama is down")])
    with pytest.raises(RuntimeError, match="Ollama is down"):
        llm(tmp_path).chat(MSG)
    assert len(calls) == CFG.models["llm_attempts"]


# ------------------------------------------------------------------------------------------ role proxy
def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def post(url, body: dict | bytes):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_proxy_token_limits():
    from openai_role_proxy import cap_tokens
    for payload, expect in (({}, ("max_tokens", 2048)), ({"max_tokens": 99999}, ("max_tokens", 4096)),
                            ({"max_tokens": 300}, ("max_tokens", 300)),
                            ({"max_completion_tokens": 50}, ("max_completion_tokens", 50))):
        cap_tokens(payload, 2048, 4096)
        assert payload[expect[0]] == expect[1]
    p = {"max_completion_tokens": 50}
    cap_tokens(p, 2048, 4096)
    assert "max_tokens" not in p                                   # the client's own limit is respected


def test_proxy_turns_a_loop_abort_into_an_empty_completion_and_adds_a_token_limit():
    from openai_role_proxy import make_handler
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            data = b'{"error":{"message":"prediction aborted, token repeat limit reached"}}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    up, up_url = serve(Upstream)
    proxy, url = serve(make_handler(up_url, 10, True, "none", 2048, 4096))
    status, body = post(f"{url}/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    up.shutdown()
    proxy.shutdown()
    assert status == 200 and body["choices"][0]["message"]["content"] == ""
    assert seen[0]["max_tokens"] == 2048                           # garak / PyRIT send none: the proxy adds one


def test_proxy_answers_when_the_model_server_is_down():
    from openai_role_proxy import make_handler
    proxy, url = serve(make_handler("http://127.0.0.1:1", 2, True, "none"))      # nothing listens on port 1
    status, body = post(f"{url}/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
    proxy.shutdown()
    assert status == 502 and "upstream" in body["error"]["message"]    # a JSON error, not a dropped socket


# ------------------------------------------------------------------------------------ guarded endpoint
class FakeGuarded:
    def __init__(self, fail: bool):
        self.fail, self.log = fail, []
        self.stats = {"requests": 0, "errors": 0, "input_blocked": 0, "output_blocked": 0, "canary_leaks": 0}

    def count(self, **inc):
        for k, v in inc.items():
            self.stats[k] += v

    def reply(self, messages):
        if self.fail:
            raise RuntimeError("guard model could not be loaded")
        return "hi", {"input_blocked": False, "output_blocked": False, "canary_leak": False}


def test_guarded_endpoint_always_answers_and_counts_its_errors():
    """The handler used to have no error handling: a failure closed the socket and garak died on 'Remote end closed
    connection without response' — five runs in a row, at the same prompt."""
    import guarded_server as G
    chat = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    for fail, text in ((True, ""), (False, "hi")):
        g = FakeGuarded(fail)
        srv, url = serve(G.make_handler(g))
        status, body = post(f"{url}/v1/chat/completions", chat)
        with urllib.request.urlopen(f"{url}/stats", timeout=10) as r:
            stats = json.loads(r.read())
        bad_status, _ = post(f"{url}/v1/chat/completions", b"not json")
        srv.shutdown()
        assert status == 200 and body["choices"][0]["message"]["content"] == text
        assert stats["requests"] == 1 and stats["errors"] == int(fail)
        assert bad_status == 400


# ------------------------------------------------------------------------------------------- garak + MLflow
def test_garak_probe_is_finished_only_with_a_completion_entry(tmp_path):
    import jailbreak_runner as J
    done, partial = tmp_path / "a.report.jsonl", tmp_path / "b.report.jsonl"
    done.write_text('{"entry_type": "attempt"}\n{"entry_type": "completion"}\n')
    partial.write_text('{"entry_type": "attempt"}\n')
    assert J._garak_finished(done) and not J._garak_finished(partial)
    assert not J._garak_finished(tmp_path / "missing.report.jsonl")


def test_mlflow_failure_never_fails_an_experiment(monkeypatch):
    import mlflow

    from horizon import logging_utils as LU
    for fn in ("log_params", "log_param", "log_metrics", "log_metric", "log_dict", "log_artifact", "set_tags",
               "set_tag", "log_text"):
        monkeypatch.setattr(mlflow, fn, getattr(mlflow, fn))        # restore what _disable_tracking replaces
    monkeypatch.setitem(LU._TRACKED, "on", True)

    def broken(*a, **k):
        raise RuntimeError("duplicate column name: step")
    monkeypatch.setattr(mlflow, "set_tracking_uri", broken)
    monkeypatch.setattr(LU.time, "sleep", lambda s: None)
    with LU.mlflow_run(CFG, "t") as run:                             # must not raise
        assert run is None
        mlflow.log_metrics({"x": 1.0})                               # later logging calls are no-ops now
    LU.log_examples([{"a": 1}], "x")                                 # ... and so is the CSV artifact
