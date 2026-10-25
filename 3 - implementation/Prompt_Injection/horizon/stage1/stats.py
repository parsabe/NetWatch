"""Stage 1 math (Slides 13-24, 27). Implemented exactly as specified; no tuning.

All probabilities that can be astronomically small (forging) are computed in
log-space and also returned as log10.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps


# ---------------------------------------------------------------- Step 1 ----
@dataclass
class Forging:
    value: float     # q/|K_τ| as a float (underflows to 0.0 for huge |K_τ|)
    log10: float     # log10(q/|K_τ|), exact in log-space


def forging_bound(q: float, log_key_space: float) -> Forging:
    """Slide 13: P_forge ≤ q / |K_τ| (union bound over q attacker attempts).

    `log_key_space` is ln|K_τ| (e.g. math.lgamma(|Σ|+1) for |Σ|!), so 26! and
    larger key spaces never overflow.
    """
    if q <= 0:
        return Forging(0.0, -math.inf)
    ln = math.log(q) - log_key_space
    return Forging(math.exp(ln) if ln < 700 else math.inf, ln / math.log(10))


# --------------------------------------------------------------- Step 2a ----
def asr(outcomes) -> float:
    """Slide 15: p̂_τ = (1/n) Σ_i A(δ_i, κ_i)."""
    a = np.asarray(outcomes, dtype=float)
    return float(a.mean()) if a.size else float("nan")


# --------------------------------------------------------------- Step 2b ----
def cp_upper(s: int, n: int, alpha: float) -> float:
    """Slide 16: one-sided Clopper–Pearson upper bound p̄ = Beta⁻¹(1−α; s+1, n−s).

    If s = n the bound is 1. For s = 0 this equals 1 − α^(1/n) (≈ 3/n at
    α = 0.05, the rule of three).
    """
    if n <= 0:
        return 1.0
    if s >= n:
        return 1.0
    return float(sps.beta.ppf(1.0 - alpha, s + 1, n - s))


def cp_interval(s: int, n: int, alpha: float) -> tuple[float, float]:
    """Two-sided Clopper–Pearson interval (error bars in figures)."""
    lo = 0.0 if s == 0 else float(sps.beta.ppf(alpha / 2, s, n - s + 1))
    hi = 1.0 if s == n else float(sps.beta.ppf(1 - alpha / 2, s + 1, n - s))
    return lo, hi


# --------------------------------------------------------------- Step 2c ----
def dro_saturation_rho(p: float) -> float:
    """Slide 19: validity limit ρ ≤ (1−p)/(2p); beyond it the bound is 1."""
    return math.inf if p <= 0 else (1.0 - p) / (2.0 * p)


def dro_chi2(p: float, rho: float) -> float:
    """Slide 19: g_ρ(p) = p + sqrt(2ρ p(1−p)) for ρ ≤ (1−p)/(2p), else 1.

    Worst case of E_Q[A] over Q with χ²-divergence ½∫(dQ/dP−1)² dP ≤ ρ (Slide 18)
    for a binary A with mean p under P. g_ρ(0) = 0.
    """
    if p <= 0.0:
        return 0.0
    if rho > dro_saturation_rho(p):
        return 1.0
    return float(p + math.sqrt(2.0 * rho * p * (1.0 - p)))


# ---------------------------------------------------------------- Step 4 ----
@dataclass
class CombinedRisk:
    total: float
    forging: float
    dro: float
    adaptive: float


def combined_risk(q: float, log_key_space: float, p_bar: float, p_adp: float, rho: float) -> CombinedRisk:
    """Slide 21: R̄_τ(ρ) = min{1, q/|K_τ| + max(g_ρ(p̄_τ), p̄^adp_τ)}."""
    f = forging_bound(q, log_key_space).value
    g = dro_chi2(p_bar, rho)
    return CombinedRisk(min(1.0, f + max(g, p_adp)), f, g, p_adp)


# ---------------------------------------------------------------- Step 5 ----
def choose_tau(risk: dict[str, float], utility: dict[str, float], u_none: float, eta: float) -> str | None:
    """Slide 22: τ* = argmin_τ R̄_τ(ρ)  s.t.  U_τ ≥ U_none − η. None if infeasible."""
    feasible = [t for t in risk if utility[t] >= u_none - eta]
    if not feasible:
        return None
    return min(feasible, key=lambda t: (risk[t], t))


# ------------------------------------------------------- Support (Slide 27) --
def mcnemar_pvalue(a: np.ndarray, b: np.ndarray) -> float:
    """Slide 27: exact McNemar test on paired binary outcomes of two variants."""
    from statsmodels.stats.contingency_tables import mcnemar
    a = np.asarray(a, dtype=int)
    b = np.asarray(b, dtype=int)
    table = [[int(np.sum((a == 1) & (b == 1))), int(np.sum((a == 1) & (b == 0)))],
             [int(np.sum((a == 0) & (b == 1))), int(np.sum((a == 0) & (b == 0)))]]
    if table[0][1] + table[1][0] == 0:
        return 1.0
    return float(mcnemar(table, exact=True).pvalue)


def bootstrap_ci(x, alpha: float, resamples: int, seed: int, stat=np.mean) -> tuple[float, float]:
    """Slide 27: percentile bootstrap CI for a statistic of paired outcomes."""
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(resamples, len(x)))
    vals = np.apply_along_axis(stat, 1, x[idx])
    return float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))
