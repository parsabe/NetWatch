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
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LOSS_BOUND = 1.0   # B: losses lie in [0, B]


def crc_losses(scores: np.ndarray, dangerous: np.ndarray, lam: float, a: float) -> np.ndarray:
    """L_i(λ) = a·1[dangerous_i ∧ s_i ≥ λ]."""
    return a * ((np.asarray(dangerous, bool)) & (np.asarray(scores) >= lam)).astype(float)


@dataclass
class CRCResult:
    lambda_hat: float          # +inf → no feasible λ in the grid: never ask, always DENY
    n: int
    risk_curve: np.ndarray     # L̂_n(λ) on the grid
    bound_curve: np.ndarray    # (n/(n+1)) L̂_n(λ) + B/(n+1)
    grid: np.ndarray


def calibrate_lambda(scores, dangerous, a: float, alpha_user: float, grid: np.ndarray,
                     loss_bound: float = LOSS_BOUND) -> CRCResult:
    """Slide 39: λ̂ = inf{λ : (n/(n+1)) L̂_n(λ) + B/(n+1) ≤ α_user}; B = `loss_bound` ≥ sup L_i."""
    scores = np.asarray(scores, float)
    dangerous = np.asarray(dangerous, bool)
    n = len(scores)
    grid = np.sort(np.asarray(grid, float))
    risk = np.array([crc_losses(scores, dangerous, lam, a).mean() if n else 0.0 for lam in grid])
    bound = (n / (n + 1)) * risk + loss_bound / (n + 1)
    feasible = np.nonzero(bound <= alpha_user)[0]
    lam_hat = float(grid[feasible[0]]) if feasible.size else float("inf")
    return CRCResult(lam_hat, n, risk, bound, grid)


def escalate(score: float, lambda_hat: float) -> bool:
    """CONFIRM path: ask the user iff s(x) ≥ λ̂, otherwise DENY."""
    return score >= lambda_hat


def calibrate_lambda_groups(scores, dangerous, groups, a: float, alpha_user: float, grid: np.ndarray,
                            loss_bound: float, min_group: int) -> dict:
    """Group-conditional CRC: λ̂_g per group (e.g. attack style), λ̂ = max_g λ̂_g.

    Since every L_i(λ) is non-increasing in λ, the largest λ̂_g satisfies the CRC inequality in EVERY group, so
    the guarantee holds per style (not only on the pooled average) — robust to a shift in the style mix.
    Groups smaller than `min_group` are pooled into one group.
    """
    scores, dangerous, groups = np.asarray(scores, float), np.asarray(dangerous, bool), np.asarray(groups, str)
    vals, counts = np.unique(groups, return_counts=True)
    small = set(vals[counts < min_group])
    g = np.where(np.isin(groups, list(small)), "pooled_small_groups", groups)
    per = {k: calibrate_lambda(scores[g == k], dangerous[g == k], a, alpha_user, grid, loss_bound)
           for k in np.unique(g)}
    lam = max((r.lambda_hat for r in per.values()), default=float("inf"))
    return {"lambda_hat": lam, "per_group": {k: {"lambda_hat": r.lambda_hat, "n": r.n} for k, r in per.items()}}
