"""Statistical functions and robust risk bounds for Stage 1.

Cites: Slides 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23.
Ground Rules:
- Implements §5 exact mathematics.
- No numeric values hard-coded; all parameters passed as arguments.
- Every function docstring cites its slide number.
"""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import beta


def forging_probability(keyspace_log_size: float, q_attempts: float) -> Tuple[float, float]:
    """Step 1: Compute P_forge <= q / |K_tau| in linear and log10 space.

    Cites: Slide 13, Slide 14, Slide 23.
    Args:
        keyspace_log_size: Natural logarithm of keyspace size ln(|K_tau|).
        q_attempts: Number of attacker attempts q (float or int).
    Returns:
        (p_forge_linear, p_forge_log10)
    """
    if q_attempts <= 0:
        return 0.0, float("-inf")
    log_q = math.log(q_attempts)
    log_ratio = log_q - keyspace_log_size
    if log_ratio >= 0.0:
        return 1.0, 0.0
    p_forge_log10 = log_ratio / math.log(10.0)
    p_forge_linear = math.exp(log_ratio)
    return p_forge_linear, p_forge_log10


def empirical_asr(successes: int, total_trials: int) -> float:
    """Step 2a: Empirical Attack Success Rate p_hat = s / n.

    Cites: Slide 15.
    """
    if total_trials <= 0:
        return 0.0
    return float(successes) / float(total_trials)


def clopper_pearson_upper(successes: int, total_trials: int, alpha: float) -> float:
    """Step 2b: One-sided Clopper-Pearson (1 - alpha) upper bound p_bar.

    Formula: Beta^{-1}(1 - alpha; s + 1, n - s).
    If s = n -> 1.0. If s = 0 -> 1 - alpha^{1/n}.
    Cites: Slide 16, Slide 23.
    """
    if total_trials <= 0:
        return 1.0
    if successes >= total_trials:
        return 1.0
    if successes == 0:
        return float(1.0 - alpha ** (1.0 / total_trials))
    return float(beta.ppf(1.0 - alpha, successes + 1, total_trials - successes))


def dro_worst_case(p: float, rho: float) -> float:
    """Step 2c: Closed-form Chi-square Distributionally Robust Optimization bound.

    Formula: g_rho(p) = p + sqrt(2 * rho * p * (1 - p)) if rho <= (1 - p) / (2 * p) else 1.0.
    With boundary conditions: g_rho(0) = 0.0.
    Cites: Slide 19, Slide 23.
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    if rho <= 0.0:
        return float(p)
    saturation_threshold = (1.0 - p) / (2.0 * p)
    if rho > saturation_threshold:
        return 1.0
    return float(min(1.0, p + math.sqrt(2.0 * rho * p * (1.0 - p))))


def combined_risk(
    keyspace_log_size: float,
    q_attempts: float,
    p_bar: float,
    p_adp_bar: float,
    rho: float,
) -> float:
    """Step 4: Combined risk formula.

    Formula: R_bar_tau(rho) = min{1, q / |K_tau| + max(g_rho(p_bar), p_adp_bar)}.
    Cites: Slide 21, Slide 23.
    """
    p_forge, _ = forging_probability(keyspace_log_size, q_attempts)
    dro_term = dro_worst_case(p_bar, rho)
    max_attack_term = max(dro_term, p_adp_bar)
    return float(min(1.0, p_forge + max_attack_term))


def choose_defense(
    variant_results: Dict[str, Dict[str, float]],
    baseline_variant: str,
    eta: float,
) -> Optional[str]:
    """Step 5: Defense choice rule tau* = argmin R_bar_tau(rho) s.t. U_tau >= U_none - eta.

    Cites: Slide 22.
    """
    if baseline_variant not in variant_results:
        raise ValueError(f"Baseline variant '{baseline_variant}' not found in results.")
    u_baseline = variant_results[baseline_variant]["utility"]
    threshold = u_baseline - eta

    feasible = {
        name: res["risk"]
        for name, res in variant_results.items()
        if res["utility"] >= threshold
    }
    if not feasible:
        return None
    return min(feasible, key=lambda k: feasible[k])
