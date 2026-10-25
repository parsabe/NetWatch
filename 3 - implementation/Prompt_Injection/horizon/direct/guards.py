"""Direct-injection / jailbreak layer (NOT part of the Horizon math).

* Input:  LlamaFirewall with the Prompt Guard 2 scanner on the user prompt u.
* Output: Llama Guard on the final (user, assistant) exchange.
Both models are gated on Hugging Face; they load lazily and raise
GatedModelError with instructions if access is missing.
"""
from __future__ import annotations

from dataclasses import dataclass


class GatedModelError(RuntimeError):
    pass


@dataclass
class GuardVerdict:
    blocked: bool
    score: float | None
    reason: str
    detector: str


def _device(pref: str) -> str:
    import torch
    if pref != "auto":
        return pref
    return "cuda" if torch.cuda.is_available() else "cpu"


def _gated(model_id: str, err: Exception) -> GatedModelError:
    return GatedModelError(
        f"Cannot load '{model_id}' ({type(err).__name__}: {str(err)[:160]}). It is gated: accept the "
        f"license at https://huggingface.co/{model_id} with the account used by `hf auth login`.")


class PromptGuardInput:
    """LlamaFirewall + Prompt Guard 2 on the user prompt."""

    def __init__(self, direct_cfg: dict):
        self.cfg = direct_cfg
        self._fw = None

    def _load(self):
        if self._fw is not None:
            return
        try:
            import huggingface_hub
            if not hasattr(huggingface_hub, "HfFolder"):   # removed in huggingface_hub 1.x; LlamaFirewall
                huggingface_hub.HfFolder = type("HfFolder", (), {"get_token": staticmethod(huggingface_hub.get_token)})
            from llamafirewall import LlamaFirewall, Role, ScannerType
            fw = LlamaFirewall(scanners={Role.USER: [ScannerType.PROMPT_GUARD]})
            from llamafirewall import UserMessage
            fw.scan(UserMessage(content="hello"))         # forces the model download / load
            self._fw = fw
        except Exception as e:  # gated HF repo, missing token, …
            raise _gated(self.cfg["prompt_guard_model"], e) from e

    def check(self, text: str) -> GuardVerdict:
        self._load()
        from llamafirewall import ScanDecision, UserMessage
        r = self._fw.scan(UserMessage(content=text))
        blocked = r.decision == ScanDecision.BLOCK or (r.score is not None and r.score >= self.cfg["prompt_guard_threshold"])
        return GuardVerdict(blocked, r.score, str(r.reason), "llamafirewall/prompt_guard_2")


class LlamaGuardOutput:
    """Llama Guard (transformers) on the final output."""

    def __init__(self, direct_cfg: dict):
        self.cfg = direct_cfg
        self._model = self._tok = None

    def _load(self):
        if self._model is not None:
            return
        mid = self.cfg["llama_guard_model"]
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(mid)
            dev = _device(self.cfg["device"])
            self._model = AutoModelForCausalLM.from_pretrained(
                mid, torch_dtype=torch.bfloat16 if dev == "cuda" else torch.float32).to(dev)
        except Exception as e:
            raise _gated(mid, e) from e

    def check(self, user_text: str, assistant_text: str) -> GuardVerdict:
        self._load()
        import torch
        conv = [{"role": "user", "content": [{"type": "text", "text": user_text}]},
                {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}]
        ids = self._tok.apply_chat_template(conv, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(ids, max_new_tokens=self.cfg["guard_max_new_tokens"],
                                       do_sample=False, pad_token_id=self._tok.eos_token_id)
        verdict = self._tok.decode(out[0, ids.shape[-1]:], skip_special_tokens=True).strip()
        return GuardVerdict(verdict.lower().startswith("unsafe"), None, verdict, "llama_guard")
