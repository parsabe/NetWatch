"""Stage 2 Sion Minimax Game (Level 2 LP Formulation).

Cites: Slide 41 (Allowed mixes and Sion's theorem).
Game:
    min_{pi in Pi} max_{w in W} pi^T ell_bar w
    Pi = {pi >= 0, sum pi = 1, utility >= U_min, leakage <= leakage_cap_bits}
    W = {w >= 0, sum w = 1}
Solves using scipy.optimize.linprog as an LP.
Outputs:
    pi_star_horizon: optimal configuration mix
    game_value: minimax loss value
    w_star: attacker's worst-case mix over attack styles
"""
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linprog


def solve_sion_v2_lp(
    loss_matrix: np.ndarray,
    config_names: List[str],
    attack_styles: List[str],
    utilities: Dict[str, float],
    leakages: Dict[str, float],
    u_min: float,
    leakage_cap_bits: float,
) -> Dict[str, Any]:
    """Solve Level 2 Sion minimax zero-sum game via Linear Programming.

    loss_matrix M has shape (n_styles, n_configs), where M[k, theta] is the loss.
    Cites: Slide 41.
    """
    n_styles, n_configs = loss_matrix.shape
    if len(config_names) != n_configs or len(attack_styles) != n_styles:
        raise ValueError("Dimensions of loss_matrix must match config_names and attack_styles.")

    # Decision variables x = [pi_0, pi_1, ..., pi_{C-1}, v]
    # Minimize v (the objective is [0, 0, ..., 0, 1])
    c = np.zeros(n_configs + 1)
    c[-1] = 1.0

    # Inequality constraints A_ub * x <= b_ub:
    # 1. For each style k: (M pi)_k <= v  =>  sum_j M[k, j] * pi_j - v <= 0
    # 2. Utility constraint: sum_j u_j * pi_j >= U_min  =>  - sum_j u_j * pi_j <= - U_min
    # 3. Leakage constraint: sum_j leak_j * pi_j <= leakage_cap_bits
    A_ub_list = []
    b_ub_list = []

    for k in range(n_styles):
        row = np.zeros(n_configs + 1)
        row[:n_configs] = loss_matrix[k, :]
        row[-1] = -1.0
        A_ub_list.append(row)
        b_ub_list.append(0.0)

    # Utility constraint
    u_row = np.zeros(n_configs + 1)
    u_row[:n_configs] = -np.array([utilities.get(cfg, 1.0) for cfg in config_names])
    A_ub_list.append(u_row)
    b_ub_list.append(-u_min)

    # Leakage constraint
    leak_row = np.zeros(n_configs + 1)
    leak_row[:n_configs] = np.array([leakages.get(cfg, 0.0) for cfg in config_names])
    A_ub_list.append(leak_row)
    b_ub_list.append(leakage_cap_bits)

    A_ub = np.array(A_ub_list)
    b_ub = np.array(b_ub_list)

    # Equality constraint sum(pi) == 1
    A_eq = np.zeros((1, n_configs + 1))
    A_eq[0, :n_configs] = 1.0
    b_eq = np.array([1.0])

    # Variable bounds: pi_j >= 0, v unbounded
    bounds = [(0.0, None) for _ in range(n_configs)] + [(None, None)]

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")

    if not res.success:
        raise RuntimeError(f"Sion LP failed to find optimal solution: {res.message}")

    pi_val = np.maximum(0.0, res.x[:n_configs])
    pi_val = pi_val / np.sum(pi_val)
    game_value = float(res.fun)

    # Extract dual multipliers for attacker mix w*
    # The first n_styles dual variables corresponding to the attack style constraints
    if hasattr(res, "ineqlin") and hasattr(res.ineqlin, "marginals"):
        dual_styles = np.maximum(0.0, res.ineqlin.marginals[:n_styles])
        sum_dual = np.sum(dual_styles)
        if sum_dual > 0:
            w_star_val = dual_styles / sum_dual
        else:
            w_star_val = np.ones(n_styles) / float(n_styles)
    else:
        w_star_val = np.ones(n_styles) / float(n_styles)

    pi_dict = {cfg: float(pi_val[i]) for i, cfg in enumerate(config_names)}
    w_dict = {sty: float(w_star_val[i]) for i, sty in enumerate(attack_styles)}

    return {
        "game_value": game_value,
        "pi_star_horizon": pi_dict,
        "w_star": w_dict,
        "status": "OPTIMAL",
    }
