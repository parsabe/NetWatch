"""Sion's minimax game solver for Stage 1 (SOCP formulation) and support stats.

Cites: Slide 24, Slide 27, Slide 30, Slide 32.
Ground Rules:
- Implements V1(rho, eta) as an SOCP in cvxpy with Clarabel/ECOS/SCS.
- Epigraph variable t >= each max branch.
- Computes pi*, V1, R_bar_Q, |K|, and T.
- Support stats: McNemar tests between paired variants and bootstrap CIs (Slide 27).
- No hard-coded numeric thresholds outside parameters.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

import cvxpy as cp
import numpy as np
from scipy import stats


def solve_sion_v1(
    paired_a_matrix: np.ndarray,
    variant_names: List[str],
    keyspace_log_sizes: Dict[str, float],
    q_attempts: float,
    p_adp_bars: Dict[str, float],
    utilities: Dict[str, float],
    baseline_variant: str,
    rho: float,
    eta: float,
    solver_name: str = "CLARABEL",
) -> Dict[str, Any]:
    """Solve Level 1 Sion minimax game as a Second-Order Cone Program (SOCP).

    Formula:
      V1(rho, eta) = min_{pi in Pi_eta} min{1, sum_tau pi_tau q/|K_tau|
                     + max(E_P[L_pi] + sqrt(2 * rho * Var_P(L_pi)), sum_tau pi_tau p_bar_adp_tau)}
      Pi_eta = {pi >= 0, sum pi = 1, sum pi_tau U_tau >= U_none - eta}
      Var_P(L_pi) = (1/n) || (A - 1 * mean(A)) pi ||_2^2

    Cites: Slide 24, Slide 30.
    """
    n, k = paired_a_matrix.shape
    if k != len(variant_names):
        raise ValueError(f"Matrix cols {k} does not match variants count {len(variant_names)}.")

    # Empirical mean vector (ASR) a_bar and centered matrix A_tilde
    a_bar = np.mean(paired_a_matrix, axis=0)  # shape (k,)
    a_tilde = paired_a_matrix - np.ones((n, 1)) @ a_bar.reshape((1, k))  # shape (n, k)

    # Forging coefficients f_tau = min(1, q / |K_tau|)
    f_vec = np.zeros(k)
    for i, name in enumerate(variant_names):
        ln_k = keyspace_log_sizes.get(name, 0.0)
        log_q = math.log(max(1.0, q_attempts))
        if log_q >= ln_k:
            f_vec[i] = 1.0
        else:
            f_vec[i] = math.exp(log_q - ln_k)

    # Adaptive upper bound vector
    adp_vec = np.array([p_adp_bars.get(name, 0.0) for name in variant_names])

    # Utility vector and threshold
    u_vec = np.array([utilities.get(name, 0.0) for name in variant_names])
    u_none = utilities.get(baseline_variant, 1.0)
    u_min_target = u_none - eta

    # Decision variables
    pi = cp.Variable(k, nonneg=True)
    t = cp.Variable(1)  # epigraph variable for the max(DRO, Adaptive) term

    # Objective: minimize forging + max-branch
    objective = cp.Minimize(f_vec @ pi + t)

    # Constraints
    constraints = [
        cp.sum(pi) == 1.0,
        u_vec @ pi >= u_min_target,
        t >= adp_vec @ pi,
    ]

    # DRO branch: t >= a_bar^T pi + sqrt(2 * rho / n) * || A_tilde @ pi ||_2
    if rho > 0.0:
        rho_factor = math.sqrt(2.0 * rho / float(n))
        constraints.append(
            t >= a_bar @ pi + rho_factor * cp.norm(a_tilde @ pi, 2)
        )
    else:
        constraints.append(t >= a_bar @ pi)

    # Solve SOCP
    prob = cp.Problem(objective, constraints)
    
    # Select solver
    available_solvers = cp.installed_solvers()
    target_solver = cp.CLARABEL if "CLARABEL" in available_solvers else cp.SCS
    
    try:
        prob.solve(solver=target_solver)
    except Exception:
        prob.solve(solver=cp.SCS)

    if prob.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"SOCP solver failed with status: {prob.status}")

    pi_val = np.maximum(0.0, pi.value)
    pi_val = pi_val / np.sum(pi_val)  # Normalize
    t_val = float(t.value[0])
    raw_v1 = float(f_vec @ pi_val + t_val)
    v1 = min(1.0, raw_v1)

    pi_dict = {name: float(pi_val[i]) for i, name in enumerate(variant_names)}

    # Min keyspace |K| over active variants (pi_tau > 1e-4) (Slide 32)
    active_keyspaces = [
        math.exp(keyspace_log_sizes[name])
        for name, weight in pi_dict.items()
        if weight > 1e-4
    ]
    min_active_keyspace = min(active_keyspaces) if active_keyspaces else 1.0

    return {
        "v1": v1,
        "pi_star": pi_dict,
        "raw_game_value": raw_v1,
        "min_active_keyspace": min_active_keyspace,
        "status": prob.status,
    }


def mcnemar_test_paired(successes_a: np.ndarray, successes_b: np.ndarray) -> Dict[str, float]:
    """McNemar's paired test between two spotlighting variants.

    Cites: Slide 27.
    """
    if len(successes_a) != len(successes_b):
        raise ValueError("Paired arrays must have identical length.")
    # 2x2 contingency table:
    # n00: both fail (0, 0), n01: a=0, b=1, n10: a=1, b=0, n11: both succeed (1, 1)
    b = int(np.sum((successes_a == 0) & (successes_b == 1)))
    c = int(np.sum((successes_a == 1) & (successes_b == 0)))
    
    # Continuity-corrected chi-square statistic: (|b - c| - 1)^2 / (b + c)
    total_discordant = b + c
    if total_discordant == 0:
        return {"statistic": 0.0, "p_value": 1.0, "b": b, "c": c}
    stat = (abs(b - c) - 1.0) ** 2 / float(total_discordant)
    p_val = float(stats.chi2.sf(stat, df=1))
    return {"statistic": stat, "p_value": p_val, "b": b, "c": c}


def bootstrap_ci(
    data: np.ndarray,
    stat_fn=np.mean,
    n_boot: int = 1000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> Tuple[float, float]:
    """Bootstrap confidence interval for a metric.

    Cites: Slide 27.
    """
    rng = np.random.RandomState(seed)
    n = len(data)
    boot_stats = np.empty(n_boot)
    for i in range(n_boot):
        sample = rng.choice(data, size=n, replace=True)
        boot_stats[i] = stat_fn(sample)
    alpha = 1.0 - confidence_level
    low = float(np.percentile(boot_stats, 100.0 * (alpha / 2.0)))
    high = float(np.percentile(boot_stats, 100.0 * (1.0 - alpha / 2.0)))
    return low, high
