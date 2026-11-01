"""Conformal risk control on the confirmation threshold λ (Slide 39; Angelopoulos et al., ICLR 2024).

Semantics (author decision): for a CONFIRM-class call with score
s(x) = vote agreement fraction ∈ [0,1],
    s(x) ≥ λ  → ask the user;   s(x) < λ → DENY.
Loss of calibration call i:  L_i(λ) = a · 1[dangerous_i ∧ s_i ≥ λ] ∈ [0,1],
non-increasing in λ (a = user_mistake_rate). Then
    λ̂ = smallest λ with (n/(n+1)) L̂_n(λ) + B/(n+1) ≤ α_user,   B = sup L = a (B = 1 is valid but loose),
guarantees E[L_{n+1}(λ̂)] = P(user mistakenly approves a dangerous call) ≤ α_user
under exchangeability.
Calibration labels: benign = AgentDojo user-task tool calls, dangerous =
injection-task tool calls (ground truth).

Small-sample behaviour (what to expect when the calibration set is starved)
---------------------------------------------------------------------------
* The result always says WHY: `status` is one of
    ok               a threshold λ̂ > grid[0] was certified
    unconstrained    asking about every CONFIRM call (λ̂ = grid[0]) already meets α_user: the data do not need a filter
    no_data          no CONFIRM event was recorded → nothing is known → λ̂ = +inf (never ask, DENY: fail-closed)
    infeasible_data  no λ meets α_user because n is too small (B/(n+1) > α_user): collect more calibration calls
    infeasible_risk  n is large enough, but dangerous calls score too high to be separated → λ̂ = +inf (fail-closed)
* Non-finite scores are dropped (a vote that never ran has no score).
* Group-conditional CRC (`calibrate_lambda_groups`): only groups with at least `min_group` calls get their own
  bound. The max over groups amplifies noise, so a handful of calls in a rare attack style used to push λ̂ to +inf
  for EVERY style; groups below `min_group` are now covered by the pooled bound instead.
* `alpha_user / a` is the share of dangerous-and-asked calls the guarantee tolerates. Above ~0.25 the guarantee is
  weak (reported as a note): with a = 0.1 and α_user = 0.05, half of all calls may reach the user.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

LOSS_BOUND = 1.0   # B: losses lie in [0, B]
WEAK_GUARANTEE_SHARE = 0.25     # α_user / a above this: the guarantee barely constrains λ


def crc_losses(scores: np.ndarray, dangerous: np.ndarray, lam: float, a: float) -> np.ndarray:
    """L_i(λ) = a·1[dangerous_i ∧ s_i ≥ λ]."""
    return a * ((np.asarray(dangerous, bool)) & (np.asarray(scores) >= lam)).astype(float)


def min_calibration_size(alpha_user: float, loss_bound: float) -> int:
    """Smallest n for which even a perfect threshold (no dangerous call accepted, L̂ = 0) satisfies the CRC
    inequality: B/(n+1) ≤ α_user  ⇔  n ≥ B/α_user − 1. Below this no λ can be certified, whatever the data say."""
    return max(1, math.ceil(loss_bound / alpha_user - 1.0 - 1e-12))


@dataclass
class CRCResult:
    lambda_hat: float          # +inf → no certified λ: never ask, always DENY (fail-closed)
    n: int
    risk_curve: np.ndarray     # L̂_n(λ) on the grid
    bound_curve: np.ndarray    # (n/(n+1)) L̂_n(λ) + B/(n+1)
    grid: np.ndarray
    status: str = "ok"         # ok | unconstrained | no_data | infeasible_data | infeasible_risk
    n_dangerous: int = 0
    n_needed: int = 0          # min_calibration_size(α_user, B)


def calibrate_lambda(scores, dangerous, a: float, alpha_user: float, grid: np.ndarray,
                     loss_bound: float = LOSS_BOUND) -> CRCResult:
    """Slide 39: λ̂ = inf{λ : (n/(n+1)) L̂_n(λ) + B/(n+1) ≤ α_user}; B = `loss_bound` ≥ sup L_i."""
    scores = np.asarray(scores, float)
    dangerous = np.asarray(dangerous, bool)
    keep = np.isfinite(scores)                       # a call whose vote never ran has no score
    scores, dangerous = scores[keep], dangerous[keep]
    n = len(scores)
    grid = np.sort(np.asarray(grid, float))
    risk = np.array([crc_losses(scores, dangerous, lam, a).mean() if n else 0.0 for lam in grid])
    bound = (n / (n + 1)) * risk + loss_bound / (n + 1)
    feasible = np.nonzero(bound <= alpha_user)[0]
    n_needed = min_calibration_size(alpha_user, loss_bound)
    n_dang = int(dangerous.sum())
    if n == 0:                                       # nothing observed: B ≤ α_user would "certify" any λ from no data
        return CRCResult(float("inf"), 0, risk, bound, grid, "no_data", 0, n_needed)
    if not feasible.size:
        status = "infeasible_data" if n < n_needed else "infeasible_risk"
        return CRCResult(float("inf"), n, risk, bound, grid, status, n_dang, n_needed)
    status = "unconstrained" if feasible[0] == 0 else "ok"
    return CRCResult(float(grid[feasible[0]]), n, risk, bound, grid, status, n_dang, n_needed)


def escalate(score: float, lambda_hat: float) -> bool:
    """CONFIRM path: ask the user iff s(x) ≥ λ̂, otherwise DENY."""
    return score >= lambda_hat


def guarantee_note(a: float, alpha_user: float) -> str:
    """Plain-words strength of the guarantee; empty when it is reasonably strong."""
    share = alpha_user / a if a > 0 else float("inf")
    if share >= 1.0:
        return (f"vacuous: α_user = {alpha_user:g} ≥ a = {a:g}, so every λ meets the target; lower α_user "
                f"(a user who errs with probability a can never exceed risk a)")
    if share > WEAK_GUARANTEE_SHARE:
        return (f"weak: α_user/a = {share:.2f}, the guarantee only limits P(dangerous call reaches the user) to "
                f"{share:.0%}; lower α_user for a stronger guarantee")
    return ""


def _summary(r: CRCResult) -> dict:
    return {"lambda_hat": r.lambda_hat, "n": r.n, "n_dangerous": r.n_dangerous, "status": r.status}


def calibrate_lambda_groups(scores, dangerous, groups, a: float, alpha_user: float, grid: np.ndarray,
                            loss_bound: float, min_group: int) -> dict:
    """Group-conditional CRC: λ̂_g per group (e.g. attack style), λ̂ = max_g λ̂_g (and ≥ the pooled λ̂).

    Since every L_i(λ) is non-increasing in λ, the largest λ̂_g satisfies the CRC inequality in EVERY group, so
    the guarantee holds per style (not only on the pooled average) — robust to a shift in the style mix.

    Groups with fewer than `min_group` calls do NOT get a bound of their own: with so few calls the finite-sample
    term B/(n_g+1) dominates and the max over groups would let a few noisy calls set λ̂ for every style. They are
    covered by the pooled calibration over all calls instead (reported under "pooled_small_groups"), so the
    per-style guarantee holds for styles with ≥ min_group calls and the average guarantee for the rest.
    """
    scores, dangerous, groups = np.asarray(scores, float), np.asarray(dangerous, bool), np.asarray(groups, str)
    keep = np.isfinite(scores)
    scores, dangerous, groups = scores[keep], dangerous[keep], groups[keep]
    pooled = calibrate_lambda(scores, dangerous, a, alpha_user, grid, loss_bound)
    vals, counts = np.unique(groups, return_counts=True)
    small = [str(v) for v, c in zip(vals, counts) if c < min_group]
    per = {str(k): calibrate_lambda(scores[groups == k], dangerous[groups == k], a, alpha_user, grid, loss_bound)
           for k, c in zip(vals, counts) if c >= min_group}
    if small:
        is_small = np.isin(groups, small)
        union = calibrate_lambda(scores[is_small], dangerous[is_small], a, alpha_user, grid, loss_bound)
        per["pooled_small_groups"] = union if union.n >= min_group else pooled      # too few → the overall pooled bound
    lam = max([pooled.lambda_hat, *[r.lambda_hat for r in per.values()]])
    return {"lambda_hat": lam, "per_group": {k: _summary(r) for k, r in per.items()}, "pooled": _summary(pooled),
            "small_groups": small, "min_group": int(min_group)}
