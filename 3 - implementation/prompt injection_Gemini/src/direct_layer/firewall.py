"""LlamaFirewall / Direct Layer Orchestrator.

Combines:
- Input Prompt Guard 2
- Output Llama Guard
- Canary Token Manager
- Role-based policy rules
Cites: §0, §4.
"""
from typing import Any, Dict, Optional, Tuple

from .canary import CanaryManager
from .llama_guard import LlamaGuardOutputDetector
from .prompt_guard import PromptGuardDetector


class DirectLayerFirewall:
    """End-to-end direct injection and jailbreak defense layer."""
    def __init__(
        self,
        canary_manager: Optional[CanaryManager] = None,
        prompt_guard: Optional[PromptGuardDetector] = None,
        llama_guard: Optional[LlamaGuardOutputDetector] = None,
    ):
        self.canary_mgr = canary_manager or CanaryManager()
        self.prompt_guard = prompt_guard or PromptGuardDetector()
        self.llama_guard = llama_guard or LlamaGuardOutputDetector()

    def inspect_input(self, user_prompt: str, user_role: str = "user") -> Tuple[bool, str]:
        """Inspect user prompt u on input. Returns (is_allowed, block_reason).

        Cites: §4 (Direct layer on u: block or pass).
        """
        # Role-based restriction: guest roles cannot issue admin system directives
        if user_role == "guest":
            if any(k in user_prompt.lower() for k in ["admin", "system", "delete", "format"]):
                return False, "Blocked by role-based policy: guest role privilege violation."

        if self.prompt_guard.is_injection(user_prompt):
            return False, "Blocked by Prompt Guard: Direct injection or jailbreak detected."

        return True, "Passed input firewall."

    def inspect_output(self, response_text: str, active_canary: str) -> Tuple[bool, str]:
        """Inspect model output. Returns (is_allowed, block_reason).

        Cites: §4 (Llama Guard + canary check on final output).
        """
        # 1. Canary check
        if self.canary_mgr.check_output_for_leakage(response_text, active_canary):
            return False, "Blocked: Output contains secret system prompt canary token (leakage detected)."

        # 2. Llama Guard check
        is_safe, category, *rest = self.llama_guard.is_safe(response_text)
        if not is_safe:
            return False, f"Blocked by Llama Guard: Output flagged under safety category '{category}'."

        return True, "Passed output guard."
