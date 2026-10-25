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
