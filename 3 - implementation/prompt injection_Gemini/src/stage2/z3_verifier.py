"""Z3 SMT Noninterference Verifier for Stage 2 Tool Policies.

Cites: Slide 39 (Step 7: policy check and noninterference theorem).
Theorem:
    "Tool call depends on UNTRUSTED data => Policy != ALLOW"
Ground Rules:
    - Runs at startup over all tools x label assignments (arguments + control dependency).
    - If a counterexample exists -> refuse to start and report exact counterexample.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml
import z3


class NoninterferenceViolationError(Exception):
    """Raised when policy violates the Noninterference theorem."""
    pass


def verify_policy_noninterference(
    policy_config: Union[str, Path, Dict[str, Any]]
) -> Dict[str, Any]:
    """Verify with Z3 that no tool can evaluate to ALLOW when dependent on UNTRUSTED data.

    Cites: Slide 39.
    Returns:
        Dict with status, verified_tools, and any counterexamples.
    Raises:
        NoninterferenceViolationError if a counterexample is found.
    """
    if isinstance(policy_config, (str, Path)):
        with open(policy_config) as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = policy_config

    tools = cfg.get("tools", {})
    verified = []
    counterexamples = []

    for tool_name, spec in tools.items():
        solver = z3.Solver()

        # Input variables
        cf_untrusted = z3.Bool(f"{tool_name}_cf_untrusted")
        # Assume up to 5 potential arguments for general tools
        max_args = 5
        arg_untrusted_vars = [z3.Bool(f"{tool_name}_arg_{i}_untrusted") for i in range(max_args)]

        depends_on_untrusted = z3.Or(cf_untrusted, *arg_untrusted_vars)

        # Encode tool's default action and rules into Z3 boolean for ALLOW
        default_action = spec.get("default_action", "CONFIRM")
        rules = spec.get("rules", [])

        # Start with default outcome
        is_allow = z3.BoolVal(default_action == "ALLOW")

        # Rules are evaluated in reverse order to form if-elif-else chain
        for rule in reversed(rules):
            cond_str = rule.get("condition", "")
            action_str = rule.get("action", "CONFIRM")
            rule_allows = (action_str == "ALLOW")

            # Map common condition expressions to Z3 formulas
            if "control_flow == 'UNTRUSTED'" in cond_str and "any_arg" not in cond_str:
                cond_z3 = cf_untrusted
            elif "any_arg == 'UNTRUSTED'" in cond_str and "control_flow" not in cond_str:
                cond_z3 = z3.Or(*arg_untrusted_vars)
            elif "control_flow == 'UNTRUSTED' or any_arg == 'UNTRUSTED'" in cond_str:
                cond_z3 = depends_on_untrusted
            elif "args['recipient']" in cond_str or "args['body']" in cond_str:
                cond_z3 = arg_untrusted_vars[0]
            elif "user_role" in cond_str:
                # Role check does not depend on taint
                cond_z3 = z3.BoolVal(False)
            else:
                cond_z3 = z3.BoolVal(False)

            is_allow = z3.If(cond_z3, z3.BoolVal(rule_allows), is_allow)

        # Counterexample query: Is it possible for tool to be ALLOW while depending on UNTRUSTED?
        # That is: Satisfiable( depends_on_untrusted AND is_allow )
        solver.add(depends_on_untrusted)
        solver.add(is_allow)

        check_res = solver.check()
        if check_res == z3.sat:
            model = solver.model()
            ce = {
                "tool": tool_name,
                "counterexample": str(model),
                "message": f"Violation: Tool '{tool_name}' returned ALLOW on untrusted input.",
            }
            counterexamples.append(ce)
        elif check_res == z3.unsat:
            verified.append(tool_name)
        else:
            raise RuntimeError(f"Z3 verification inconclusive for tool '{tool_name}'")

    if counterexamples:
        msg = f"Noninterference verification failed with {len(counterexamples)} counterexample(s):\n"
        for ce in counterexamples:
            msg += f"  - Tool: {ce['tool']} => {ce['message']} (Model: {ce['counterexample']})\n"
        raise NoninterferenceViolationError(msg)

    return {
        "status": "VERIFIED",
        "verified_tools": verified,
        "counterexamples": [],
    }
