"""Small-sample safeguards for the empirical Stage 2 game (Sion LP, V_Horizon) — what to do when data are starved.

With a bounded campaign every (attack style × configuration) cell holds a few dozen episodes, not thousands. The
game then fails in four ways, all handled here and all REPORTED (never silent):

1. misaligned data      configurations finished on different attacks (a phase was cut short) → NaN in the paired
                        loss matrix → crash. `complete_cases` keeps only attacks that every kept configuration saw.
2. starved style cells  ℓ̄[k, θ] for a style with 5 attacks is ≈ the rule of three (≈ 0.5) for every θ; the max over
                        styles then picks noise. `merge_small_styles` pools styles with < `min_cell` attacks.
3. noisy utility floor  U_θ from a few clean tasks has a standard error ≈ 0.1, so U_min = 0.5 sits inside the noise
                        and the game flips between "solved" and "Π infeasible". `relax_utility_floor` lowers the
                        floor to the best admissible utility (and says so) instead of returning nothing.
4. uninformative bounds when every ℓ̄ ≈ 1 the value says nothing. `informativeness_note` flags it.

Validity: dropping attacks only loses power (a CP bound on fewer attacks is still a CP bound); merging styles
changes the attacker's strategy set from "each style" to "each pooled group" — reported; relaxing U_min changes Π —
reported. None of them makes a bound invalid; they replace a crash by a weaker, labelled answer.
"""
from __future__ import annotations

import numpy as np

POOLED = "pooled_small_styles"
ALL_STYLES = "all_styles"


def merge_small_styles(style_of, styles: list[str], min_cell: int) -> tuple[np.ndarray, list[str], list[str]]:
    """Pool attack styles with fewer than `min_cell` attacks. Returns (new style index per attack, new names, notes).

    * styles with no attack at all disappear;
    * styles below `min_cell` merge into POOLED; if POOLED is itself below `min_cell` it joins the largest style;
    * if no style reaches `min_cell`, everything becomes one group (ALL_STYLES): the game has a single attacker move.
    """
    s = np.asarray(style_of, int)
    counts = np.bincount(s, minlength=len(styles)) if len(s) else np.zeros(len(styles), int)
    notes: list[str] = []
    label = {i: styles[i] for i in range(len(styles)) if counts[i] > 0}
    small = [i for i in label if counts[i] < min_cell]
    big = [i for i in label if counts[i] >= min_cell]
    if small:
        if not big:
            label = {i: ALL_STYLES for i in label}
            notes.append(f"no attack style has ≥ {min_cell} attacks → one pooled group ({int(counts.sum())} attacks): "
                         "the attacker's worst-case mix over styles cannot be estimated")
        else:
            pooled_n = int(sum(counts[i] for i in small))
            target = POOLED if pooled_n >= min_cell else styles[max(big, key=lambda i: counts[i])]
            for i in small:
                label[i] = target
            notes.append("styles with < %d attacks pooled into '%s': %s" % (
                min_cell, target, ", ".join(f"{styles[i]} ({counts[i]})" for i in small)))
    names = list(dict.fromkeys(label.values()))
    idx = {n: j for j, n in enumerate(names)}
    new = np.array([idx[label[i]] for i in s], int)
    return new, names, notes


def complete_cases(loss_matrix: np.ndarray, min_attacks: int = 2) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Rows (attacks) × columns (configurations) of a paired matrix that may contain NaN.

    Keeps the configurations that have ≥ `min_attacks` observed attacks, then the attacks observed under ALL kept
    configurations. Returns (row mask, column mask, notes). A configuration seen on very few attacks is dropped
    rather than allowed to shrink everyone else's sample to its own size."""
    M = np.asarray(loss_matrix, float)
    notes: list[str] = []
    if M.size == 0:
        return np.zeros(M.shape[0], bool), np.zeros(M.shape[1], bool), ["empty loss matrix"]
    seen = np.isfinite(M).sum(axis=0)
    cols = seen >= max(min_attacks, 0.5 * seen.max())      # drop configurations with < half of the best coverage
    if not cols.all():
        notes.append("configurations dropped (finished on too few attacks): " +
                     ", ".join(f"#{j} ({int(seen[j])} of {int(seen.max())})" for j in np.nonzero(~cols)[0]))
    rows = np.isfinite(M[:, cols]).all(axis=1) if cols.any() else np.zeros(M.shape[0], bool)
    if rows.sum() < M.shape[0] and cols.any():
        notes.append(f"{int(M.shape[0] - rows.sum())} of {M.shape[0]} attacks dropped (not run under every kept "
                     "configuration)")
    return rows, cols, notes


def relax_utility_floor(utility, U_min: float, leakage, cap: float) -> tuple[float | None, list[str]]:
    """Π = {π ∈ simplex : Σπ U ≥ U_min, π_θ = 0 if leakage_θ > cap} is empty when U_min exceeds the best admissible
    utility. Returns (floor to use, notes); None when no configuration meets the leakage cap at all."""
    u = np.asarray(utility, float)
    leak = np.asarray(leakage, float)
    ok = (leak <= cap) & np.isfinite(u)
    if not ok.any():
        return None, ["no configuration both meets the leakage cap and has a utility estimate: Π is empty"]
    best = float(u[ok].max())
    if U_min <= best + 1e-12:
        return U_min, []
    return best - 1e-9, [f"utility floor U_min = {U_min:g} is above the best admissible utility {best:.3f} "
                         f"(measured on few clean tasks): relaxed to {best:.3f}"]


def informativeness_note(loss_bar: np.ndarray, threshold: float = 0.9) -> str:
    """Empty when the bounds discriminate; a warning when every upper bound is ≈ 1 (the game says nothing)."""
    L = np.asarray(loss_bar, float)
    if L.size and np.isfinite(L).any() and float(np.nanmin(L)) >= threshold:
        return (f"every loss bound is ≥ {threshold:g} (smallest {float(np.nanmin(L)):.3f}): too few attacks per "
                "cell for the game to separate configurations — add attacks or read the value as 'no guarantee'")
    return ""
