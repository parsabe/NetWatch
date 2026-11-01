"""Final Horizon value (Slide 42), solved as an SOCP with cvxpy (same pattern as V1).

V_Horizon = min_{π∈Π} min{1, Σ_θ π_θ q/|K_θ|
                             + max(E_P[L_π] + sqrt(2ρ Var_P(L_π)), Σ_θ π_θ ℓ̄^adp_θ)}

DECISION (pending author confirmation): Slide 42 writes
L_π(x) = Σ_θ π_θ (p̄^CP_G,θ + a p̄^CP_C,θ + h p̄^CP_F,θ), which does not depend on x,
so Var_P(L_π) = 0 and the DRO term vanishes. We therefore
  * compute Var_P from PER-ATTACK losses L_π(x) = Σ_θ π_θ ℓ_θ(x) (paired matrix
    attacks × configs, ℓ_θ(x) = G + aC + hF), and
  * use the Clopper–Pearson upper bound for the mean term:
    E-term = Σ_θ π_θ (p̄_G,θ + a p̄_C,θ + h p̄_F,θ).
Π = {π ≥ 0, Σπ = 1, Σ π_θ U_θ ≥ U_min, π_θ = 0 if leakage_θ > leakage_cap_bits}.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np

from horizon.stage1.sion import forging_vector
from horizon.stage2.starvation import relax_utility_floor


@dataclass
class HorizonResult:
    status: str
    value: float | None
    inner: float | None
    pi: dict[str, float] = field(default_factory=dict)
    terms: dict[str, float] = field(default_factory=dict)


def solve_v_horizon(L: np.ndarray, configs: list[str], mean_cp: np.ndarray, adp_bar: np.ndarray,
                    q: float, log_key_spaces: np.ndarray, rho: float, utility: np.ndarray,
                    U_min: float, leakage: np.ndarray, leakage_cap_bits: float,
                    solver: str) -> HorizonResult:
    """Slide 42: V_Horizon and π*_Horizon. L is the (n attacks × Θ) per-attack loss matrix."""
    L = np.asarray(L, float)
    n, T = L.shape
    f = forging_vector(q, log_key_spaces)
    Lc = L - L.mean(axis=0)[None, :]
    pi = cp.Variable(T, nonneg=True)
    t = cp.Variable()
    # the variance of the per-attack loss needs ≥ 2 attacks; with fewer, the DRO term is just the CP mean bound
    scale = math.sqrt(2.0 * rho / n) if n >= 2 else 0.0
    cons = [cp.sum(pi) == 1,
            np.asarray(utility, float) @ pi >= U_min,
            t >= np.asarray(mean_cp, float) @ pi + scale * cp.norm(Lc @ pi, 2),
            t >= np.asarray(adp_bar, float) @ pi]
    for j in range(T):
        if leakage[j] > leakage_cap_bits:
            cons.append(pi[j] == 0)
    prob = cp.Problem(cp.Minimize(f @ pi + t), cons)
    try:
        prob.solve(solver=solver)
    except cp.error.SolverError:
        prob.solve()
    if pi.value is None or prob.status not in ("optimal", "optimal_inaccurate"):
        return HorizonResult(prob.status, None, None)
    w = np.clip(pi.value, 0, None)
    w = w / w.sum()
    Lp = L @ w
    var = float(np.mean((Lp - Lp.mean()) ** 2)) if n >= 2 else 0.0
    mean_term = float(np.asarray(mean_cp) @ w)
    dro = mean_term + math.sqrt(2 * rho * var)
    adp = float(np.asarray(adp_bar) @ w)
    forge = float(f @ w)
    inner = forge + max(dro, adp)
    return HorizonResult(prob.status, min(1.0, inner), inner, dict(zip(configs, w.tolist())),
                         {"forging": forge, "mean_cp": mean_term, "var": var, "dro": dro, "adaptive": adp})


def solve_v_horizon_robust(L: np.ndarray, configs: list[str], mean_cp: np.ndarray, adp_bar: np.ndarray,
                           q: float, log_key_spaces: np.ndarray, rho: float, utility: np.ndarray,
                           U_min: float, leakage: np.ndarray, leakage_cap_bits: float,
                           solver: str) -> HorizonResult:
    """`solve_v_horizon` for measured (small, possibly incomplete) data: never crashes, every change is a note.

    Drops configurations whose loss column / bounds / utility are not finite, relaxes a utility floor that lies above
    the best admissible utility, and needs ≥ 1 attack (with fewer than 2 the variance term is 0). The SOCP itself is
    `solve_v_horizon` on the cleaned inputs.
    """
    L = np.asarray(L, float)
    if L.ndim != 2 or L.shape[0] == 0 or L.shape[1] == 0:
        return HorizonResult("no data", None, None, terms={"notes": ["empty per-attack loss matrix"]})
    arrs = {k: np.asarray(v, float) for k, v in dict(mean=mean_cp, adp=adp_bar, u=utility, leak=leakage,
                                                       logk=log_key_spaces).items()}
    cols = np.isfinite(L).all(axis=0) & np.isfinite(arrs["mean"]) & np.isfinite(arrs["adp"]) & np.isfinite(arrs["u"])
    notes = []
    if not cols.all():
        notes.append("configurations without complete data dropped: " +
                     ", ".join(configs[j] for j in np.nonzero(~cols)[0]))
    if not cols.any():
        return HorizonResult("no usable data", None, None, terms={"notes": notes})
    floor, n2 = relax_utility_floor(arrs["u"][cols], U_min, arrs["leak"][cols], leakage_cap_bits)
    notes += n2
    if floor is None:
        return HorizonResult("infeasible", None, None, terms={"notes": notes})
    res = solve_v_horizon(L[:, cols], [c for c, k in zip(configs, cols) if k], arrs["mean"][cols], arrs["adp"][cols],
                          q, arrs["logk"][cols], rho, arrs["u"][cols], floor, arrs["leak"][cols], leakage_cap_bits,
                          solver)
    res.terms = {**res.terms, "notes": notes}
    return res
