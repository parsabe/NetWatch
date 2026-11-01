"""Small-sample behaviour ("data starvation") of the CRC and of the empirical Sion games — offline, no LLM.

A bounded campaign has a few dozen episodes per cell, not thousands. These tests pin down what the math does then:
it answers (a weaker, labelled answer) instead of crashing, returning nothing, or letting a handful of calls decide.
"""
import math

import numpy as np
import pandas as pd
import pytest

from horizon.stage1.sion import solve_v1
from horizon.stage2.conformal import calibrate_lambda, calibrate_lambda_groups, guarantee_note, min_calibration_size
from horizon.stage2.empirical import build_stage2_inputs
from horizon.stage2.horizon_value import solve_v_horizon_robust
from horizon.stage2.sion_lp import solve_sion_lp_robust
from horizon.stage2.starvation import complete_cases, merge_small_styles, relax_utility_floor

GRID = np.linspace(0, 1, 101)
INF = float("inf")


# ------------------------------------------------------------------------------------------- CRC
def test_crc_no_data_is_fail_closed_not_vacuously_feasible():
    # B = a = 0.1 ≤ α_user = 0.2: without this rule "no data" would certify λ = 0 (ask about everything) from nothing
    r = calibrate_lambda([], [], a=0.1, alpha_user=0.2, grid=GRID, loss_bound=0.1)
    assert (r.status, r.n, r.lambda_hat) == ("no_data", 0, INF)


def test_crc_says_why_nothing_could_be_certified():
    few = calibrate_lambda(np.full(10, 0.2), np.zeros(10, bool), a=1.0, alpha_user=0.05, grid=GRID, loss_bound=1.0)
    assert few.status == "infeasible_data" and few.n_needed == 19 and few.lambda_hat == INF
    # enough calls, but every dangerous one is unanimous (score 1.0): no threshold separates them
    stuck = calibrate_lambda(np.ones(40), np.ones(40, bool), a=1.0, alpha_user=0.05, grid=GRID, loss_bound=1.0)
    assert stuck.status == "infeasible_risk" and stuck.lambda_hat == INF


def test_crc_status_ok_and_unconstrained():
    rng = np.random.default_rng(0)
    dang = rng.random(400) < 0.3
    sc = np.where(dang, rng.uniform(0, 0.3, 400), rng.uniform(0.5, 1, 400))
    ok = calibrate_lambda(sc, dang, a=0.5, alpha_user=0.02, grid=GRID, loss_bound=0.5)
    assert ok.status == "ok" and 0.1 < ok.lambda_hat < 0.35      # > 0: asking about everything is NOT enough here
    calm = calibrate_lambda(rng.uniform(0, 1, 50), np.zeros(50, bool), a=0.1, alpha_user=0.05, grid=GRID, loss_bound=0.1)
    assert calm.status == "unconstrained" and calm.lambda_hat == 0.0


def test_crc_drops_non_finite_scores():
    r = calibrate_lambda([0.1, np.nan, 0.9], [False, True, False], 0.1, 0.5, GRID, 0.1)
    assert r.n == 2


def test_min_calibration_size_and_guarantee_note():
    assert min_calibration_size(0.05, 0.1) == 1 and min_calibration_size(0.02, 0.1) == 4
    assert "weak" in guarantee_note(0.1, 0.05)          # α_user/a = 0.5: half the dangerous calls may reach the user
    assert "vacuous" in guarantee_note(0.1, 0.2)
    assert guarantee_note(0.1, 0.02) == ""


def test_group_crc_a_starved_style_cannot_veto_every_style():
    rng = np.random.default_rng(1)
    big_d = rng.random(100) < 0.1
    big = np.where(big_d, rng.uniform(0, 0.4, 100), rng.uniform(0.5, 1, 100))
    scores = np.concatenate([big, np.full(4, 0.9)])
    dang = np.concatenate([big_d, np.ones(4, bool)])
    groups = np.array(["common"] * 100 + ["rare"] * 4)
    alone = calibrate_lambda(scores[100:], dang[100:], 1.0, 0.05, GRID, 1.0)
    assert alone.lambda_hat == INF                      # the 4 rare calls on their own: B/(n+1) = 0.2 > α_user
    g = calibrate_lambda_groups(scores, dang, groups, 1.0, 0.05, GRID, 1.0, 30)
    assert math.isfinite(g["lambda_hat"])               # before: max over groups = +inf for EVERY style
    assert g["small_groups"] == ["rare"] and "common" in g["per_group"]


# --------------------------------------------------------------------------- Stage 2 game helpers
def test_merge_small_styles():
    new, names, notes = merge_small_styles(np.array([0] * 30 + [1] * 3 + [2] * 4), ["a", "b", "c"], 20)
    assert names == ["a"] and set(new) == {0} and notes            # 3 + 4 < 20 → joined the largest style
    new, names, _ = merge_small_styles(np.array([0] * 25 + [1] * 6 + [2] * 7), ["a", "b", "c"], 5)
    assert names == ["a", "b", "c"]                                  # nothing small: unchanged
    new, names, _ = merge_small_styles(np.array([0] * 3 + [1] * 4), ["a", "b"], 20)
    assert names == ["all_styles"]                                   # nothing reaches the minimum: one group
    new, names, _ = merge_small_styles(np.array([0] * 25 + [2] * 25), ["a", "b", "c"], 20)
    assert names == ["a", "c"] and sorted(set(new)) == [0, 1]       # a style without attacks disappears


def test_complete_cases_drops_misaligned_attacks_and_starved_configs():
    nan = np.nan
    M = np.array([[0, 1, nan], [1, 0, nan], [0, nan, nan], [1, 1, 0]], float)
    rows, cols, notes = complete_cases(M)
    assert rows.tolist() == [True, True, False, True] and cols.tolist() == [True, True, False]
    assert len(notes) == 2


def test_relax_utility_floor():
    floor, notes = relax_utility_floor([0.4, 0.45], 0.5, [0, 0], 10)
    assert floor == pytest.approx(0.45) and notes
    floor, notes = relax_utility_floor([0.9, 0.4], 0.5, [100, 0], 10)       # the 0.9 config breaks the leakage cap
    assert floor == pytest.approx(0.4) and notes
    assert relax_utility_floor([0.9, 0.4], 0.3, [0, 0], 10) == (0.3, [])
    assert relax_utility_floor([0.9], 0.3, [100], 10)[0] is None


def test_sion_lp_robust_survives_nan_and_an_unreachable_utility_floor():
    L = np.array([[0.3, np.nan, 0.5], [0.4, 0.2, 0.1]])
    r = solve_sion_lp_robust(L, ["k1", "k2"], ["a", "b", "c"], np.array([0.6, 0.9, 0.7]), 0.95, np.zeros(3), 100.0)
    assert r.status == "optimal" and r.value == pytest.approx(0.5, abs=1e-6)
    assert r.pi["c"] == pytest.approx(1.0, abs=1e-6) and "b" not in r.pi
    assert len(r.notes) == 2                                          # the dropped configuration + the relaxed floor


def test_sion_lp_robust_flags_uninformative_bounds():
    r = solve_sion_lp_robust(np.full((2, 2), 0.97), ["k1", "k2"], ["a", "b"], np.ones(2), 0.0, np.zeros(2), 10.0)
    assert r.status == "optimal" and any("too few attacks" in n for n in r.notes)


def test_v_horizon_robust_with_one_attack_and_one_incomplete_configuration():
    L = np.array([[0.0, 1.0, np.nan]])                                # ONE attack: no variance to estimate
    r = solve_v_horizon_robust(L, ["a", "b", "c"], np.array([0.2, 0.6, 0.3]), np.array([0.1, 0.1, 0.1]), 1.0,
                               np.array([60.0, 60.0, 60.0]), 0.1, np.array([0.9, 0.9, 0.9]), 0.5, np.zeros(3), 10.0,
                               "CLARABEL")
    assert r.status == "optimal" and r.pi["a"] == pytest.approx(1.0, abs=1e-3) and "c" not in r.pi
    assert r.terms["notes"]


# ------------------------------------------------------------------------------------ Stage 1 game
def test_stage1_sion_with_one_attack_does_not_divide_by_zero():
    names = ["none", "delimiting", "datamark_dynamic"]
    args = (names, np.array([0.5, 0.48, 0.47]), 0.5, 0.05, 1000.0, np.array([0.0, 0.0, 40.0]),
            np.array([0.5, 0.4, 0.1]), 0.1, "CLARABEL")
    r = solve_v1(np.array([[1, 0, 0]]), *args)                        # 1 attack × 3 variants: Var is undefined → 0
    assert r.status in ("optimal", "optimal_inaccurate") and r.value == pytest.approx(0.1, abs=1e-3)
    assert solve_v1(np.zeros((0, 3)), *args).status == "no data"


# ------------------------------------------------------------------------ measured episodes → game
def _episodes() -> pd.DataFrame:
    rows = []
    for cfg in ("none|m=5|strict|confirm_untrusted", "full|m=5|strict|confirm_untrusted"):
        rows.append(dict(config=cfg, suite="s", user_task="u0", injection_task="", style="benign",
                         attack_id="s/u0/benign", utility=1, asr=0, G=0, C=0, F=0, alarms=0, seconds=1))
        for k in range(30):
            style = "important_instructions" if k < 25 else "direct"          # 'direct' has 5 attacks
            asr = 1 if (cfg.startswith("none") and k % 5 == 0) else 0         # 'none' is hit 6 times, no Stage 2 events
            rows.append(dict(config=cfg, suite="s", user_task="u0", injection_task=f"i{k}", style=style,
                             attack_id=f"s/u0/i{k}/{style}", utility=1, asr=asr, G=0, C=0, F=0, alarms=0, seconds=1))
    return pd.DataFrame(rows)


def test_measured_episodes_become_a_correct_game():
    inp, notes = build_stage2_inputs(_episodes(), None, None, min_cell=10)
    j = [i for i, c in enumerate(inp.configs) if c[0] == "none"][0]
    assert inp.G.shape == (30, 2)                       # 30 attacks: the benign episodes carry no attack
    assert inp.G[:, j].sum() == 6 and inp.G[:, 1 - j].sum() == 0    # 'none' has no policy engine: success IS a bypass
    assert inp.styles == ["important_instructions"]     # 'direct' (5 attacks < 10) joined the largest style
    assert np.allclose(inp.utility, 1.0) and not inp.simulated
    assert len(inp.scores) == 0 and any("calibration.csv" in n for n in notes)


def test_attacks_missing_under_one_configuration_are_dropped_not_crashed_on():
    ep = _episodes()
    ep = ep[~(ep["config"].str.startswith("full") & (ep["attack_id"] == "s/u0/i3/important_instructions"))]
    inp, notes = build_stage2_inputs(ep, None, None, min_cell=10)
    assert inp.G.shape == (29, 2) and any("dropped" in n for n in notes)


def test_no_attacked_episode_raises_a_clear_error():
    ep = _episodes()
    with pytest.raises(ValueError, match="no attacked episodes"):
        build_stage2_inputs(ep[ep["style"] == "benign"], None, None, min_cell=10)
