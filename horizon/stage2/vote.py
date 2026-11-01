"""Majority vote over m quarantined runs (Slide 35) and its Hoeffding bound (Slide 36)."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class VoteResult:
    accepted: bool
    value: Any               # representative of the winning class (None if refused)
    canonical: str | None
    agreement: float         # (size of largest class) / m  — the score s(x)
    counts: dict[str, int]
    m: int


def majority_vote(outputs: list[tuple[str, Any]], m: int) -> VoteResult:
    """Slide 35: y* = Majority(y_1..y_m); accept only if MORE than m/2 runs agree.

    `outputs` holds (canonical_form, value) for runs that parsed; runs that failed
    to parse count toward m but never toward a class. Ties / no majority → refusal.
    """
    counts = Counter(c for c, _ in outputs)
    if not counts:
        return VoteResult(False, None, None, 0.0, {}, m)
    top, n_top = counts.most_common(1)[0]
    agreement = n_top / m
    if n_top * 2 > m:
        rep = next(v for c, v in outputs if c == top)
        return VoteResult(True, rep, top, agreement, dict(counts), m)
    return VoteResult(False, None, None, agreement, dict(counts), m)


def hoeffding_vote_bound(m: int, r_q: float):
    """Slide 36: P(vote fooled) ≤ exp(−2m(½ − R̄_Q)²) for R̄_Q < ½; otherwise no guarantee.

    Assumes independent runs with per-run failure ≤ R̄_Q. Runs on the same text are
    correlated, so always report `empirical_vote_fooling` next to this bound.
    """
    half = 0.5
    if r_q >= half:
        return "no guarantee"
    return math.exp(-2.0 * m * (half - r_q) ** 2)


def empirical_vote_fooling(run_success: np.ndarray, m: int) -> np.ndarray:
    """Per attack: was the vote fooled, i.e. did > m/2 of its m runs execute the attack?

    `run_success` is (n_attacks, ≥m) binary; uses the first m runs of each attack.
    """
    s = np.asarray(run_success)[:, :m].sum(axis=1)
    return (2 * s > m).astype(int)


def per_attack_failure_rates(run_success: np.ndarray) -> np.ndarray:
    """p(x): per-attack single-run failure rate across independent keys (F10)."""
    return np.asarray(run_success, dtype=float).mean(axis=1)


# ----------------------------------------------------------- Upgrades ---------
def _majority(m: int) -> int:
    """Smallest number of fooled runs that wins the vote (> m/2)."""
    return m // 2 + 1


def exact_vote_fooling(m: int, r_q: float) -> float:
    """P(Bin(m, R̄_Q) > m/2): the exact value Hoeffding bounds, under the same independence assumption."""
    from scipy.stats import binom
    return float(binom.sf(_majority(m) - 1, m, r_q))


def chernoff_vote_bound(m: int, r_q: float):
    """Chernoff–KL bound exp(−m·KL(t‖R̄_Q)), t = (⌊m/2⌋+1)/m: tighter than Hoeffding (Pinsker), same assumption."""
    t = _majority(m) / m
    if r_q >= t:
        return "no guarantee"
    if r_q <= 0:
        return 0.0
    kl = t * math.log(t / r_q) + ((1 - t) * math.log((1 - t) / (1 - r_q)) if t < 1 else 0.0)
    return math.exp(-m * kl)


def vote_fooling_cp(run_success: np.ndarray, m: int, alpha: float) -> dict:
    """Model-free: share of attacks whose first m runs fooled the vote, with a one-sided Clopper–Pearson bound.

    Valid however strongly the runs on one text are correlated (no independence assumption) — the headline
    number next to the Hoeffding / Chernoff bounds, which do assume independence.
    """
    from horizon.stage1.stats import cp_upper
    f = empirical_vote_fooling(run_success, m)
    return {"m": m, "fooled": int(f.sum()), "n": int(len(f)), "rate": float(f.mean()) if len(f) else float("nan"),
            "cp_upper": cp_upper(int(f.sum()), int(len(f)), alpha)}


def mixture_vote_fooling(run_success: np.ndarray, m: int) -> float:
    """E_x[P(Bin(m, p(x)) > m/2)] with p(x) the per-attack failure rate: extrapolates to m larger than the
    recorded runs, keeping the correlation that comes from the attack text (runs independent given x)."""
    from scipy.stats import binom
    p = per_attack_failure_rates(run_success)
    return float(np.mean(binom.sf(_majority(m) - 1, m, p))) if len(p) else float("nan")


def run_correlation(run_success: np.ndarray) -> float:
    """Intra-class correlation of run failures within an attack (ANOVA estimator); 0 = independent runs."""
    x = np.asarray(run_success, dtype=float)
    n, k = x.shape
    if n < 2 or k < 2:
        return float("nan")
    grand = x.mean()
    msb = k * np.sum((x.mean(axis=1) - grand) ** 2) / (n - 1)
    msw = np.sum((x - x.mean(axis=1, keepdims=True)) ** 2) / (n * (k - 1))
    den = msb + (k - 1) * msw
    return float((msb - msw) / den) if den > 0 else 0.0
