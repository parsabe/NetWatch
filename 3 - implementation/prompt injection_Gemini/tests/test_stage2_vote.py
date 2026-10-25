"""Unit tests for Stage 2 majority voting and Hoeffding bounds.

Cites: Slide 36, Slide 37; Ground Rules §9.
"""
import pytest
from src.stage2.vote import hoeffding_vote_bound, majority_vote


def test_hoeffding_vote_bound_slide_values():
    """Verify Hoeffding(m=5, R_bar_Q=0.15) ~= 0.294; (m=9) ~= 0.110; R_bar_Q >= 0.5 -> 'no guarantee'.

    Cites: Slide 37; §9.
    """
    b_m5 = hoeffding_vote_bound(m=5, r_bar_q=0.15)
    assert isinstance(b_m5, float)
    assert abs(b_m5 - 0.294) < 0.003, f"Expected ~0.294, got {b_m5}"

    b_m9 = hoeffding_vote_bound(m=9, r_bar_q=0.15)
    assert isinstance(b_m9, float)
    assert abs(b_m9 - 0.110) < 0.003, f"Expected ~0.110, got {b_m9}"

    # Boundary case: R_bar_Q >= 0.5 -> "no guarantee"
    assert hoeffding_vote_bound(m=5, r_bar_q=0.50) == "no guarantee"
    assert hoeffding_vote_bound(m=9, r_bar_q=0.65) == "no guarantee"


def test_majority_vote_ties_and_no_majority():
    """Verify Vote: ties / no majority -> refusal (Slide 36; §9)."""
    # 4 runs, 2 vs 2 -> tie (count = 2, total = 4; 2 is not > 4/2)
    success, winner, count, reason = majority_vote(["action_a", "action_b", "action_a", "action_b"], m=4)
    assert not success
    assert winner is None
    assert "Majority vote failed" in reason

    # 5 runs, all different -> no majority
    success, winner, count, reason = majority_vote(["a", "b", "c", "d", "e"], m=5)
    assert not success
    assert winner is None

    # 5 runs, 3 agree -> strict majority
    success, winner, count, reason = majority_vote(["target_val", "other", "target_val", "other2", "target_val"], m=5)
    assert success
    assert winner == "target_val"
    assert count == 3
