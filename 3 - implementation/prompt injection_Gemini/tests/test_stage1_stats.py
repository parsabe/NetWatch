"""Unit tests for Stage 1 statistics, DRO, and risk formulas.

Verifies exact values specified in Ground Rules §9 and Slides 16, 19, 23.
"""
import math
import pytest
from src.stage1.stats import (
    clopper_pearson_upper,
    combined_risk,
    dro_worst_case,
    forging_probability,
)


def test_clopper_pearson_upper_slide_values():
    """Verify CP upper(s=20, n=1000, alpha=0.05) ~= 0.029.

    Verify CP upper(s=60, n=500, alpha=0.05) ~= 0.147.
    Cites: Slide 16, Slide 23; §9.
    """
    p_bar_20 = clopper_pearson_upper(successes=20, total_trials=1000, alpha=0.05)
    assert abs(p_bar_20 - 0.029) < 0.002, f"Expected ~0.029, got {p_bar_20}"

    p_bar_60 = clopper_pearson_upper(successes=60, total_trials=500, alpha=0.05)
    assert abs(p_bar_60 - 0.147) < 0.003, f"Expected ~0.147, got {p_bar_60}"


def test_clopper_pearson_rule_of_three():
    """Verify CP upper(s=0, n) ~= 3/n (exact = 1 - alpha^(1/n)).

    Cites: Slide 16; §9.
    """
    n = 1000
    alpha = 0.05
    exact = 1.0 - (alpha ** (1.0 / n))
    approx_rule_of_three = 3.0 / n  # ~0.003

    p_bar_zero = clopper_pearson_upper(successes=0, total_trials=n, alpha=alpha)
    assert abs(p_bar_zero - exact) < 1e-6
    assert abs(p_bar_zero - approx_rule_of_three) < 0.0005


def test_dro_closed_form():
    """Verify g_0.1(0.029) ~= 0.104; DRO returns 1.0 when rho > (1-p)/(2p).

    Cites: Slide 19, Slide 23; §9.
    """
    p = 0.029
    rho = 0.1
    g_val = dro_worst_case(p=p, rho=rho)
    assert abs(g_val - 0.104) < 0.002, f"Expected ~0.104, got {g_val}"

    # Saturation threshold: (1 - p) / (2p)
    sat_rho = (1.0 - p) / (2.0 * p)
    assert dro_worst_case(p=p, rho=sat_rho + 0.05) == 1.0
    assert dro_worst_case(p=0.0, rho=rho) == 0.0


def test_forging_log_space():
    """Verify Forging 1000/26! ~= 2.5e-24 (log-space).

    Cites: Slide 13, Slide 23; §9.
    """
    # 26! log-space: ln(26!) = lgamma(27)
    ln_keyspace = math.lgamma(27)
    q = 1000.0
    p_forge_linear, p_forge_log10 = forging_probability(ln_keyspace, q)

    expected = 2.5e-24
    assert abs(p_forge_linear - expected) < 1e-24 or math.isclose(p_forge_linear, expected, rel_tol=0.05)
    # log10(2.5e-24) ~= -23.60
    assert abs(p_forge_log10 - math.log10(expected)) < 0.05


def test_combined_risk_worked_example():
    """Verify Combined R_bar (worked example) ~= 0.147.

    Random scrambling of 26 letters, q=1000, alpha=0.05, rho=0.1.
    Static: s=20, n=1000 -> p_bar ~= 0.029 -> g_0.1(p_bar) ~= 0.104.
    Adaptive: s_a=60, n_a=500 -> p_adp_bar ~= 0.147.
    R_bar = min{1, 2.5e-24 + max(0.104, 0.147)} ~= 0.147.
    Cites: Slide 23; §9.
    """
    ln_keyspace = math.lgamma(27)
    q = 1000.0
    p_bar = clopper_pearson_upper(20, 1000, alpha=0.05)
    p_adp_bar = clopper_pearson_upper(60, 500, alpha=0.05)
    rho = 0.1

    r_bar = combined_risk(ln_keyspace, q, p_bar, p_adp_bar, rho)
    assert abs(r_bar - 0.147) < 0.003, f"Expected ~0.147, got {r_bar}"
