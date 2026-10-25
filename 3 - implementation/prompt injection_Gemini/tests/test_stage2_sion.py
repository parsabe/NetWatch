"""Unit tests for Stage 2 Sion Minimax LP and V_Horizon SOCP.

Cites: Slide 41, Slide 42; Ground Rules §9.
"""
import numpy as np
import pytest
from src.stage2.sion_v2 import solve_sion_v2_lp
from src.stage2.v_horizon import solve_v_horizon_socp


def test_sion_lp_known_2x2_matrix():
    """Verify Sion LP on a known 2x2 matrix game returns the correct analytical value.

    Cites: Slide 41; §9.
    Known 2x2 game:
      Styles (rows) x Configs (cols):
      M = [[3.0, 1.0],
           [2.0, 4.0]]
      Equilibrium: pi* = [0.5, 0.5], game_value = 2.5.
    """
    loss_matrix = np.array([
        [3.0, 1.0],
        [2.0, 4.0],
    ])
    config_names = ["cfg_0", "cfg_1"]
    attack_styles = ["style_0", "style_1"]
    utilities = {"cfg_0": 1.0, "cfg_1": 1.0}
    leakages = {"cfg_0": 0.0, "cfg_1": 0.0}

    res = solve_sion_v2_lp(
        loss_matrix=loss_matrix,
        config_names=config_names,
        attack_styles=attack_styles,
        utilities=utilities,
        leakages=leakages,
        u_min=0.0,
        leakage_cap_bits=100.0,
    )

    assert "game_value" in res
    assert abs(res["game_value"] - 2.5) < 1e-4, f"Expected 2.5, got {res['game_value']}"
    assert abs(res["pi_star_horizon"]["cfg_0"] - 0.75) < 1e-3
    assert abs(res["pi_star_horizon"]["cfg_1"] - 0.25) < 1e-3


def test_v_horizon_socp_solver():
    """Verify that V_Horizon SOCP solves and bounds risk within [0, 1].

    Cites: Slide 42.
    """
    n = 50
    configs = ["theta_1", "theta_2", "theta_3"]
    np.random.seed(42)
    # Synthetic per-attack losses in [0, 1]
    l_mat = np.random.uniform(0.05, 0.40, (n, len(configs)))

    keyspaces = {"theta_1": 10.0, "theta_2": 25.0, "theta_3": 50.0}
    adp_losses = {"theta_1": 0.35, "theta_2": 0.20, "theta_3": 0.15}
    utilities = {"theta_1": 0.98, "theta_2": 0.95, "theta_3": 0.90}
    leakages = {"theta_1": 8.0, "theta_2": 16.0, "theta_3": 24.0}

    res = solve_v_horizon_socp(
        paired_loss_matrix=l_mat,
        config_names=configs,
        keyspace_log_sizes=keyspaces,
        q_attempts=100.0,
        adaptive_loss_bars=adp_losses,
        utilities=utilities,
        leakages=leakages,
        rho=0.05,
        u_min=0.85,
        leakage_cap_bits=32.0,
    )

    assert "v_horizon" in res
    assert 0.0 <= res["v_horizon"] <= 1.0
    assert abs(sum(res["pi_star_horizon"].values()) - 1.0) < 1e-4
