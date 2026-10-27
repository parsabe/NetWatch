"""Sion game, Level 2 (Slide 41), solved as a linear program.

    min_{π∈Π} max_{w∈W} πᵀ ℓ̄ w      ℓ̄[k, θ] = Clopper–Pearson upper bound of the loss
                                     of attack style k under configuration θ
    Π = {π ≥ 0, Σπ = 1, Σ_θ π_θ U_θ ≥ U_min, π_θ = 0 if leakage_θ > leakage_cap_bits}
    W = simplex over attack styles k

(Leakage enters Π as a hard per-configuration cap: author decision.)
Because max over the simplex W is attained at a vertex, the LP is
    min_{π,v} v   s.t.  Σ_θ ℓ̄[k,θ] π_θ ≤ v  ∀k,  π ∈ Π.
The attacker's worst-case mix w* is the dual of the k-constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog


@dataclass
class LPResult:
    status: str
    value: float | None
    pi: dict[str, float] = field(default_factory=dict)
    w: dict[str, float] = field(default_factory=dict)


def solve_sion_lp(loss_bar: np.ndarray, styles: list[str], configs: list[str],
                  utility: np.ndarray, U_min: float, leakage: np.ndarray,
                  leakage_cap_bits: float) -> LPResult:
    """Slide 41: π*_Horizon, the game value, and w* (attacker's worst-case style mix)."""
    L = np.asarray(loss_bar, float)            # K × Θ
    K, T = L.shape
    c = np.zeros(T + 1)
    c[-1] = 1.0                                 # minimize v
    A_ub = np.hstack([L, -np.ones((K, 1))])     # Σ_θ ℓ̄[k,θ] π_θ − v ≤ 0
    b_ub = np.zeros(K)
    A_ub = np.vstack([A_ub, np.append(-np.asarray(utility, float), 0.0)])   # Σ π U ≥ U_min
    b_ub = np.append(b_ub, -U_min)
    A_eq = np.append(np.ones(T), 0.0)[None, :]
    bounds = [(0.0, 0.0) if leakage[j] > leakage_cap_bits else (0.0, None) for j in range(T)]
    bounds.append((None, None))
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=[1.0], bounds=bounds, method="highs")
    if res.status != 0:
        return LPResult(res.message, None)
    pi = res.x[:T]
    duals = -np.asarray(res.ineqlin.marginals[:K])
    w = duals / duals.sum() if duals.sum() > 0 else np.full(K, 1.0 / K)
    return LPResult("optimal", float(res.x[-1]), dict(zip(configs, pi.tolist())), dict(zip(styles, w.tolist())))
