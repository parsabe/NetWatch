"""Majority voting and Hoeffding confidence bounds for quarantined runs.

Cites: Slide 36 (Step 5: majority vote), Slide 37 (Step 5: Hoeffding bound).
Ground Rules:
- Canonicalize typed outputs.
- Accept y* only if > m/2 runs agree (strict majority); otherwise alarm -> refused -> CONFIRM/abort.
- If R_bar_Q < 0.5: compute exp(-2 * m * (0.5 - R_bar_Q)^2); if >= 0.5: return 'no guarantee'.
- Measure empirical vote-fooling rate alongside the theoretical bound.
"""
import math
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

from ..stage1.stats import clopper_pearson_upper


def canonicalize_output(val: Any) -> str:
    """Canonicalize a value for voting."""
    if val is None:
        return "<EMPTY_OR_NONE>"
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, str):
        return " ".join(val.strip().split())
    if isinstance(val, dict):
        items = sorted((str(k), canonicalize_output(v)) for k, v in val.items())
        return "{" + ", ".join(f"{k}: {v}" for k, v in items) + "}"
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(canonicalize_output(x) for x in val) + "]"
    return str(val).strip()


def majority_vote(runs: List[Any], m: Optional[int] = None) -> Tuple[bool, Optional[Any], int, str]:
    """Execute majority vote over m quarantined outputs.

    Condition: > m/2 runs must agree on identical canonical value.
    Returns:
        (success, winning_value, count, reason)

    Cites: Slide 36.
    """
    total = m if m is not None else len(runs)
    if total <= 0 or len(runs) == 0:
        return False, None, 0, "No voter outputs provided."

    canonical_map: Dict[str, Any] = {}
    counts = Counter()

    for item in runs:
        c_key = canonicalize_output(item)
        if c_key not in canonical_map:
            canonical_map[c_key] = item
        counts[c_key] += 1

    most_common_key, max_count = counts.most_common(1)[0]
    required_majority = total / 2.0  # strict majority: count > total / 2

    if max_count > required_majority:
        return True, canonical_map[most_common_key], max_count, "Strict majority reached."
    else:
        return False, None, max_count, f"Majority vote failed: top count {max_count}/{total} <= {total}/2 (alarm raised)."


def hoeffding_vote_bound(m: int, r_bar_q: float) -> Union[float, str]:
    """Compute Hoeffding bound on probability that the vote is fooled.

    Formula: P(vote fooled) <= exp(-2 * m * (1/2 - R_bar_Q)^2) if R_bar_Q < 1/2, else 'no guarantee'.
    Cites: Slide 37.
    """
    if r_bar_q >= 0.5:
        return "no guarantee"
    margin = 0.5 - r_bar_q
    exponent = -2.0 * float(m) * (margin ** 2)
    return float(math.exp(exponent))


def evaluate_empirical_vote_fooling(
    attack_run_results: List[List[bool]],
    m: int,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Measure empirical vote-fooling rate per attack.

    Each item in attack_run_results is a list of booleans: True if the run succeeded in executing the injection.
    An attack fools the vote if > m/2 runs succeeded.
    Cites: Slide 36, Slide 37.
    """
    n_attacks = len(attack_run_results)
    if n_attacks == 0:
        return {"n_attacks": 0, "fooled_count": 0, "rate": 0.0, "cp_upper": 0.0}

    fooled_count = 0
    required_majority = m / 2.0

    for run_bools in attack_run_results:
        success_count = sum(1 for b in run_bools if b)
        if success_count > required_majority:
            fooled_count += 1

    rate = float(fooled_count) / float(n_attacks)
    cp_upper = clopper_pearson_upper(fooled_count, n_attacks, alpha)

    return {
        "n_attacks": n_attacks,
        "fooled_count": fooled_count,
        "empirical_rate": rate,
        "clopper_pearson_upper": cp_upper,
    }
