"""Stage 1: Keyed Spotlighting with Robust Risk Bounds and Sion Minimax Game."""
from .keys import generate_key, get_keyspace_log_size, get_keyspace_size
from .metrics import compute_utility, token_f1
from .sion_v1 import bootstrap_ci, mcnemar_test_paired, solve_sion_v1
from .stats import (
    choose_defense,
    clopper_pearson_upper,
    combined_risk,
    dro_worst_case,
    empirical_asr,
    forging_probability,
)
from .transforms import TRANSFORMS, build_spotlighted_prompt

__all__ = [
    "generate_key",
    "get_keyspace_log_size",
    "get_keyspace_size",
    "TRANSFORMS",
    "build_spotlighted_prompt",
    "empirical_asr",
    "clopper_pearson_upper",
    "dro_worst_case",
    "combined_risk",
    "choose_defense",
    "forging_probability",
    "solve_sion_v1",
    "mcnemar_test_paired",
    "bootstrap_ci",
    "compute_utility",
    "token_f1",
]
