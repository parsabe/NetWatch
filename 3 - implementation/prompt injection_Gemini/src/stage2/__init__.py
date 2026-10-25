"""Stage 2: Deterministic Guardrail and Math-Hardened CaMeL."""
from .conformal import calibrate_conformal_threshold, evaluate_conformal_risk
from .leakage import check_leakage_constraint, compute_schema_leakage_bits
from .loss import compute_per_attack_loss
from .policy import PolicyDecision, PolicyEngine
from .quarantine import QuarantinedExecutionAlarm, QuarantinedRunner
from .sion_v2 import solve_sion_v2_lp
from .types import Capability, SchemaParser, TypedValue
from .v_horizon import solve_v_horizon_socp
from .vote import evaluate_empirical_vote_fooling, hoeffding_vote_bound, majority_vote
from .z3_verifier import NoninterferenceViolationError, verify_policy_noninterference

__all__ = [
    "Capability",
    "TypedValue",
    "SchemaParser",
    "PolicyDecision",
    "PolicyEngine",
    "QuarantinedRunner",
    "QuarantinedExecutionAlarm",
    "majority_vote",
    "hoeffding_vote_bound",
    "evaluate_empirical_vote_fooling",
    "calibrate_conformal_threshold",
    "evaluate_conformal_risk",
    "compute_per_attack_loss",
    "compute_schema_leakage_bits",
    "check_leakage_constraint",
    "solve_sion_v2_lp",
    "solve_v_horizon_socp",
    "verify_policy_noninterference",
    "NoninterferenceViolationError",
]
