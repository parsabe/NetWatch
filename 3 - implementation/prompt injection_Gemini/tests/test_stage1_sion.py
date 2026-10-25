"""Unit tests for Stage 1 Sion Minimax Game SOCP solver.

Cites: Slide 24, Slide 27, Slide 30.
"""
import numpy as np
import pytest
from src.stage1.keys import get_keyspace_log_size
from src.stage1.sion_v1 import bootstrap_ci, mcnemar_test_paired, solve_sion_v1


def test_solve_sion_v1_toy():
    """Test SOCP solver on synthetic paired matrix."""
    n_attacks = 100
    variants = ["none", "delimit", "datamark_dyn"]
    np.random.seed(42)

    # Paired attack success matrix A[x, tau]
    # 'none' fails often (0.8 ASR), 'datamark_dyn' fails rarely (0.1 ASR)
    a_mat = np.zeros((n_attacks, len(variants)))
    a_mat[:, 0] = np.random.binomial(1, 0.8, n_attacks)
    a_mat[:, 1] = np.random.binomial(1, 0.5, n_attacks)
    a_mat[:, 2] = np.random.binomial(1, 0.1, n_attacks)

    log_sizes = {v: get_keyspace_log_size(v) for v in variants}
    p_adp_bars = {"none": 0.9, "delimit": 0.6, "datamark_dyn": 0.15}
    utilities = {"none": 1.0, "delimit": 0.98, "datamark_dyn": 0.95}

    res = solve_sion_v1(
        paired_a_matrix=a_mat,
        variant_names=variants,
        keyspace_log_sizes=log_sizes,
        q_attempts=100.0,
        p_adp_bars=p_adp_bars,
        utilities=utilities,
        baseline_variant="none",
        rho=0.05,
        eta=0.10,
    )

    assert "v1" in res
    assert "pi_star" in res
    assert 0.0 <= res["v1"] <= 1.0
    # Sum of weights in pi* is 1
    assert abs(sum(res["pi_star"].values()) - 1.0) < 1e-4
    # The safest variant 'datamark_dyn' should receive high weight
    assert res["pi_star"]["datamark_dyn"] > res["pi_star"]["none"]


def test_mcnemar_and_bootstrap():
    """Test McNemar test and bootstrap confidence interval computation."""
    n = 200
    a = np.zeros(n)
    b = np.zeros(n)
    # Significant difference
    a[:30] = 1
    b[:5] = 1

    mcn = mcnemar_test_paired(a, b)
    assert "p_value" in mcn
    assert mcn["p_value"] < 0.05

    low, high = bootstrap_ci(a, n_boot=200, confidence_level=0.95)
    assert 0.0 <= low <= np.mean(a) <= high <= 1.0
