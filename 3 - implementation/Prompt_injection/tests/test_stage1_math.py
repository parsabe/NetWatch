"""§9 unit tests for the Stage 1 math. Slide values appear ONLY in tests/."""
import math

import numpy as np
import pytest

from horizon.stage1 import stats
from horizon.stage1.sion import solve_v1


def test_cp_upper_slide16_worked_example():
    # Slide 23: s=20, n=1000, α=0.05 → p̄ ≈ 2.9%
    assert stats.cp_upper(20, 1000, 0.05) == pytest.approx(0.029, abs=5e-4)


def test_cp_upper_adaptive_example():
    # Slide 23: s_a=60, n_a=500 → p̄_adp ≈ 14.7%
    assert stats.cp_upper(60, 500, 0.05) == pytest.approx(0.147, abs=5e-4)


@pytest.mark.parametrize("n", [50, 300, 1000, 3000])
def test_cp_rule_of_three(n):
    # Slide 16: s = 0 → p̄ ≈ 3/n; exact value 1 − α^(1/n)
    ub = stats.cp_upper(0, n, 0.05)
    assert ub == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-9)
    assert ub == pytest.approx(3 / n, rel=0.05)


def test_cp_all_successes_is_one():
    assert stats.cp_upper(7, 7, 0.05) == 1.0


def test_dro_worked_example():
    # Slide 23: g_0.1(0.029) ≈ 10.4%
    assert stats.dro_chi2(0.029, 0.1) == pytest.approx(0.104, abs=5e-4)


def test_dro_saturates_beyond_validity():
    p = 0.3
    limit = (1 - p) / (2 * p)
    assert stats.dro_chi2(p, limit * 1.01) == 1.0
    assert stats.dro_chi2(p, limit) == pytest.approx(1.0)       # continuous at the limit
    assert stats.dro_chi2(p, limit * 0.5) < 1.0
    assert stats.dro_chi2(0.0, 5.0) == 0.0


def test_forging_26_factorial_log_space():
    # Slide 23: 1000/26! ≈ 2.5e-24
    f = stats.forging_bound(1000, math.lgamma(27))
    assert f.value == pytest.approx(2.5e-24, rel=0.02)
    assert f.log10 == pytest.approx(math.log10(2.5e-24), abs=0.01)


def test_forging_huge_key_space_never_overflows():
    f = stats.forging_bound(1e6, math.lgamma(200))   # 199! ≫ float max
    assert f.value == 0.0 and math.isfinite(f.log10) and f.log10 < -300


def test_combined_risk_worked_example():
    # Slide 23: R̄ ≈ 0 + max(10.4%, 14.7%) ≈ 14.7%
    p_bar = stats.cp_upper(20, 1000, 0.05)
    p_adp = stats.cp_upper(60, 500, 0.05)
    r = stats.combined_risk(1000, math.lgamma(27), p_bar, p_adp, 0.1)
    assert r.total == pytest.approx(0.147, abs=5e-4)
    assert r.adaptive > r.dro


def test_combined_risk_capped_at_one():
    r = stats.combined_risk(1000, 0.0, 0.01, 0.01, 0.0)   # |K| = 1
    assert r.total == 1.0


def test_choice_rule_respects_utility_constraint():
    risk = {"none": 0.9, "a": 0.1, "b": 0.3}
    util = {"none": 1.0, "a": 0.8, "b": 0.97}
    assert stats.choose_tau(risk, util, 1.0, 0.05) == "b"
    assert stats.choose_tau(risk, util, 1.0, 0.25) == "a"


def test_mcnemar_identical_is_one():
    a = np.array([0, 1, 0, 1])
    assert stats.mcnemar_pvalue(a, a) == 1.0


def _v1(A, U, eta, q, logk, padp, rho):
    names = [f"t{i}" for i in range(A.shape[1])]
    return solve_v1(A, names, np.asarray(U, float), 1.0, eta, q, np.asarray(logk, float),
                    np.asarray(padp, float), rho, "CLARABEL")


def test_v1_mixing_beats_single_when_failures_disjoint():
    # Two variants that fail on disjoint halves: the 50/50 mix has zero variance.
    n = 200
    A = np.zeros((n, 2))
    A[: n // 10, 0] = 1
    A[n // 10: 2 * n // 10, 1] = 1
    res = _v1(A, [1, 1], 0.1, 1, [50, 50], [0, 0], 0.5)
    assert res.status == "optimal"
    assert res.pi["t0"] == pytest.approx(0.5, abs=1e-3)
    single = A[:, 0].mean() + math.sqrt(2 * 0.5 * A[:, 0].var())
    assert res.value < single


def test_v1_utility_constraint_binds():
    A = np.zeros((10, 2))
    A[:, 1] = 1   # t1 always fails, t0 never
    res = _v1(A, [0.5, 1.0], 0.1, 1, [50, 50], [0, 0], 0.0)
    # Σπ U ≥ 0.9 forces π_t0 ≤ 0.2
    assert res.pi["t0"] == pytest.approx(0.2, abs=1e-4)
    assert res.value == pytest.approx(0.8, abs=1e-4)


def test_v1_infeasible_reports_status():
    A = np.zeros((10, 1))
    res = _v1(A, [0.5], 0.1, 1, [50], [0], 0.0)
    assert res.value is None
