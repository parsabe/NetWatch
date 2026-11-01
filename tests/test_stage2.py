"""§9 unit tests for Stage 2. Slide values appear ONLY in tests/."""
import math

import numpy as np
import pytest
from pydantic import BaseModel

from horizon.config import load_config
from horizon.stage2 import schemas
from horizon.stage2.conformal import calibrate_lambda
from horizon.stage2.horizon_value import solve_v_horizon
from horizon.stage2.leakage import config_leakage
from horizon.stage2.loss import cp_loss_bound, per_attack_loss
from horizon.stage2.policy import CallContext, Decision, PolicyTable
from horizon.stage2.sion_lp import solve_sion_lp
from horizon.stage2.values import Cap, ParseError, TypedValue, derive, join
from horizon.stage2.vote import empirical_vote_fooling, hoeffding_vote_bound, majority_vote
from horizon.stage2.z3_check import NoninterferenceViolation, assert_noninterference, verify_noninterference

CFG = load_config()
TOOLS = {"send_email": ["recipients", "subject", "body"], "get_unread_emails": [],
         "send_money": ["recipient", "amount"], "read_channel_messages": ["channel"],
         "update_password": ["password"], "post_webpage": ["url", "content"]}


# ---- Hoeffding (Slide 36) --------------------------------------------------
def test_hoeffding_m5():
    assert hoeffding_vote_bound(5, 0.15) == pytest.approx(0.294, abs=1e-3)


def test_hoeffding_m9():
    assert hoeffding_vote_bound(9, 0.15) == pytest.approx(0.110, abs=1e-3)


@pytest.mark.parametrize("r", [0.5, 0.7, 1.0])
def test_hoeffding_no_guarantee(r):
    assert hoeffding_vote_bound(5, r) == "no guarantee"


# ---- Vote (Slide 35) ---------------------------------------------------------
def test_vote_majority_accepts():
    r = majority_vote([("a", 1), ("a", 1), ("b", 2)], 3)
    assert r.accepted and r.value == 1 and r.agreement == pytest.approx(2 / 3)


def test_vote_tie_refuses():
    r = majority_vote([("a", 1), ("a", 1), ("b", 2), ("b", 2)], 4)
    assert not r.accepted and r.value is None


def test_vote_no_majority_refuses():
    r = majority_vote([("a", 1), ("b", 2), ("c", 3)], 3)
    assert not r.accepted


def test_vote_unparsed_runs_count_toward_m():
    # 2 of 5 agree, 3 failed to parse → 2 ≤ 5/2 → refusal
    r = majority_vote([("a", 1), ("a", 1)], 5)
    assert not r.accepted


def test_empirical_vote_fooling():
    runs = np.array([[1, 1, 0, 0, 0], [1, 1, 1, 0, 0]])
    assert empirical_vote_fooling(runs, 5).tolist() == [0, 1]


# ---- Typed values / capabilities (Slide 37) --------------------------------
def test_label_join_most_restrictive():
    assert join([Cap.SYSTEM, Cap.USER]) == Cap.USER
    assert join([Cap.USER, Cap.UNTRUSTED]) == Cap.UNTRUSTED


def test_derived_value_inherits_control_label():
    a = TypedValue(1, "int", Cap.USER)
    cond = TypedValue(True, "bool", Cap.UNTRUSTED)
    v = derive(lambda x: x + 1, a, control=[cond])
    assert v.cap == Cap.UNTRUSTED and v.data == 2


class Date(BaseModel):
    date: str


def test_parser_rejects_wrong_type():
    with pytest.raises(ParseError):
        schemas.parse('{"date": "2025-04-12; DROP TABLE"}', Date, "strict", CFG.stage2)
    with pytest.raises(ParseError):
        schemas.parse("no json here", Date, "strict", CFG.stage2)
    assert schemas.parse('{"date": "2025-04-12"}', Date, "strict", CFG.stage2).date == "2025-04-12"


def test_canonical_form():
    a = schemas.canonical(Date(date=" 2025-04-12 "))
    b = schemas.canonical(Date(date="2025-04-12"))
    assert a == b


# ---- Leakage (Slide 41) ------------------------------------------------------
class Flag(BaseModel):
    ok: bool


def test_leakage_bool_is_one_bit():
    assert config_leakage([Flag], "strict", CFG.stage2).schema_bits == pytest.approx(1.0)


def test_leakage_strict_below_loose():
    s = config_leakage([Date], "strict", CFG.stage2).total
    lo = config_leakage([Date], "loose", CFG.stage2).total
    assert s < lo


# ---- Policy + Z3 (Slide 38) --------------------------------------------------
@pytest.mark.parametrize("name", ["strict", "confirm_untrusted"])
def test_shipped_policies_satisfy_noninterference(name):
    assert verify_noninterference(PolicyTable.load(name), TOOLS) == []


def test_z3_catches_bad_policy():
    raw = PolicyTable.load("strict").raw
    bad = dict(raw)
    bad["policies"] = {"bad": {"default": [{"when": {"untrusted_control": True}, "then": "DENY"},
                                           {"then": "ALLOW"}]}}
    table = PolicyTable(bad, "bad")
    ces = verify_noninterference(table, TOOLS)
    assert ces and all(c.control_cap != "UNTRUSTED" for c in ces)
    assert any("UNTRUSTED" in c.arg_caps.values() for c in ces)
    with pytest.raises(NoninterferenceViolation):
        assert_noninterference(table, TOOLS)


def test_policy_runtime_matches_rules():
    t = PolicyTable.load("confirm_untrusted")
    ctx = CallContext("send_email", {"recipients": Cap.UNTRUSTED}, Cap.USER, "owner")
    assert t.decide(ctx) == Decision.CONFIRM
    ctx = CallContext("send_email", {"recipients": Cap.USER}, Cap.UNTRUSTED, "owner")
    assert t.decide(ctx) == Decision.CONFIRM          # control dependency counts
    ctx = CallContext("send_money", {"recipient": Cap.UNTRUSTED}, Cap.USER, "owner")
    assert t.decide(ctx) == Decision.DENY
    ctx = CallContext("send_email", {"recipients": Cap.USER}, Cap.USER, "guest")
    assert t.decide(ctx) == Decision.DENY             # role-based rule
    ctx = CallContext("send_email", {"recipients": Cap.USER}, Cap.SYSTEM, "owner")
    assert t.decide(ctx) == Decision.ALLOW


# ---- Conformal (Slide 39) ----------------------------------------------------
def test_crc_picks_smallest_feasible_lambda():
    rng = np.random.default_rng(0)
    n = 400
    dangerous = rng.random(n) < 0.3
    scores = np.where(dangerous, rng.uniform(0, 0.6, n), rng.uniform(0.4, 1, n))
    grid = np.linspace(0, 1, 101)
    res = calibrate_lambda(scores, dangerous, a=0.5, alpha_user=0.02, grid=grid)
    assert math.isfinite(res.lambda_hat)
    i = int(np.searchsorted(grid, res.lambda_hat))
    assert res.bound_curve[i] <= 0.02
    assert i == 0 or res.bound_curve[i - 1] > 0.02


def test_crc_infeasible_never_escalates():
    res = calibrate_lambda(np.ones(5), np.ones(5, bool), a=1.0, alpha_user=0.01, grid=np.linspace(0, 1, 11))
    assert res.lambda_hat == float("inf")


# ---- Loss (Slide 40) ---------------------------------------------------------
def test_per_attack_loss():
    l = per_attack_loss([1, 0, 0], [0, 1, 0], [0, 0, 1], a=0.1, h=1.0)
    assert l.tolist() == pytest.approx([1.0, 0.1, 1.0])
    b = cp_loss_bound([0] * 100, [0] * 100, [0] * 100, 0.1, 1.0, 0.05)
    assert b.total == pytest.approx(b.G + 0.1 * b.C + b.F)


# ---- Sion LP (Slide 41) ------------------------------------------------------
def test_sion_lp_known_2x2_game():
    # ℓ̄ = [[4,1],[2,3]] (rows: attack styles, cols: configs): π = (½,½), value 2.5, w* = (¼,¾)
    r = solve_sion_lp(np.array([[4.0, 1.0], [2.0, 3.0]]), ["k1", "k2"], ["a", "b"],
                      np.ones(2), 0.0, np.zeros(2), 1.0)
    assert r.value == pytest.approx(2.5)
    assert r.pi["a"] == pytest.approx(0.5) and r.w["k1"] == pytest.approx(0.25)


def test_sion_lp_matching_pennies():
    r = solve_sion_lp(np.array([[1.0, 0.0], [0.0, 1.0]]), ["k1", "k2"], ["a", "b"],
                      np.ones(2), 0.0, np.zeros(2), 1.0)
    assert r.value == pytest.approx(0.5)


def test_sion_lp_leakage_cap_excludes_config():
    r = solve_sion_lp(np.array([[1.0, 0.0], [0.0, 1.0]]), ["k1", "k2"], ["a", "b"],
                      np.ones(2), 0.0, np.array([0.0, 100.0]), 10.0)
    assert r.pi["b"] == pytest.approx(0.0) and r.value == pytest.approx(1.0)


# ---- V_Horizon (Slide 42) ---------------------------------------------------
def test_v_horizon_capped_and_feasible():
    L = np.array([[0.0, 1.0], [1.0, 0.0]] * 50)
    r = solve_v_horizon(L, ["a", "b"], np.array([0.6, 0.6]), np.array([0.1, 0.1]), 1.0,
                        np.array([60.0, 60.0]), 0.1, np.ones(2), 0.0, np.zeros(2), 10.0, "CLARABEL")
    assert r.status == "optimal"
    assert r.pi["a"] == pytest.approx(0.5, abs=1e-3)       # mixing kills the variance
    assert r.value == pytest.approx(0.6, abs=1e-3)
