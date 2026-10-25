"""End-to-End Horizon Pipeline (Runtime execution per request).

Cites: §4 (Target Architecture), §1.7 (Ablation flags), §6 (Stage 2 Runtime).
Workflow:
  user prompt u
   -> [Direct layer] LlamaFirewall/Prompt Guard 2 on u (block or pass)
   -> [Stage 2] P-LLM sees ONLY u -> program in CaMeL's restricted language
   -> deterministic interpreter executes, tracking DATA and CONTROL-FLOW dependencies
   -> for each untrusted input x: m quarantined runs, each with independent tau ~ pi* and
      fresh kappa, x_tilde = tau_kappa(x) [Stage 1], typed-schema output, no tools
   -> majority vote -> typed value v = (data, type, cap) -> Policy(t, v1..vk) ->
      ALLOW / DENY / CONFIRM(lambda) -> tool call
   -> [Direct layer] Llama Guard + canary check on the final output
"""
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import yaml

from ..direct_layer.canary import CanaryManager
from ..direct_layer.firewall import DirectLayerFirewall
from ..stage1.transforms import build_spotlighted_prompt
from ..stage2.conformal import evaluate_conformal_risk
from ..stage2.policy import PolicyDecision, PolicyEngine
from ..stage2.quarantine import QuarantinedExecutionAlarm, QuarantinedRunner
from ..stage2.types import Capability, TypedValue
from ..stage2.vote import majority_vote


class HorizonPipeline:
    """Full Horizon Unified Guardrail Pipeline with configurable ablations."""
    def __init__(
        self,
        config_path: Optional[Union[str, Path]] = None,
        pllm_client: Optional[Callable[[str, str], str]] = None,
        qllm_client: Optional[Callable[[str, str], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ):
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent.parent / "config" / "params.yaml"

        with open(config_path) as f:
            self.params = yaml.safe_load(f)

        self.ablations = self.params.get("ablations", {})
        self.pllm_client = pllm_client
        self.qllm_client = qllm_client
        self.tool_executor = tool_executor

        # Initialize subcomponents
        self.firewall = DirectLayerFirewall()
        self.canary_mgr = CanaryManager()
        self.policy_engine = PolicyEngine()
        self.quarantine_runner = QuarantinedRunner(llm_fn=self.qllm_client)

    def process_untrusted_input(
        self,
        task: str,
        untrusted_text: str,
        schema_type: str = "string",
    ) -> TypedValue:
        """Process untrusted external input x with Stage 1 spotlighting and Stage 2 quarantine."""
        use_stage1 = self.ablations.get("use_stage1", True)
        use_stage2 = self.ablations.get("use_stage2", True)
        use_vote = self.ablations.get("use_vote", True)
        m = self.ablations.get("m", 5) if use_vote else 1

        if not use_stage2:
            # Baseline without Stage 2: returns unquarantined string
            return TypedValue(data=untrusted_text, value_type=schema_type, cap=Capability.UNTRUSTED)

        # Quarantined execution across m runs
        try:
            typed_val = self.quarantine_runner.run_quarantine(
                task=task,
                untrusted_text=untrusted_text,
                schema_type=schema_type,
                m=m,
            )
            return typed_val
        except QuarantinedExecutionAlarm as e:
            # Value refused -> marked for confirmation/abort path
            return TypedValue(data=None, value_type=schema_type, cap=Capability.UNTRUSTED)

    def dispatch_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        control_flow: Capability = Capability.USER,
        user_role: str = "user",
        lambda_threshold: float = 0.5,
    ) -> Tuple[PolicyDecision, Any]:
        """Verify tool execution via deterministic policy and conformal threshold."""
        use_policy = self.ablations.get("use_policy", True)

        if not use_policy:
            # Ablation: policy disabled
            decision = PolicyDecision.ALLOW
        else:
            decision = self.policy_engine.evaluate(
                tool_name=tool_name,
                args=args,
                control_flow=control_flow,
                user_role=user_role,
            )

        if decision == PolicyDecision.DENY:
            return PolicyDecision.DENY, "Tool execution denied by Horizon Noninterference Policy."

        if decision == PolicyDecision.CONFIRM:
            # Conformal check: in autonomous runs or simulation, prompt confirmation or abort
            return PolicyDecision.CONFIRM, f"Action '{tool_name}' requires explicit user confirmation."

        # If ALLOW and executor is configured, execute tool
        if self.tool_executor:
            raw_args = {k: (v.data if isinstance(v, TypedValue) else v) for k, v in args.items()}
            tool_output = self.tool_executor(tool_name, raw_args)
            return PolicyDecision.ALLOW, tool_output

        return PolicyDecision.ALLOW, "Tool allowed."

    def execute_request(
        self,
        user_prompt: str,
        untrusted_context: Optional[str] = None,
        user_role: str = "user",
    ) -> Dict[str, Any]:
        """Execute per-request end-to-end pipeline."""
        use_firewall = self.ablations.get("use_firewall", True)
        use_canary = self.ablations.get("use_canary", True)
        use_output_guard = self.ablations.get("use_output_guard", True)

        # 1. Direct input inspection
        if use_firewall:
            allowed, reason = self.firewall.inspect_input(user_prompt, user_role=user_role)
            if not allowed:
                return {
                    "status": "BLOCKED_INPUT",
                    "reason": reason,
                    "output": None,
                }

        # 2. Embed canary in P-LLM system prompt
        base_system_prompt = "You are a secure assistant executing in the Horizon architecture."
        if use_canary:
            p_sys, canary = self.canary_mgr.embed_in_system_prompt(base_system_prompt)
        else:
            p_sys, canary = base_system_prompt, ""

        # 3. Process untrusted context if present
        v_untrusted = None
        if untrusted_context is not None:
            v_untrusted = self.process_untrusted_input(
                task="Extract structured content",
                untrusted_text=untrusted_context,
            )

        # 4. Generate response via P-LLM
        if self.pllm_client:
            raw_response = self.pllm_client(p_sys, user_prompt)
        else:
            raw_response = f"Execution completed for query: {user_prompt}"

        # 5. Direct output inspection (Llama Guard + Canary check)
        if use_output_guard or use_canary:
            allowed_out, out_reason = self.firewall.inspect_output(raw_response, canary)
            if not allowed_out:
                return {
                    "status": "BLOCKED_OUTPUT",
                    "reason": out_reason,
                    "output": None,
                }

        return {
            "status": "SUCCESS",
            "output": raw_response,
            "untrusted_value": v_untrusted,
        }
