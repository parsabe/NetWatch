"""Tests for the analysis upgrades: every new bound is checked for validity (coverage / ≥ exact value) and
tightness against the original method it sits next to."""
import math

import numpy as np
import pytest
from scipy import stats as sps

from horizon.config import load_config
from horizon.stage1 import stats
from horizon.stage1.sion import evaluate_v1_objective, solve_v1
from horizon.stage2.conformal import calibrate_lambda, calibrate_lambda_groups, crc_losses
from horizon.stage2.leakage import config_leakage, side_channel_bits, trace_bits
from horizon.stage2.loss import bernstein_loss_bound, cp_loss_bound
from horizon.stage2.vote import (chernoff_vote_bound, exact_vote_fooling, hoeffding_vote_bound,
                                 mixture_vote_fooling, run_correlation, vote_fooling_cp)

CFG = load_config()
RNG = np.random.default_rng(7)


# ---- Stage 1 -------------------------------------------------------------------
def test_holm_matches_definition():
    p = {"a": 0.01, "b": 0.04, "c": 0.03}
    adj = stats.holm(p)
    assert adj["a"] == pytest.approx(0.03)
    assert adj["c"] == pytest.approx(0.06)
    assert adj["b"] == pytest.approx(0.06)          # monotone: never below the previous adjusted value


def test_paired_bootstrap_diff_contains_truth():
    a = RNG.random(400) < 0.4
    b = RNG.random(400) < 0.2
    d, lo, hi = stats.paired_bootstrap_diff(a, b, 0.05, 2000, 1)
    assert lo <= d <= hi
    assert lo < 0.2 < hi


def test_chi2_divergence_and_rho_hat():
    assert stats.chi2_divergence([0.5, 0.5], [0.5, 0.5]) == 0.0
    # one source of a 50/50 pool that only has its own categories: ½ Σ p (q/p − 1)² = ½
    r = stats.estimate_rho({"s1": {"x": 10}, "s2": {"y": 10}})
    assert r["rho_hat"] == pytest.approx(0.5)


def test_v1_cp_mean_is_conservative():
    A = (RNG.random((80, 3)) < [0.3, 0.2, 0.1]).astype(float)
    U, lk, padp = np.array([1.0, 1.0, 1.0]), np.log([1e9] * 3), np.zeros(3)
    pbar = np.array([stats.cp_upper(int(A[:, j].sum()), 80, 0.05) for j in range(3)])
    emp = solve_v1(A, list("abc"), U, 1.0, 0.1, 1, lk, padp, 0.05, "CLARABEL")
    cpv = solve_v1(A, list("abc"), U, 1.0, 0.1, 1, lk, padp, 0.05, "CLARABEL", pbar)
    assert cpv.inner >= emp.inner - 1e-9
    w = np.array(list(cpv.pi.values()))
    assert evaluate_v1_objective(A, w, 1, lk, padp, 0.05, pbar).inner == pytest.approx(cpv.inner, abs=1e-5)


# ---- Stage 2: vote -------------------------------------------------------------
@pytest.mark.parametrize("m", [1, 3, 5, 9, 15])
@pytest.mark.parametrize("r", [0.05, 0.15, 0.3, 0.45])
def test_vote_bounds_ordering(m, r):
    ex = exact_vote_fooling(m, r)
    ch = chernoff_vote_bound(m, r)
    ho = hoeffding_vote_bound(m, r)
    assert ex == pytest.approx(sps.binom.sf(m // 2, m, r))
    assert ex <= ch + 1e-12 <= ho + 1e-9             # exact ≤ Chernoff–KL ≤ Hoeffding


def test_vote_fooling_cp_and_mixture():
    runs = (RNG.random((300, 5)) < RNG.beta(1, 5, (300, 1))).astype(int)   # correlated within an attack
    r = vote_fooling_cp(runs, 5, 0.05)
    assert r["rate"] <= r["cp_upper"] <= 1
    assert mixture_vote_fooling(runs, 5) == pytest.approx(r["rate"], abs=0.05)
    assert run_correlation(runs) > 0.05             # the beta mixture induces positive correlation
    indep = (RNG.random((3000, 5)) < 0.2).astype(int)
    assert abs(run_correlation(indep)) < 0.03


# ---- Stage 2: CRC --------------------------------------------------------------
def test_crc_b_equals_a_is_tighter_and_valid():
    a, alpha, grid = 0.1, 0.05, np.linspace(0, 1, 101)
    risks_a, lam_a, lam_1 = [], [], []
    for _ in range(600):
        n = 40
        dang = RNG.random(n + 1) < 0.3
        sc = np.where(dang, RNG.beta(2, 4, n + 1), RNG.beta(6, 2, n + 1))
        la = calibrate_lambda(sc[:n], dang[:n], a, alpha, grid, a).lambda_hat
        l1 = calibrate_lambda(sc[:n], dang[:n], a, alpha, grid).lambda_hat
        lam_a.append(la)
        lam_1.append(l1)
        risks_a.append(crc_losses(sc[n:], dang[n:], la, a)[0] if math.isfinite(la) else 0.0)
    assert np.mean(risks_a) <= alpha                                       # guarantee still holds
    assert all(x <= y for x, y in zip(lam_a, lam_1))                       # never stricter than B = 1
    assert sum(math.isfinite(x) for x in lam_a) >= sum(math.isfinite(x) for x in lam_1)


def test_crc_groups_takes_the_max():
    sc = np.concatenate([RNG.beta(6, 2, 200), RNG.beta(2, 4, 200)])
    dang = np.concatenate([RNG.random(200) < 0.1, RNG.random(200) < 0.6])
    grp = np.array(["easy"] * 200 + ["hard"] * 200)
    g = calibrate_lambda_groups(sc, dang, grp, 1.0, 0.05, np.linspace(0, 1, 101), 1.0, 30)
    assert g["lambda_hat"] == max(v["lambda_hat"] for v in g["per_group"].values())
    tiny = calibrate_lambda_groups(sc[:5], dang[:5], ["x", "y", "x", "y", "z"], 1.0, 0.5,
                                   np.linspace(0, 1, 11), 1.0, 30)
    assert list(tiny["per_group"]) == ["pooled_small_groups"]


# ---- Stage 2: loss bounds --------------------------------------------------------
def test_bonferroni_loss_bound_joint_coverage():
    a, h, n = 0.1, 1.0, 150
    pG, pC, pF = 0.05, 0.15, 0.03
    truth = pG + a * pC + h * pF
    cover_b = cover_n = 0
    trials = 1500
    for _ in range(trials):
        G, C, F = (RNG.random(n) < p for p in (pG, pC, pF))
        cover_b += cp_loss_bound(G, C, F, a, h, 0.05, "bonferroni").total >= truth
        cover_n += cp_loss_bound(G, C, F, a, h, 0.05).total >= truth
    assert cover_b / trials >= 0.95
    assert cover_b >= cover_n


def test_bernstein_loss_bound_coverage():
    a, h, n = 0.1, 1.0, 200
    hits = 0
    for _ in range(800):
        G, C, F = (RNG.random(n) < p for p in (0.05, 0.15, 0.03))
        hits += bernstein_loss_bound(G, C, F, a, h, 0.05) >= 0.05 + a * 0.15 + h * 0.03
    assert hits / 800 >= 0.95


# ---- leakage -----------------------------------------------------------------------
def test_trace_side_channel():
    assert trace_bits(10, 3) == pytest.approx(20.0)
    s2 = dict(CFG.stage2, side_channel_mode="fixed", side_channel_bits=0.0)
    assert side_channel_bits(s2) == 0.0
    from pydantic import BaseModel

    class Yes(BaseModel):
        answer: bool
    lk = config_leakage([Yes], "strict", dict(CFG.stage2, side_channel_mode="trace"))
    assert lk.side_channel_bits == pytest.approx(trace_bits(CFG.stage2["side_channel_max_decisions"],
                                                            CFG.stage2["side_channel_outcomes"]))
