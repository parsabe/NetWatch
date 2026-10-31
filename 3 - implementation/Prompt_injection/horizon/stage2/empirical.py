"""Measured AgentDojo episodes → the arrays of the empirical Stage 2 game, robust to small and incomplete data.

`scripts/make_figures.py` used to build these arrays straight from `pivot_table` and crashed or misled when

* configurations had finished on different attacks (a phase was cut short) → NaN in the paired matrix;
* the benign episodes (G = C = F = 0 by construction) were counted as attacks, diluting every loss estimate;
* the configurations without Stage 2 ('none', 'stage1') had G = 0 for every attack even where the attack succeeded
  (no policy engine → no events), so the game preferred "no defence" because it looked loss-free;
* utility was averaged over attacked episodes (utility UNDER attack) instead of the clean tasks;
* calibration.csv / runs.csv did not exist yet (the CRC phase had not run).

Every correction is returned as a human-readable note; nothing is changed silently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from horizon.stage2.starvation import complete_cases, merge_small_styles
from horizon.theory import Stage2Inputs

NO_STAGE2 = ("none", "stage1")          # ablations without a policy engine


def _bin(x) -> np.ndarray:
    return (np.asarray(x, float) >= 0.5).astype(int)


def build_stage2_inputs(ep: pd.DataFrame, cal: pd.DataFrame | None, runs: pd.DataFrame | None,
                        min_cell: int) -> tuple[Stage2Inputs, list[str]]:
    """(inputs, notes). Raises ValueError with the reason when there is nothing to build a game from."""
    notes: list[str] = []
    ep = ep.copy()
    ep["ablation"] = ep["config"].astype(str).str.split("|").str[0]
    benign = ep[ep["style"] == "benign"]
    att = ep[ep["style"] != "benign"].copy()
    if att.empty:
        raise ValueError("no attacked episodes in episodes.csv")
    if len(benign):
        notes.append(f"{len(benign)} benign episodes excluded from the attack loss matrix (they carry no attack)")
    no_s2 = att["ablation"].isin(NO_STAGE2)
    if no_s2.any():
        att.loc[no_s2, "G"] = np.maximum(att.loc[no_s2, "G"].to_numpy(float), att.loc[no_s2, "asr"].to_numpy(float))
        notes.append("G := attack success for configurations without Stage 2 (no policy engine, so a successful "
                     "attack is a bypass by definition)")

    configs = list(dict.fromkeys(att["config"]))
    piv = {c: att.pivot_table(index="attack_id", columns="config", values=c)[configs] for c in "GCF"}
    rows, cols, n1 = complete_cases(piv["G"].to_numpy(float))
    notes += n1
    if not rows.any() or not cols.any():
        raise ValueError("no attack was run under every kept configuration: " + ("; ".join(n1) or "no data"))
    kept = [c for c, k in zip(configs, cols) if k]
    ids = piv["G"].index[rows]
    G, C, F = (_bin(piv[c].to_numpy(float)[np.ix_(rows, cols)]) for c in "GCF")

    styles_raw = list(dict.fromkeys(att["style"]))
    sty = att.drop_duplicates("attack_id").set_index("attack_id").loc[ids, "style"]
    style_of = sty.map({s: i for i, s in enumerate(styles_raw)}).to_numpy(int)
    style_of, styles, n2 = merge_small_styles(style_of, styles_raw, min_cell)
    notes += n2

    ub = benign.groupby("config")["utility"].mean().reindex(kept) if len(benign) else pd.Series(np.nan, index=kept)
    ua = att.groupby("config")["utility"].mean().reindex(kept)
    util = ub.where(ub.notna(), ua)
    if ub.isna().any():
        notes.append("no benign episodes for " + ", ".join(ub.index[ub.isna()]) + ": utility under attack used")

    if cal is not None and len(cal) and {"score", "dangerous"} <= set(cal.columns):
        scores, dangerous = cal["score"].to_numpy(float), cal["dangerous"].to_numpy().astype(bool)
    else:
        scores, dangerous = np.zeros(0), np.zeros(0, bool)
        notes.append("calibration.csv missing or empty: the CRC figure has no data")
    if runs is not None and len(runs) and {"attack_id", "run", "success"} <= set(runs.columns):
        run_success = runs.pivot_table(index="attack_id", columns="run", values="success").dropna().to_numpy()
    else:
        run_success = np.zeros((0, 1))
        notes.append("runs.csv missing or empty (no quarantined votes were recorded): the vote figures have no data")
    inp = Stage2Inputs([tuple(c.split("|")) for c in kept], styles, style_of, G, C, F, util.to_numpy(float),
                       run_success, scores, dangerous, False)
    return inp, notes
