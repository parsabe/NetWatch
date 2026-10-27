"""Direct-injection / jailbreak layer (NOT part of the Horizon math).

* Input:  ProtectAI deberta-v3-base-prompt-injection-v2 on the user prompt u (Apache-2.0, not gated). It replaces
          Prompt Guard 2 / LlamaFirewall (Meta access rejected). Model card: detects prompt injection in English,
          NOT jailbreaks; its training data includes XSTest (one of our benign sets) — reported, never hidden.
* Output: Qwen3Guard-Gen on the final (user, assistant) exchange (Qwen, Apache-2.0, not gated). It replaces
          Llama Guard, whose access request was rejected by Meta; same role, same off-the-shelf use, no training.
Both load lazily and raise GatedModelError with instructions if a model cannot be loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


class GatedModelError(RuntimeError):
    pass


@dataclass
class GuardVerdict:
    blocked: bool
    score: float | None
    reason: str
    detector: str


def _device(pref: str, min_free_gb: float = 0.0) -> str:
    import torch
    if pref != "auto":
        return pref
    if not torch.cuda.is_available():
        return "cpu"
    # the GPU is shared with Ollama (and other guard processes in the campaign): CPU instead of an OOM crash
    return "cuda" if torch.cuda.mem_get_info()[0] >= min_free_gb * 2 ** 30 else "cpu"


def _gated(model_id: str, err: Exception) -> GatedModelError:
    return GatedModelError(
        f"Cannot load '{model_id}' ({type(err).__name__}: {str(err)[:160]}). It is gated: accept the "
        f"license at https://huggingface.co/{model_id} with the account used by `hf auth login`.")


class InjectionGuardInput:
    """DeBERTa prompt-injection classifier (SAFE / INJECTION) on the user prompt.

    Text longer than the model window is scored in windows; the verdict uses the highest INJECTION probability.
    """

    def __init__(self, direct_cfg: dict):
        self.cfg = direct_cfg
        self._model = self._tok = None

    def _load(self):
        if self._model is not None:
            return
        mid, rev = self.cfg["input_guard_model"], self.cfg["input_guard_revision"]
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(mid, revision=rev)
            self._model = AutoModelForSequenceClassification.from_pretrained(mid, revision=rev).to(
                _device(self.cfg["device"], self.cfg.get("min_free_gpu_gb", 0))).eval()
            self._pos = next(int(k) for k, v in self._model.config.id2label.items()
                             if v == self.cfg["input_guard_positive_label"])
        except Exception as e:
            raise _gated(mid, e) from e

    def score(self, text: str) -> float:
        self._load()
        import torch
        window = min(self._tok.model_max_length, self.cfg["input_guard_max_window"])
        enc = self._tok(text, truncation=True, max_length=window, stride=self.cfg["input_guard_stride"],
                        return_overflowing_tokens=True, padding=True, return_tensors="pt")
        with torch.no_grad():
            logits = self._model(input_ids=enc["input_ids"].to(self._model.device),
                                 attention_mask=enc["attention_mask"].to(self._model.device)).logits
        return float(torch.softmax(logits, dim=-1)[:, self._pos].max())

    def check(self, text: str) -> GuardVerdict:
        sc = self.score(text)
        blocked = sc >= self.cfg["input_guard_threshold"]
        return GuardVerdict(blocked, sc, f"P(injection) = {sc:.3f}", "deberta_injection")


class Qwen3GuardOutput:
    """Qwen3Guard-Gen (transformers) on the final output, or on the prompt alone when there is no answer.

    The model writes "Safety: Safe|Controversial|Unsafe", "Categories: …" and, for responses, "Refusal: Yes|No".
    Labels listed in direct.output_guard_block are blocked, unless every category named is in
    direct.output_guard_ignore_categories (e.g. PII when the user asked for their own contacts).
    """

    SAFETY = re.compile(r"Safety:\s*(Safe|Unsafe|Controversial)")
    CATEGORIES = re.compile(r"Categories:\s*(.+)")
    REFUSAL = re.compile(r"Refusal:\s*(Yes|No)")

    def __init__(self, direct_cfg: dict):
        self.cfg = direct_cfg
        self._model = self._tok = None

    def _load(self):
        if self._model is not None:
            return
        mid, rev = self.cfg["output_guard_model"], self.cfg["output_guard_revision"]
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(mid, revision=rev)
            dev = _device(self.cfg["device"], self.cfg.get("min_free_gpu_gb", 0))
            self._model = AutoModelForCausalLM.from_pretrained(
                mid, revision=rev, dtype=torch.bfloat16 if dev == "cuda" else torch.float32).to(dev)
        except Exception as e:
            raise _gated(mid, e) from e

    def parse(self, text: str) -> tuple[str | None, list[str], str | None]:
        label = self.SAFETY.search(text)
        cats = self.CATEGORIES.search(text)
        refusal = self.REFUSAL.search(text)
        return (label.group(1) if label else None,
                [c.strip() for c in cats.group(1).split(",")] if cats else [],
                refusal.group(1) if refusal else None)

    def verdict(self, text: str) -> GuardVerdict:
        label, cats, refusal = self.parse(text)
        reason = " · ".join(x for x in [label or "unparsed", ", ".join(cats),
                                        f"refusal {refusal}" if refusal else ""] if x)
        ignored = set(self.cfg["output_guard_ignore_categories"])
        counted = [c for c in cats if c not in ignored and c != "None"]
        blocked = label in self.cfg["output_guard_block"] and (bool(counted) or not cats or not ignored)
        return GuardVerdict(blocked, None, reason, "qwen3guard")

    def check(self, user_text: str, assistant_text: str = "") -> GuardVerdict:
        self._load()
        import torch
        conv = [{"role": "user", "content": user_text}]
        if assistant_text:
            conv.append({"role": "assistant", "content": assistant_text})
        text = self._tok.apply_chat_template(conv, tokenize=False)
        ids = self._tok([text], return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(**ids, max_new_tokens=self.cfg["guard_max_new_tokens"], do_sample=False)
        return self.verdict(self._tok.decode(out[0, ids.input_ids.shape[-1]:], skip_special_tokens=True))
