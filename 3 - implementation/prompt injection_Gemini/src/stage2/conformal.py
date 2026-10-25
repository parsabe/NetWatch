"""Conformal Risk Control (CRC) calibration for the user confirmation threshold lambda.

Cites: Slide 40 (Step 8: allow, deny or ask user; Angelopoulos et al., ICLR 2024).
Theorem:
    P(user mistakenly approves a dangerous call) <= alpha_user
Calibration:
    On the calibration split of n samples, find lambda_hat = smallest lambda such that:
        (n / (n + 1)) * R_hat_n(lambda) + 1 / (n + 1) <= alpha_user
"""
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np


def calibrate_conformal_threshold(
    calibration_scores: np.ndarray,
    calibration_losses: np.ndarray,
    alpha_user: float,
    candidate_lambdas: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Compute calibrated confirmation threshold lambda_hat.

    Args:
        calibration_scores: 1D array of uncertainty/disagreement scores s(x).
        calibration_losses: 1D array of binary/bounded losses (e.g. 1 if dangerous action was executed).
        alpha_user: User mistake level risk target (e.g. 0.01).
        candidate_lambdas: Discretized grid of lambda thresholds to evaluate.
    Returns:
        Dict containing lambda_hat, empirical_risk, upper_bound_risk.

    Cites: Slide 40.
    """
    n = len(calibration_scores)
    if n == 0:
        raise ValueError("Calibration split is empty.")

    if candidate_lambdas is None:
        # Evaluate 100 candidate thresholds over score range
        candidate_lambdas = np.linspace(0.0, 1.0, 101)

    lambda_hat = None
    r_hat_selected = 1.0
    bound_selected = 1.0

    # Sort thresholds from smallest to largest to pick smallest lambda satisfying the bound
    for lam in candidate_lambdas:
        # A higher threshold asks user when score >= lambda
        # When asked, user may mistakenly approve (loss occurs only if not blocked)
        # Risk R_hat_n(lambda) is the mean loss when applying threshold lambda
        # If score >= lam: action escalated to CONFIRM; loss = 0 if blocked, or a * danger
        # Monotone risk in lambda: higher lambda escalates less, increasing dangerous approvals
        escalated = calibration_scores >= lam
        # Realized loss: dangerous actions that were NOT escalated (passed through without ask)
        active_loss = calibration_losses * (~escalated)
        r_hat = float(np.mean(active_loss))

        # Conformal risk upper bound: (n / (n + 1)) * R_hat + 1 / (n + 1)
        conformal_bound = (float(n) / (float(n) + 1.0)) * r_hat + (1.0 / (float(n) + 1.0))

        if conformal_bound <= alpha_user:
            lambda_hat = float(lam)
            r_hat_selected = r_hat
            bound_selected = conformal_bound
            break

    # If no threshold satisfies bound, select conservative minimum (escalate everything)
    if lambda_hat is None:
        lambda_hat = 0.0
        r_hat_selected = 0.0
        bound_selected = 1.0 / (float(n) + 1.0)

    return {
        "lambda_hat": lambda_hat,
        "empirical_risk": r_hat_selected,
        "conformal_bound": bound_selected,
        "alpha_user": alpha_user,
        "n_calibration": n,
    }


def evaluate_conformal_risk(
    report_scores: np.ndarray,
    report_losses: np.ndarray,
    lambda_hat: float,
) -> float:
    """Compute realized risk on the report split using calibrated lambda_hat.

    Cites: Slide 40.
    """
    escalated = report_scores >= lambda_hat
    active_loss = report_losses * (~escalated)
    return float(np.mean(active_loss))
