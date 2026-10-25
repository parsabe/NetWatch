"""Quarantined LLM execution with per-run independent spotlighting and majority voting.

Cites: Slide 34, Slide 35 (Step 4: quarantined LLM x m), Slide 36 (majority vote).
Ground Rules:
- For untrusted x, run the Q-LLM m times.
- DECISION (author specification): Each run draws its OWN tau ~ pi* and its OWN fresh key kappa.
- Q-LLM has NO tools and returns a Pydantic-typed schema or parses strictly via SchemaParser.
- Majority vote accepts y* only if > m/2 runs agree; otherwise alarm -> value refused.
- Wrapped in TypedValue with capability UNTRUSTED.
"""
import math
import secrets
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel

from ..stage1.keys import generate_key
from ..stage1.transforms import TRANSFORMS, build_spotlighted_prompt
from .types import Capability, PARSERS, TypedValue
from .vote import majority_vote


class QuarantinedExecutionAlarm(Exception):
    """Raised when majority vote across quarantined runs fails to reach agreement."""
    pass


class QuarantinedRunner:
    """Executes untrusted inputs in quarantine with randomized spotlighting and voting.

    Cites: Slide 34, Slide 35, Slide 36.
    """
    def __init__(
        self,
        llm_fn: Optional[Callable[[str, str], str]] = None,
        pi_star: Optional[Dict[str, float]] = None,
        default_variant: str = "datamark_dyn",
    ):
        self.llm_fn = llm_fn
        # Optimal mix pi* from Stage 1 (defaults to single safe variant if uncalibrated)
        self.pi_star = pi_star or {default_variant: 1.0}

    def _sample_variant(self) -> str:
        """Sample tau ~ pi* using CSPRNG."""
        variants = list(self.pi_star.keys())
        weights = list(self.pi_star.values())
        total_w = sum(weights)
        if total_w <= 0:
            return variants[0]
        # Cumulative sum with secrets
        r = secrets.randbelow(1000000) / 1000000.0 * total_w
        cum = 0.0
        for v, w in zip(variants, weights):
            cum += w
            if r <= cum:
                return v
        return variants[-1]

    def run_quarantine(
        self,
        task: str,
        untrusted_text: str,
        schema_type: str = "string",
        m: int = 5,
        simulate_outputs: Optional[List[str]] = None,
    ) -> TypedValue:
        """Run m independent quarantined evaluations over untrusted_text.

        Each run draws its OWN tau ~ pi* and fresh kappa (Slide 35).
        Cites: Slide 34, Slide 35, Slide 36, Slide 38.
        """
        parser = PARSERS.get(schema_type, PARSERS["string"])
        raw_outputs: List[str] = []
        parsed_outputs: List[Any] = []

        for i in range(m):
            if simulate_outputs and i < len(simulate_outputs):
                out = simulate_outputs[i]
            elif self.llm_fn is not None:
                tau = self._sample_variant()
                sys_p, usr_p, key = build_spotlighted_prompt(task, untrusted_text, tau)
                out = self.llm_fn(sys_p, usr_p)
            else:
                # Default fallback for testing or mock environments
                out = untrusted_text

            raw_outputs.append(out)
            try:
                val = parser(out)
                parsed_outputs.append(val)
            except Exception:
                # Parser rejected malicious/malformed output
                parsed_outputs.append(None)

        # Execute majority vote across parsed outputs
        success, winning_val, count, reason = majority_vote(parsed_outputs, m)

        if not success or winning_val is None:
            # Alarm raised: majority vote failed or values refused
            raise QuarantinedExecutionAlarm(
                f"Quarantine rejected untrusted input: {reason} "
                f"(Outputs: {raw_outputs})"
            )

        # Return boxed TypedValue tagged as UNTRUSTED
        return TypedValue(
            data=winning_val,
            value_type=schema_type,
            cap=Capability.UNTRUSTED,
        )
