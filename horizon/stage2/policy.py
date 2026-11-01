"""Deterministic policy engine (Slide 38).

Policy(t, v_1..v_k) ∈ {ALLOW, DENY, CONFIRM}, read from config/policy.yaml.
The same rule list is evaluated here (runtime) and compiled to Z3 in
z3_check.py (startup proof of noninterference), so the proof is about the
exact table that runs.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from horizon.config import ROOT
from horizon.stage2.values import Cap


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    CONFIRM = "CONFIRM"


@dataclass
class CallContext:
    tool: str
    arg_caps: dict[str, Cap]
    control_cap: Cap
    role: str


class PolicyTable:
    def __init__(self, raw: dict, policy_name: str):
        self.raw = raw
        self.name = policy_name
        if policy_name not in raw["policies"]:
            raise KeyError(f"policy '{policy_name}' not in policy table")
        self.policy = raw["policies"][policy_name]
        self.roles = raw["roles"]["all"]
        self.default_role = raw["roles"]["default"]
        self.read_only = raw["read_only_tools"]
        self.internal = set(raw.get("internal_functions", []))

    @classmethod
    def load(cls, policy_name: str, path: str | Path | None = None) -> "PolicyTable":
        p = Path(path) if path else ROOT / "config" / "policy.yaml"
        return cls(yaml.safe_load(p.read_text()), policy_name)

    def tool_class(self, tool: str) -> str:
        return "read_only" if any(fnmatch.fnmatch(tool, pat) for pat in self.read_only) else "side_effect"

    def rules_for(self, tool: str) -> list[dict]:
        return self.policy.get("overrides", {}).get(tool, self.policy["default"])

    @staticmethod
    def _holds(cond: dict, ctx: CallContext, tool_class: str) -> bool:
        data_u = any(c == Cap.UNTRUSTED for c in ctx.arg_caps.values())
        ctrl_u = ctx.control_cap == Cap.UNTRUSTED
        for key, want in cond.items():
            if key == "untrusted_any" and (data_u or ctrl_u) != want:
                return False
            if key == "untrusted_data" and data_u != want:
                return False
            if key == "untrusted_control" and ctrl_u != want:
                return False
            if key == "untrusted_args" and not any(ctx.arg_caps.get(a) == Cap.UNTRUSTED for a in want):
                return False
            if key == "role_in" and ctx.role not in want:
                return False
            if key == "tool_class" and tool_class != want:
                return False
        return True

    def decide(self, ctx: CallContext) -> Decision:
        """Slide 38: first matching rule decides; no match → DENY."""
        tc = self.tool_class(ctx.tool)
        for rule in self.rules_for(ctx.tool):
            if self._holds(rule.get("when", {}), ctx, tc):
                return Decision(rule["then"])
        return Decision.DENY
