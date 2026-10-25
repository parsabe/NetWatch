"""Startup proof of noninterference with Z3 (Slide 38).

Theorem checked:  tool call depends on UNTRUSTED data  ⇒  Policy ≠ ALLOW
for every tool t ∈ T, every label assignment of its arguments
(cap ∈ {SYSTEM, USER, UNTRUSTED}), every control-dependency label, and every role.

The policy rule list is compiled to a Z3 term; we ask Z3 for an assignment with
(some argument or the control dependency UNTRUSTED) ∧ Policy = ALLOW.
UNSAT for every tool = proof. SAT = counterexample → refuse to start.
"""
from __future__ import annotations

from dataclasses import dataclass

import z3

from horizon.stage2.policy import Decision, PolicyTable
from horizon.stage2.values import Cap

_DEC = {Decision.ALLOW: 0, Decision.DENY: 1, Decision.CONFIRM: 2}


@dataclass
class Counterexample:
    tool: str
    role: str
    arg_caps: dict[str, str]
    control_cap: str

    def __str__(self) -> str:
        return (f"tool={self.tool} role={self.role} control={self.control_cap} "
                f"args={self.arg_caps} → ALLOW although it depends on UNTRUSTED data")


class NoninterferenceViolation(RuntimeError):
    pass


def _compile(table: PolicyTable, tool: str, arg_vars: dict[str, z3.ArithRef],
             ctrl: z3.ArithRef, role: z3.ArithRef) -> z3.ArithRef:
    tc = table.tool_class(tool)
    unt = int(Cap.UNTRUSTED)
    data_u = z3.Or([v == unt for v in arg_vars.values()]) if arg_vars else z3.BoolVal(False)
    ctrl_u = ctrl == unt

    def cond(c: dict) -> z3.BoolRef:
        parts = []
        for key, want in c.items():
            if key == "untrusted_any":
                parts.append(z3.Or(data_u, ctrl_u) == want)
            elif key == "untrusted_data":
                parts.append(data_u == want)
            elif key == "untrusted_control":
                parts.append(ctrl_u == want)
            elif key == "untrusted_args":
                parts.append(z3.Or([arg_vars[a] == unt for a in want if a in arg_vars] or [z3.BoolVal(False)]))
            elif key == "role_in":
                parts.append(z3.Or([role == table.roles.index(r) for r in want if r in table.roles]
                                   or [z3.BoolVal(False)]))
            elif key == "tool_class":
                parts.append(z3.BoolVal(tc == want))
            else:
                raise ValueError(f"unknown policy condition '{key}'")
        return z3.And(parts) if parts else z3.BoolVal(True)

    term = z3.IntVal(_DEC[Decision.DENY])                     # no match → DENY
    for rule in reversed(table.rules_for(tool)):
        term = z3.If(cond(rule.get("when", {})), z3.IntVal(_DEC[Decision(rule["then"])]), term)
    return term


def check_tool(table: PolicyTable, tool: str, arg_names: list[str]) -> Counterexample | None:
    arg_vars = {a: z3.Int(f"cap_{a}") for a in arg_names}
    ctrl, role = z3.Int("cap_control"), z3.Int("role")
    s = z3.Solver()
    for v in [*arg_vars.values(), ctrl]:
        s.add(v >= int(Cap.SYSTEM), v <= int(Cap.UNTRUSTED))
    s.add(role >= 0, role < len(table.roles))
    unt = int(Cap.UNTRUSTED)
    depends_on_untrusted = z3.Or([v == unt for v in [*arg_vars.values(), ctrl]])
    s.add(depends_on_untrusted, _compile(table, tool, arg_vars, ctrl, role) == _DEC[Decision.ALLOW])
    if s.check() == z3.unsat:
        return None
    mdl = s.model()
    val = lambda x: Cap(mdl.eval(x, model_completion=True).as_long()).name
    return Counterexample(tool, table.roles[mdl.eval(role, model_completion=True).as_long()],
                          {a: val(v) for a, v in arg_vars.items()}, val(ctrl))


def verify_noninterference(table: PolicyTable, tools: dict[str, list[str]]) -> list[Counterexample]:
    """All counterexamples over all tools (empty list = proof holds)."""
    out = []
    for tool, args in sorted(tools.items()):
        if tool in table.internal:
            continue
        ce = check_tool(table, tool, args)
        if ce:
            out.append(ce)
    return out


def assert_noninterference(table: PolicyTable, tools: dict[str, list[str]]) -> None:
    """Refuse to start if the policy admits an ALLOW on UNTRUSTED-dependent calls."""
    ces = verify_noninterference(table, tools)
    if ces:
        msg = "\n".join(str(c) for c in ces)
        raise NoninterferenceViolation(
            f"Policy '{table.name}' violates noninterference; refusing to start.\n{msg}")
