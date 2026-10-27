"""Theory-mode inputs: figures before any LLM run (§8).

Math-only curves (forging, CP, DRO, Hoeffding, leakage) need parameters only.
Figures that need a paired matrix (V1, McNemar, Stage 2 game) use a SIMULATED
matrix drawn from params.yaml `theory:` with numpy's seeded generator (this is
data simulation, not key generation). Every such figure is labelled SIMULATED.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import expit, logit

from horizon.config import Config
from horizon.stage1.transforms import build_variants
from horizon.stage2.conformal import calibrate_lambda


@dataclass
class Stage1Inputs:
    names: list[str]
    log_K: np.ndarray
    U: np.ndarray
    A: np.ndarray            # n × T paired matrix
    p_hat: np.ndarray
    s: np.ndarray
    n: int
    s_adp: np.ndarray
    n_adp: int
    simulated: bool


def stage1_theory(cfg: Config) -> Stage1Inputs:
    th = cfg.theory
    variants = build_variants(cfg.stage1)
    names = list(variants)
    rng = np.random.default_rng(cfg.run["seed"])
    n = th["n_attacks_sim"]
    difficulty = rng.normal(0.0, th["attack_difficulty_sd"], size=n)
    p = np.array([th["example_p_hat"][t] for t in names])
    probs = expit(logit(p)[None, :] + difficulty[:, None])
    A = (rng.random((n, len(names))) < probs).astype(int)
    p_adp = np.array([th["example_p_adp"][t] for t in names])
    n_adp = th["example_adaptive"]["n"]
    return Stage1Inputs(names, np.array([variants[t].log_key_space() for t in names]),
                        np.array([th["example_U"][t] for t in names]), A, A.mean(axis=0),
                        A.sum(axis=0), n, np.round(p_adp * n_adp).astype(int), n_adp, True)


@dataclass
class Stage2Inputs:
    configs: list[tuple]           # (m, parser, policy, λ)
    styles: list[str]
    style_of: np.ndarray           # style index per attack
    G: np.ndarray                  # n × Θ
    C: np.ndarray
    F: np.ndarray
    utility: np.ndarray
    run_success: np.ndarray        # n × R single-run failures (independent keys)
    scores: np.ndarray             # calibration scores s(x)
    dangerous: np.ndarray
    simulated: bool = True


def stage2_theory(cfg: Config, R_Q: float) -> Stage2Inputs:
    """Simulated per-attack G/C/F under each θ, consistent with the Stage 2 pipeline logic:
    a per-attack single-run failure p(x) around R̄_Q; vote fooled when > m/2 of m runs fail;
    F = fooled ∧ parser passes; CONFIRM-policies route F to the user when s ≥ λ (C);
    G = 0 because the verified policy never ALLOWs UNTRUSTED-dependent calls."""
    th, s2 = cfg.theory, cfg.stage2
    rng = np.random.default_rng(cfg.run["seed"])
    n = th["n_attacks_sim"]
    styles = th["styles_sim"]
    style_of = rng.integers(0, len(styles), size=n)
    shift = np.asarray(th["style_shift_sim"])[style_of]
    px = expit(logit(R_Q) + rng.normal(0.0, th["attack_difficulty_sd"], size=n) + shift)
    R = th["runs_per_attack_sim"]
    runs = (rng.random((n, R)) < px[:, None]).astype(int)

    configs, G, C, F, U = [], [], [], [], []
    lam = th["lambda_sim"]
    for m in s2["m_list"]:
        fooled = (2 * runs[:, :m].sum(axis=1) > m)
        agree = np.maximum(runs[:, :m].mean(axis=1), 1 - runs[:, :m].mean(axis=1))
        for parser in s2["parsers"]:
            passes = rng.random(n) < th["parser_pass_sim"][parser]
            f = (fooled & passes).astype(int)
            for policy in s2["policies"]:
                c = (f.astype(bool) & (agree >= lam)).astype(int) if policy == "confirm_untrusted" \
                    else np.zeros(n, int)
                configs.append((m, parser, policy, lam))
                G.append(np.zeros(n, int))
                C.append(c)
                F.append(f)
                U.append(th["utility_sim"][policy])
    ncal = n
    dangerous = rng.random(ncal) < th["dangerous_fraction_sim"]
    sb = th["score_beta_sim"]
    scores = np.where(dangerous, rng.beta(*sb["dangerous"], ncal), rng.beta(*sb["benign"], ncal))
    return Stage2Inputs(configs, styles, style_of, np.array(G).T, np.array(C).T, np.array(F).T,
                        np.array(U), runs, scores, dangerous)


def crc_theory(cfg: Config, inp: Stage2Inputs):
    a = cfg.stage2["user_mistake_rate"]
    return calibrate_lambda(inp.scores, inp.dangerous, a, cfg.stage2["alpha_user"], cfg.lambda_grid(),
                            a if cfg.stage2.get("crc_loss_bound") == "a" else 1.0)
