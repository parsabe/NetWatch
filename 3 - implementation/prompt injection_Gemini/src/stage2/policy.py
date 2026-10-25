"""Deterministic tool policy and access control for Stage 2.

Cites: Slide 39 (Step 7: policy check and noninterference).
Ground Rules:
- Deterministic rule table per tool loaded from YAML.
- Role-based rules for user roles.
- Noninterference invariant: Tool call depends on UNTRUSTED data => Policy != ALLOW.
"""
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

from .types import Capability, TypedValue


class PolicyDecision(str, Enum):
    """Deterministic policy decision."""
    ALLOW = "ALLOW"
    DENY = "DENY"
    CONFIRM = "CONFIRM"


class PolicyEngine:
    """Evaluates deterministic tool invocation policies.

    Cites: Slide 39.
    """
    def __init__(self, policy_config_path: Optional[Union[str, Path]] = None, config_dict: Optional[Dict[str, Any]] = None):
        if config_dict is not None:
            self.config = config_dict
        elif policy_config_path is not None:
            with open(policy_config_path) as f:
                self.config = yaml.safe_load(f)
        else:
            default_path = Path(__file__).resolve().parent.parent.parent / "config" / "policy.yaml"
            with open(default_path) as f:
                self.config = yaml.safe_load(f)

        self.tools = self.config.get("tools", {})
        self.roles = self.config.get("roles", {})

    def evaluate(
        self,
        tool_name: str,
        args: Dict[str, Any],
        control_flow: Capability = Capability.USER,
        user_role: str = "user",
    ) -> PolicyDecision:
        """Evaluate policy for a tool call.

        Args:
            tool_name: Name of tool to execute.
            args: Mapping of arg_name -> TypedValue or raw value.
            control_flow: Capability label of control flow.
            user_role: Role of calling user (e.g., admin, user, guest).
        Returns:
            PolicyDecision in {ALLOW, DENY, CONFIRM}.

        Cites: Slide 39.
        """
        if tool_name not in self.tools:
            # Unknown tool defaults to DENY for security
            return PolicyDecision.DENY

        tool_spec = self.tools[tool_name]
        default_action = PolicyDecision(tool_spec.get("default_action", "CONFIRM"))

        # Extract argument capabilities
        arg_caps: Dict[str, str] = {}
        has_untrusted_arg = False
        for k, v in args.items():
            if isinstance(v, TypedValue):
                cap_str = v.cap.value
            elif hasattr(v, "capability"):
                cap_str = str(v.capability)
            else:
                cap_str = Capability.USER.value
            arg_caps[k] = cap_str
            if cap_str == Capability.UNTRUSTED.value:
                has_untrusted_arg = True

        cf_untrusted = (control_flow == Capability.UNTRUSTED or str(control_flow) == "UNTRUSTED")
        is_dependent_on_untrusted = cf_untrusted or has_untrusted_arg

        # Evaluate explicit rules in order
        rules = tool_spec.get("rules", [])
        for rule in rules:
            cond = rule.get("condition", "")
            action = PolicyDecision(rule.get("action", "CONFIRM"))

            env = {
                "control_flow": "UNTRUSTED" if cf_untrusted else "USER",
                "any_arg": "UNTRUSTED" if has_untrusted_arg else "USER",
                "args": arg_caps,
                "user_role": user_role,
            }

            try:
                # Safe eval of condition boolean expression
                if eval(cond, {"__builtins__": {}}, env):
                    # Enforce noninterference: if untrusted, action can never be ALLOW
                    if is_dependent_on_untrusted and action == PolicyDecision.ALLOW:
                        return PolicyDecision.CONFIRM
                    return action
            except Exception:
                continue

        # If no specific rule triggered, use default action, guarded by noninterference
        if is_dependent_on_untrusted and default_action == PolicyDecision.ALLOW:
            return PolicyDecision.CONFIRM

        return default_action
