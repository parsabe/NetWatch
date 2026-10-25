"""Llama Prompt Guard 2 (86M) wrapper for direct injection and jailbreak detection on input.

Cites: §0, §2, §4 (Direct/jailbreak layer).
Checks user input u before passing to Stage 2.

Model details (meta-llama/Prompt-Guard-86M):
- Architecture: Multi-class sequence classifier (3 classes):
    Label 0: BENIGN
    Label 1: INJECTION (indirect and direct instruction hijacking)
    Label 2: JAILBREAK (adversarial persona bypass, system override)
- Decision rule:
    Flagged if prob_injection >= injection_threshold OR prob_jailbreak >= jailbreak_threshold.
    Overall risk score = 1.0 - prob_benign = prob_injection + prob_jailbreak.
"""
import os
import re
from typing import Any, Dict, Optional, Tuple


class PromptGuardDetector:
    """Full-featured Prompt Guard 2 detector with 3-class probability evaluation and device handling."""

    LABEL_BENIGN = 0
    LABEL_INJECTION = 1
    LABEL_JAILBREAK = 2

    LABEL_NAMES = {
        0: "BENIGN",
        1: "INJECTION",
        2: "JAILBREAK",
    }

    def __init__(
        self,
        model_name: str = "meta-llama/Prompt-Guard-86M",
        injection_threshold: float = 0.5,
        jailbreak_threshold: float = 0.5,
        device: Optional[str] = None,
        auto_load: bool = False,
    ):
        self.model_name = model_name
        self.injection_threshold = injection_threshold
        self.jailbreak_threshold = jailbreak_threshold
        self.device = device
        self.model = None
        self.tokenizer = None
        self._is_loaded = False

        if auto_load:
            self.load_model()

    def load_model(self) -> bool:
        """Attempt to load transformers model onto target device."""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else "cpu"

            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self.model.to(self.device)
            self.model.eval()
            self._is_loaded = True
            return True
        except Exception:
            # Gated model or offline environment: fall back gracefully to heuristic analyzer
            self._is_loaded = False
            return False

    def predict(self, text: str) -> Dict[str, Any]:
        """Perform 3-class prediction over the text.

        Returns:
            Dict containing:
              - is_safe: bool
              - is_injection: bool
              - is_jailbreak: bool
              - label: 'BENIGN' | 'INJECTION' | 'JAILBREAK'
              - prob_benign: float in [0, 1]
              - prob_injection: float in [0, 1]
              - prob_jailbreak: float in [0, 1]
              - risk_score: float in [0, 1]
        """
        # If model is loaded in transformers, run tensor inference
        if self._is_loaded and self.model is not None and self.tokenizer is not None:
            try:
                import torch

                inputs = self.tokenizer(
                    text,
                    return_tensors="pt",
                    truncation=True,
                    max_length=512,
                )
                inputs = {k: v.to(self.device) for k, v in inputs.items()}

                with torch.no_grad():
                    logits = self.model(**inputs).logits
                    probs = torch.softmax(logits, dim=-1)[0].cpu().numpy()

                p_benign = float(probs[self.LABEL_BENIGN])
                p_injection = float(probs[self.LABEL_INJECTION])
                p_jailbreak = float(probs[self.LABEL_JAILBREAK])

                pred_class_idx = int(probs.argmax())
                pred_label = self.LABEL_NAMES.get(pred_class_idx, "UNKNOWN")

                is_inj = p_injection >= self.injection_threshold
                is_jb = p_jailbreak >= self.jailbreak_threshold
                is_safe = not (is_inj or is_jb)
                risk_score = float(1.0 - p_benign)

                return {
                    "is_safe": is_safe,
                    "is_injection": is_inj,
                    "is_jailbreak": is_jb,
                    "label": pred_label,
                    "prob_benign": p_benign,
                    "prob_injection": p_injection,
                    "prob_jailbreak": p_jailbreak,
                    "risk_score": risk_score,
                }
            except Exception:
                # If CUDA out of memory or inference issue occurs, fall back to heuristic
                pass

        # Offline / Heuristic fallback engine
        return self._heuristic_predict(text)

    def _heuristic_predict(self, text: str) -> Dict[str, Any]:
        """Rule-based analyzer matching injection and jailbreak signatures."""
        lowered = text.lower()

        jailbreak_patterns = [
            r"\bdan mode\b",
            r"\bjailbreak\b",
            r"\bdeveloper mode enabled\b",
            r"\bignore all previous instructions\b",
            r"\bdisregard all previous\b",
            r"\bhypothetical scenario without constraints\b",
            r"\bdo anything now\b",
            r"\bpretend you have no rules\b",
            r"\bbypass restrictions\b",
            r"\bact as an unfiltered\b",
        ]

        injection_patterns = [
            r"\[system update\]",
            r"### system:",
            r"--- system alert ---",
            r"\bignore your task\b",
            r"\byour new task is to\b",
            r"\bdisregard your task\b",
            r"\boverride previous task\b",
            r"\bcanary-[0-9a-f]+\b",
            r"\becho this code\b",
            r"\bprint only the word\b",
        ]

        has_jb = any(re.search(pat, lowered) for pat in jailbreak_patterns)
        has_inj = any(re.search(pat, lowered) for pat in injection_patterns)

        if has_jb and has_inj:
            p_benign, p_injection, p_jailbreak = 0.02, 0.45, 0.53
            pred_label = "JAILBREAK"
        elif has_jb:
            p_benign, p_injection, p_jailbreak = 0.05, 0.10, 0.85
            pred_label = "JAILBREAK"
        elif has_inj:
            p_benign, p_injection, p_jailbreak = 0.05, 0.85, 0.10
            pred_label = "INJECTION"
        else:
            p_benign, p_injection, p_jailbreak = 0.95, 0.03, 0.02
            pred_label = "BENIGN"

        is_inj = p_injection >= self.injection_threshold
        is_jb = p_jailbreak >= self.jailbreak_threshold
        is_safe = not (is_inj or is_jb)
        risk_score = float(1.0 - p_benign)

        return {
            "is_safe": is_safe,
            "is_injection": is_inj,
            "is_jailbreak": is_jb,
            "label": pred_label,
            "prob_benign": p_benign,
            "prob_injection": p_injection,
            "prob_jailbreak": p_jailbreak,
            "risk_score": risk_score,
        }

    def score(self, text: str) -> float:
        """Compute malicious risk score in [0, 1]."""
        res = self.predict(text)
        return res["risk_score"]

    def is_injection(self, text: str) -> bool:
        """Returns True if the text is classified as an injection or jailbreak."""
        res = self.predict(text)
        return not res["is_safe"]
