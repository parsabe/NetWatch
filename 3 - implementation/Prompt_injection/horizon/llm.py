"""One LLM interface (LiteLLM) for local and hosted models, with an on-disk cache.

The cache key covers model, messages and sampling parameters. Prompts that
contain a fresh key κ are unique by construction, so caching never reuses an
answer across keys; it only makes re-runs and ablations free.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from horizon.config import ROOT

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# Ollama aborts a generation that loops ("prediction aborted, token repeat limit reached", HTTP 500). That is a property
# of the PROMPT, not a network hiccup: at temperature 0 the identical request loops again, so retrying it unchanged (what
# this client did) fails every time, and five days of cluster time went into retrying the same phase.
_DEGENERATE = re.compile(r"token repeat limit|prediction aborted|repeat limit reached", re.IGNORECASE)


class LLM:
    def __init__(self, model: str, models_cfg: dict, temperature: float | None = None):
        self.model = model
        self.cfg = models_cfg
        self.temperature = models_cfg["temperature"] if temperature is None else temperature
        self.cache_dir = (ROOT / models_cfg["cache_dir"]).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self.degenerate = 0          # generations Ollama aborted for looping even after the retries below

    def _api_base(self) -> str | None:
        """Ollama models use api_base; openai/<id> models use openai_api_base when set (e.g. colibri)."""
        if self.model.startswith(("openai/", "hosted_vllm/")) and self.cfg.get("openai_api_base"):
            return self.cfg["openai_api_base"]
        return self.cfg.get("api_base")

    def _key(self, messages, extra) -> str:
        blob = json.dumps({"model": self.model, "messages": messages, "t": self.temperature,
                           "max_tokens": self.cfg["max_tokens"], "extra": extra}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def chat(self, messages: list[dict], use_cache: bool = True, **extra) -> str:
        msgs = [dict(m) for m in messages]
        if self.cfg.get("disable_thinking"):
            extra = {"reasoning_effort": self.cfg["reasoning_effort_off"], **extra}
        key = self._key(msgs, extra)
        path = self.cache_dir / key[:2] / f"{key}.json"
        if use_cache and path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())["output"]

        import os

        import litellm
        litellm.suppress_debug_info = True
        # Self-hosted endpoints (local/remote Ollama, vLLM's OpenAI-compatible server) need api_base.
        self_hosted = self.model.startswith(("ollama", "openai/", "hosted_vllm/"))
        key = os.environ.get(self.cfg["api_key_env"]) if self.cfg.get("api_key_env") else None
        if self.model.startswith(("openai/", "hosted_vllm/")) and key is None:
            key = "local"
        last = None
        loops = 0
        attempts = int(self.cfg.get("llm_attempts", 4))
        cap = int(self.cfg["max_tokens"])
        for attempt in range(attempts):
            params = {"temperature": self.temperature, "max_tokens": cap}
            if loops:                       # break the loop: sample, penalise repeats (→ Ollama repeat_penalty), stop sooner
                lr = self.cfg["loop_retry"]
                params.update(temperature=min(1.0, max(self.temperature, lr["temperature_step"] * (1 + loops))),
                              frequency_penalty=1.0 + lr["repeat_penalty_step"] * loops,
                              max_tokens=max(lr["min_tokens"], cap // (1 + loops)))
            try:
                r = litellm.completion(model=self.model, messages=msgs,
                                       api_base=self._api_base() if self_hosted else None,
                                       api_key=key, timeout=self.cfg["request_timeout_s"], **{**extra, **params})
                out = r.choices[0].message.content or ""
                break
            except Exception as e:  # network hiccups / server warm-up / a looping generation
                last = e
                if _DEGENERATE.search(str(e)):
                    loops += 1          # a different sample is not a server hiccup: no sleep
                    continue
                time.sleep(2 ** attempt)
        else:
            if loops and self.cfg.get("degenerate_policy", "empty") == "empty":
                # Every sample looped. The model produced nothing usable: an empty answer (no canary, no tool call, no
                # value) is the faithful record. It is counted (self.degenerate), never cached, and never kills a phase.
                self.degenerate += 1
                return ""
            raise RuntimeError(f"LLM call failed after retries: {last}")
        out = _THINK.sub("", out).strip()
        self.calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "messages": msgs, "output": out}))
        return out


def server_alive(models_cfg: dict) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(models_cfg["api_base"] + "/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False
