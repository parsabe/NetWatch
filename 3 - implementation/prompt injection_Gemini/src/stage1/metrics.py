"""Pluggable utility metrics for clean document evaluation.

Cites: Slide 12 (Setup - success and utility: U_tau = E[u(M(x_kappa(empty)), y*)] in [0, 1]).
"""
import re
from collections import Counter
from typing import Optional


def token_f1(prediction: str, reference: str) -> float:
    """Compute token-level F1 score in [0, 1] between prediction and ground-truth reference."""
    def tokenize(text: str):
        return re.findall(r"\w+", text.lower())

    p_tokens = tokenize(prediction)
    r_tokens = tokenize(reference)
    if not p_tokens or not r_tokens:
        return 1.0 if (not p_tokens and not r_tokens) else 0.0

    common = sum((Counter(p_tokens) & Counter(r_tokens)).values())
    if common == 0:
        return 0.0
    precision = float(common) / float(len(p_tokens))
    recall = float(common) / float(len(r_tokens))
    return 2.0 * (precision * recall) / (precision + recall)


def exact_match(prediction: str, reference: str) -> float:
    """Binary exact match in {0.0, 1.0} after whitespace normalization."""
    return 1.0 if prediction.strip().lower() == reference.strip().lower() else 0.0


def substring_match(prediction: str, reference: str) -> float:
    """Binary score: 1.0 if reference is substring of prediction or vice-versa."""
    p_norm = prediction.strip().lower()
    r_norm = reference.strip().lower()
    return 1.0 if (r_norm in p_norm or p_norm in r_norm) else 0.0


UTILITY_METRICS = {
    "token_f1": token_f1,
    "exact_match": exact_match,
    "substring_match": substring_match,
}


def compute_utility(prediction: str, reference: str, metric_name: str = "token_f1") -> float:
    """Compute utility score u in [0, 1]. Cites: Slide 12."""
    if metric_name not in UTILITY_METRICS:
        raise ValueError(f"Unknown utility metric '{metric_name}'. Supported: {list(UTILITY_METRICS.keys())}")
    return UTILITY_METRICS[metric_name](prediction, reference)
