"""Sion game, Level 1 (Slides 24, 30) and the Stage 1 → Stage 2 hand-off (Slide 32).

V1(ρ,η) = min_{π∈Π_η} min{1, Σ_τ π_τ q/|K_τ|
                            + max(E_P[L_π] + sqrt(2ρ Var_P(L_π)), Σ_τ π_τ p̄^adp_τ)}
L_π(x) = Σ_τ π_τ A_τ(x),   Π_η = {π ≥ 0, Σπ = 1, Σ π_τ U_τ ≥ U_none − η}

With the paired matrix A[x, τ] (n attacks × T variants):
  E_P[L_π]  = mean(A) π
  Var_P(L_π) = (1/n) ‖(A − 1·mean(A)) π‖²
so sqrt(2ρ Var_P) = sqrt(2ρ/n) ‖(A − 1·mean(A)) π‖₂ is a second-order cone term.
The outer min{1,·} is monotone, so we minimize the inner expression as an SOCP
(epigraph variable t ≥ each branch of the max) and cap at 1 afterwards.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np

from horizon.stage1.stats import forging_bound


@dataclass
class SionResult:
    status: str
    value: float | None               # V1 (capped at 1)
    inner: float | None               # uncapped objective
    pi: dict[str, float] = field(default_factory=dict)
    terms: dict[str, float] = field(default_factory=dict)


def forging_vector(q: float, log_key_spaces: np.ndarray) -> np.ndarray:
    return np.array([forging_bound(q, lk).value for lk in log_key_spaces])


def solve_v1(A: np.ndarray, names: list[str], U: np.ndarray, u_none: float, eta: float,
             q: float, log_key_spaces: np.ndarray, p_adp: np.ndarray, rho: float,
             solver: str, mean_bound: np.ndarray | None = None) -> SionResult:
    """Slide 24: V1(ρ,η) and π* as an SOCP (cvxpy).

    `mean_bound` (optional, per variant) replaces the empirical mean in the DRO term by a finite-sample upper
    bound (Clopper–Pearson p̄_τ), the same convention as V_Horizon (Slide 42); the variance term is unchanged.
    """
    A = np.asarray(A, dtype=float)
    n, T = A.shape
    if n == 0:                                            # nothing observed: no estimate, no game
        return SionResult("no data", None, None)
    f = forging_vector(q, log_key_spaces)
    mu = A.mean(axis=0)
    Ac = A - mu[None, :]
    mb = mu if mean_bound is None else np.asarray(mean_bound, dtype=float)
    # the variance of the per-attack loss needs ≥ 2 attacks; with fewer the DRO term is just the mean bound
    scale = math.sqrt(2.0 * rho / n) if n >= 2 else 0.0

    pi = cp.Variable(T, nonneg=True)
    t = cp.Variable()
    cons = [cp.sum(pi) == 1,
            U @ pi >= u_none - eta,
            t >= mb @ pi + scale * cp.norm(Ac @ pi, 2),
            t >= p_adp @ pi]
    prob = cp.Problem(cp.Minimize(f @ pi + t), cons)
    try:
        prob.solve(solver=solver)
    except cp.error.SolverError:
        prob.solve()
    if pi.value is None or prob.status not in ("optimal", "optimal_inaccurate"):
        return SionResult(prob.status, None, None)

    w = np.clip(pi.value, 0.0, None)
    w = w / w.sum()
    L = A @ w
    mean_term = float(mb @ w)
    var_term = float(np.mean((L - L.mean()) ** 2)) if n >= 2 else 0.0
    dro_term = mean_term + math.sqrt(2.0 * rho * var_term)
    adp_term = float(p_adp @ w)
    forge_term = float(f @ w)
    inner = forge_term + max(dro_term, adp_term)
    return SionResult(prob.status, min(1.0, inner), inner,
                      dict(zip(names, w.tolist())),
                      {"forging": forge_term, "mean": mean_term, "var": var_term,
                       "dro": dro_term, "adaptive": adp_term})


def evaluate_v1_objective(A: np.ndarray, pi: np.ndarray, q: float, log_key_spaces: np.ndarray,
                          p_adp: np.ndarray, rho: float, mean_bound: np.ndarray | None = None) -> SionResult:
    """Slide 24 objective at a FIXED π (no optimization).

    Used on the report split with π* chosen on the fit split, so the reported value is
    never computed on the split used to choose it.
    """
    A = np.asarray(A, dtype=float)
    w = np.asarray(pi, dtype=float)
    L = A @ w
    mean_term = float(L.mean()) if mean_bound is None else float(np.asarray(mean_bound, dtype=float) @ w)
    var_term = float(np.mean((L - L.mean()) ** 2)) if len(L) >= 2 else 0.0
    dro_term = mean_term + math.sqrt(2.0 * rho * var_term)
    adp_term = float(np.asarray(p_adp) @ w)
    forge_term = float(forging_vector(q, log_key_spaces) @ w)
    inner = forge_term + max(dro_term, adp_term)
    return SionResult("evaluated", min(1.0, inner), inner, {},
                      {"forging": forge_term, "mean": mean_term, "var": var_term, "dro": dro_term,
                       "adaptive": adp_term})


@dataclass
class Stage1Output:
    """What Stage 1 passes to Stage 2 (Slide 32)."""
    pi_star: dict[str, float]
    R_Q: float                 # R̄_Q = Σ π*_τ R̄_τ(ρ)
    log_K: float               # ln|K| with |K| = min |K_τ| over the support of π*
    tools: list[str]           # T: tools the P-LLM can schedule
    V1: float
    rho: float
    eta: float
    q: float


def stage1_output(res: SionResult, risk_per_variant: dict[str, float],
                  log_key_space: dict[str, float], tools: list[str],
                  support_tol: float, rho: float, eta: float, q: float) -> Stage1Output:
    """Slide 32: π*, R̄_Q = Σ π*_τ R̄_τ(ρ), |K| = min_{τ: π*_τ>0} |K_τ|, T."""
    support = [t for t, p in res.pi.items() if p > support_tol]
    r_q = sum(res.pi[t] * risk_per_variant[t] for t in support)
    log_k = min(log_key_space[t] for t in support)
    return Stage1Output({t: res.pi[t] for t in support}, r_q, log_k, tools,
                        res.value, rho, eta, q)
