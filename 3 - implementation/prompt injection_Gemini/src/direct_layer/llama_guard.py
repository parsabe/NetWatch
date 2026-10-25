"""Llama Guard wrapper for output safety validation.

Cites: §0, §2, §4 (Direct/jailbreak layer).
Validates assistant output before returning to user.

Supports:
1. Local Hugging Face transformers execution (meta-llama/Llama-Guard-3-8B).
2. API / LiteLLM / Ollama serving (e.g. llama-guard3:8b).
3. Robust heuristic fallback when offline or gated models are not pre-downloaded.

Safety Taxonomy (Llama Guard 3):
- S1: Violent Crimes
- S2: Non-Violent Crimes
- S3: Sex-Related Crimes
- S4: Child Sexual Exploitation and Abuse
- S5: Defamation
- S6: Suicide and Self-Harm
- S7: Cyberattacks & Malicious Exploits
- S8: Chemical, Biological, Radiological, Nuclear (CBRN)
- S9: Other High-Risk
- S10: Hate Speech
- S11: Harassment
- S12: Sexual Content
- S13: Privacy & Data Exfiltration
- S14: Unauthorized Specialized Advice
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union


class LlamaGuardOutputDetector:
    """Evaluates assistant outputs against safety policies and prompt injection side-effects."""

    CATEGORIES = {
        "S1": "Violent Crimes",
        "S2": "Non-Violent Crimes",
        "S3": "Sex-Related Crimes",
        "S4": "Child Sexual Exploitation and Abuse",
        "S5": "Defamation",
        "S6": "Suicide and Self-Harm",
        "S7": "Cyberattacks & Malicious Exploits",
        "S8": "CBRN",
        "S9": "Other High-Risk",
        "S10": "Hate Speech",
        "S11": "Harassment",
        "S12": "Sexual Content",
        "S13": "Privacy & Data Exfiltration",
        "S14": "Unauthorized Specialized Advice",
    }

    def __init__(
        self,
        model_name: str = "meta-llama/Llama-Guard-3-8B",
        threshold: float = 0.5,
        device: Optional[str] = None,
        auto_load: bool = False,
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.device = device
        self._is_loaded = False
        self.model = None
        self.tokenizer = None

        if auto_load:
            self.load_model()

    def load_model(self) -> bool:
        """Attempt to load transformers model and tokenizer."""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else "cpu"

            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map=self.device if self.device == "cuda" else None,
            )
            if self.device != "cuda":
                self.model.to(self.device)
            self.model.eval()
            self._is_loaded = True
            return True
        except Exception:
            self._is_loaded = False
            return False

    def is_safe(
        self,
        text: str,
        user_prompt: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Check if output text is safe under Llama Guard taxonomy.

        Args:
            text: Assistant generated response to inspect.
            user_prompt: Optional context of user's query.
        Returns:
            Tuple: (is_safe: bool, category_name: str, details: dict)
        """
        # 1. If transformers model is loaded, execute generative guardrail classification
        if self._is_loaded and self.model is not None and self.tokenizer is not None:
            try:
                import torch

                conversation = []
                if user_prompt:
                    conversation.append({"role": "user", "content": user_prompt})
                conversation.append({"role": "assistant", "content": text})

                # Format with official chat template
                formatted_prompt = self.tokenizer.apply_chat_template(
                    conversation,
                    tokenize=False,
                    add_generation_prompt=True,
                )
                inputs = self.tokenizer(formatted_prompt, return_tensors="pt")
                inputs = {k: v.to(self.device) for k, v in inputs.items()}

                with torch.no_grad():
                    output_tokens = self.model.generate(
                        **inputs,
                        max_new_tokens=30,
                        pad_token_id=self.tokenizer.eos_token_id,
                        do_sample=False,
                    )
                # Decode generated verdict
                prompt_len = inputs["input_ids"].shape[1]
                verdict = self.tokenizer.decode(
                    output_tokens[0][prompt_len:],
                    skip_special_tokens=True,
                ).strip()

                return self._parse_verdict(verdict)
            except Exception:
                pass

        # 2. Heuristic output safety checks for offline/simulated mode
        return self._heuristic_is_safe(text)

    def _parse_verdict(self, verdict: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Parse raw Llama Guard text output ('safe' or 'unsafe\nS...')."""
        lines = [line.strip() for line in verdict.splitlines() if line.strip()]
        if not lines:
            return True, "safe", {"raw_verdict": verdict, "violating_codes": []}

        status = lines[0].lower()
        if status.startswith("safe"):
            return True, "safe", {"raw_verdict": verdict, "violating_codes": []}

        # Status is unsafe
        violating_codes = []
        if len(lines) > 1:
            raw_codes = lines[1].split(",")
            violating_codes = [c.strip().upper() for c in raw_codes if c.strip()]

        cat_names = [self.CATEGORIES.get(code, f"Category {code}") for code in violating_codes]
        category_summary = ", ".join(cat_names) if cat_names else "Unsafe (General)"

        return False, category_summary, {
            "raw_verdict": verdict,
            "violating_codes": violating_codes,
            "categories": cat_names,
        }

    def _heuristic_is_safe(self, text: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Rule-based safety evaluator for offline and test runs."""
        lowered = text.lower()

        # Check Cyberattacks (S7)
        cyberattack_patterns = [
            r"\brm -rf\b",
            r"\bdrop table\b",
            r"\bselect \* from users\b",
            r"\b/etc/shadow\b",
            r"\b/etc/passwd\b",
            r"\bexec\s*\(",
            r"\beval\s*\(",
            r"\bchmod 777\b",
            r"\bcurl http://[0-9a-z.]+/malware",
            r"\bpayload:\s*<script>",
            r"\bexploit payload\b",
        ]
        for pat in cyberattack_patterns:
            if re.search(pat, lowered):
                return False, self.CATEGORIES["S7"], {"violating_codes": ["S7"], "matched_pattern": pat}

        # Check Privacy / Exfiltration (S13)
        exfiltration_patterns = [
            r"\bpassword\s*=\s*['\"][^'\"]+['\"]",
            r"\bsecret_key\s*=\s*['\"][^'\"]+['\"]",
            r"\bapi_key\s*=\s*['\"][^'\"]+['\"]",
            r"\baws_secret_access_key\b",
            r"\bBEGIN PRIVATE KEY\b",
        ]
        for pat in exfiltration_patterns:
            if re.search(pat, text):
                return False, self.CATEGORIES["S13"], {"violating_codes": ["S13"], "matched_pattern": pat}

        return True, "safe", {"violating_codes": []}
