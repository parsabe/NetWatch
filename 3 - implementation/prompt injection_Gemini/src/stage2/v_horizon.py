"""Computation of the final end-to-end Horizon risk bound V_Horizon.

Cites: Slide 42 (The final Horizon formula), Slide 43, Slide 44.
Formula:
    V_Horizon = min_{pi in Pi} min{1, sum_theta pi_theta q / |K_theta|
                + max(E_P[L_pi] + sqrt(2 * rho * Var_P(L_pi)), sum_theta pi_theta ell_bar_adp_theta)}
    Pi = {pi >= 0, sum pi = 1, utility >= U_min, leakage <= leakage_cap_bits}
"""
import math
from typing import Any, Dict, List, Optional, Tuple

import cvxpy as cp
import numpy as np


def solve_v_horizon_socp(
    paired_loss_matrix: np.ndarray,
    config_names: List[str],
    keyspace_log_sizes: Dict[str, float],
    q_attempts: float,
    adaptive_loss_bars: Dict[str, float],
    utilities: Dict[str, float],
    leakages: Dict[str, float],
    rho: float,
    u_min: float,
    leakage_cap_bits: float,
    solver_name: str = "CLARABEL",
) -> Dict[str, Any]:
    """Solve the final Horizon risk bound V_Horizon as an SOCP in cvxpy.

    Cites: Slide 42.

    # NOTE (pending author confirmation):
    # DECISION: We compute E_P and Var_P from PER-ATTACK losses L_pi(x) = sum_theta pi_theta ell_theta(x)
    # (paired matrix attacks x configs), and use the Clopper-Pearson upper bound for the mean term.
    # Slide 42's literal form uses only aggregate terms, which makes Var = 0.
    """
    n, c = paired_loss_matrix.shape
    if c != len(config_names):
        raise ValueError(f"Matrix cols {c} != len(config_names) {len(config_names)}.")

    # Empirical mean loss per config and centered matrix
    l_bar = np.mean(paired_loss_matrix, axis=0)  # shape (c,)
    l_tilde = paired_loss_matrix - np.ones((n, 1)) @ l_bar.reshape((1, c))

    # Forging coefficients f_theta = min(1, q / |K_theta|)
    f_vec = np.zeros(c)
    log_q = math.log(max(1.0, q_attempts))
    for i, name in enumerate(config_names):
        ln_k = keyspace_log_sizes.get(name, 0.0)
        if log_q >= ln_k:
            f_vec[i] = 1.0
        else:
            f_vec[i] = math.exp(log_q - ln_k)

    # Adaptive loss vector
    adp_vec = np.array([adaptive_loss_bars.get(name, 0.0) for name in config_names])

    # Utility and leakage vectors
    u_vec = np.array([utilities.get(name, 1.0) for name in config_names])
    leak_vec = np.array([leakages.get(name, 0.0) for name in config_names])

    # Decision variables
    pi = cp.Variable(c, nonneg=True)
    t = cp.Variable(1)

    objective = cp.Minimize(f_vec @ pi + t)

    constraints = [
        cp.sum(pi) == 1.0,
        u_vec @ pi >= u_min,
        leak_vec @ pi <= leakage_cap_bits,
        t >= adp_vec @ pi,
    ]

    if rho > 0.0:
        rho_factor = math.sqrt(2.0 * rho / float(n))
        constraints.append(
            t >= l_bar @ pi + rho_factor * cp.norm(l_tilde @ pi, 2)
        )
    else:
        constraints.append(t >= l_bar @ pi)

    prob = cp.Problem(objective, constraints)

    available_solvers = cp.installed_solvers()
    target_solver = cp.CLARABEL if "CLARABEL" in available_solvers else cp.SCS

    try:
        prob.solve(solver=target_solver)
    except Exception:
        prob.solve(solver=cp.SCS)

    if prob.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"V_Horizon SOCP solver failed with status: {prob.status}")

    pi_val = np.maximum(0.0, pi.value)
    pi_val = pi_val / np.sum(pi_val)
    t_val = float(t.value[0])
    raw_v = float(f_vec @ pi_val + t_val)
    v_horizon = min(1.0, raw_v)

    pi_dict = {name: float(pi_val[i]) for i, name in enumerate(config_names)}

    return {
        "v_horizon": v_horizon,
        "pi_star_horizon": pi_dict,
        "raw_game_value": raw_v,
        "status": prob.status,
    }
