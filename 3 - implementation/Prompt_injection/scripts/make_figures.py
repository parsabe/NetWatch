"""Regenerate ALL figures (F1-F18) from saved results, or in theory mode from parameters.

Every figure: governing formula inside the plot (mathtext) and in the caption,
parameter footer (α, n, ρ, m, seed, model, split), title, labelled axes, legend,
CP/bootstrap error bars where an empirical rate is shown; saved as PDF + PNG +
the plotted data as CSV in figures/.

    uv run python scripts/make_figures.py --theory          # math-only / simulated inputs
    uv run python scripts/make_figures.py                   # real results where they exist
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon import theory  # noqa: E402
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402
from horizon.stage1 import stats  # noqa: E402
from horizon.stage1.sion import solve_v1  # noqa: E402
from horizon.stage1.transforms import build_variants  # noqa: E402
from horizon.stage2 import schemas  # noqa: E402
from horizon.stage2.empirical import build_stage2_inputs  # noqa: E402
from horizon.stage2.horizon_value import solve_v_horizon_robust  # noqa: E402
from horizon.stage2.leakage import SIDE_CHANNEL_FLAG, config_leakage  # noqa: E402
from horizon.stage2.loss import bernstein_loss_bound, cp_loss_bound, per_attack_loss  # noqa: E402
from horizon.stage2.sion_lp import solve_sion_lp_robust  # noqa: E402
from horizon.stage2.vote import (chernoff_vote_bound, empirical_vote_fooling, exact_vote_fooling,  # noqa: E402
                                 hoeffding_vote_bound, mixture_vote_fooling, run_correlation)

# Reference categorical palette (dataviz skill, light mode), fixed order — never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
SEQ = ["#cfe1f8", "#9cc2ef", "#5f9be3", "#2a78d6", "#1a4f91"]   # one-hue sequential steps (light→dark)
OUT = ROOT / "figures"
LEFT, FOOT_Y, FORMULA_Y = 0.012, -0.025, 1.04   # figure-fraction layout positions
TICK_ROT = 65
CAPTIONS: dict[str, str] = {}
DATA_NOTES: list[str] = []          # what the small-sample safeguards changed (written next to the figures)
RUNS: dict[str, str | None] = {"stage1": None, "stage2": None}   # results sub-folders (default: run.split)

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2,
    "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight",
})


def color_of(names: list[str]) -> dict[str, str]:
    return {n: SERIES[i % len(SERIES)] for i, n in enumerate(names)}


def save(fig, name: str, data: pd.DataFrame, formula: str, caption: str, footer: str) -> None:
    """Formula box in the figure, footer with exact parameters, PDF + PNG + CSV."""
    fig.text(LEFT, FORMULA_Y, formula, ha="left", va="bottom", fontsize=9, color=INK,
             bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=GRID))
    bottom = fig.get_tightbbox(fig.canvas.get_renderer()).y0 / fig.get_figheight()   # below every tick label
    fig.text(LEFT, min(FOOT_Y, bottom + FOOT_Y), footer, ha="left", va="top", fontsize=7, color=INK2)
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png")
    data.to_csv(OUT / f"{name}.csv", index=False)
    plt.close(fig)
    CAPTIONS[name] = f"{caption} Formula: {formula}"


def footer_of(cfg, **kw) -> str:
    base = {"α": cfg.stage1["alpha"], "seed": cfg.run["seed"], "model": cfg.models["q_llm"],
            "split": cfg.run["split"]}
    base.update(kw)
    return "  ·  ".join(f"{k}={v}" for k, v in base.items())


# =============================================================== data loading ==
def stage1_inputs(cfg, mode: str) -> theory.Stage1Inputs:
    d = ROOT / "results" / "stage1" / (RUNS["stage1"] or cfg.run["split"])
    if mode == "results" and (d / "matrix.csv").exists():
        m = pd.read_csv(d / "matrix.csv")
        variants = build_variants(cfg.stage1)
        names = [t for t in variants if t in set(m["variant"])]
        A = m.pivot_table(index="attack_id", columns="variant", values="A")[names].dropna().to_numpy().astype(int)
        U = pd.read_csv(d / "clean.csv").groupby("variant")["utility"].mean().reindex(names).to_numpy()
        adp = pd.read_csv(d / "adaptive_eval.csv")
        s_adp = adp.groupby("variant")["A"].sum().reindex(names).fillna(0).to_numpy().astype(int)
        n_adp = int(adp.groupby("variant")["A"].count().reindex(names).min())
        return theory.Stage1Inputs(names, np.array([variants[t].log_key_space() for t in names]), U, A,
                                   A.mean(axis=0), A.sum(axis=0), A.shape[0], s_adp, n_adp, False)
    return theory.stage1_theory(cfg)


def tag(sim: bool) -> str:
    return "  [SIMULATED inputs — theory mode]" if sim else ""


# ================================================================= Stage 1 ==
def f1_forging(cfg):
    variants = build_variants(cfg.stage1)
    q = cfg.q_grid()
    rows = []
    fig, ax = plt.subplots(figsize=(7, 4.2))
    col = color_of(list(variants))
    for name, v in variants.items():
        y = [stats.forging_bound(qq, v.log_key_space()).log10 for qq in q]
        ax.plot(q, y, color=col[name], label=f"{name}  (log10|K|={v.log10_key_space():.1f})")
        rows += [{"variant": name, "q": qq, "log10_P_forge": yy} for qq, yy in zip(q, y)]
    s1 = cfg.stage1
    for k in s1["dyn_marker_k_list"]:
        lk = k * math.log(s1["dyn_marker_N"])
        y = [stats.forging_bound(qq, lk).log10 for qq in q]
        ax.plot(q, y, color=MUTED, lw=1, ls=":")
        ax.annotate(f"dyn. marker k={k}", (q[-1], y[-1]), fontsize=7, color=INK2, xytext=(3, 0),
                    textcoords="offset points", va="center")
        rows += [{"variant": f"dyn_marker_k{k}", "q": qq, "log10_P_forge": yy} for qq, yy in zip(q, y)]
    ax.set_xscale("log")
    ax.set_xlabel("attacker attempts q (count, log scale)")
    ax.set_ylabel(r"$\log_{10}$ P$_{forge}$ upper bound")
    ax.axhline(0, color=INK2, lw=1)
    ax.set_title("F1  Forging probability per spotlighting variant (Slide 13)", loc="left")
    ax.legend(fontsize=7, loc="lower left", bbox_to_anchor=(1.0, 0.0))
    save(fig, "F1_forging", pd.DataFrame(rows), r"$P_{forge}\leq q/|\mathcal{K}_\tau|$",
         "Union bound on forging the per-request key; key spaces in log-space (|Σ|! via lgamma).",
         footer_of(cfg, N=s1["dyn_marker_N"], k=s1["dyn_marker_k"], **{"|Σ|": len(s1["scramble_alphabet"])}))


def f2_static_asr(cfg, inp):
    a = cfg.stage1["alpha"]
    rows = []
    fig, ax = plt.subplots(figsize=(7, 4))
    col = color_of(inp.names)
    for i, t in enumerate(inp.names):
        s, n = int(inp.s[i]), inp.n
        lo, hi = stats.cp_interval(s, n, a)
        ub = stats.cp_upper(s, n, a)
        ax.bar(i, s / n, color=col[t], width=0.6)
        ax.errorbar(i, s / n, yerr=[[s / n - lo], [hi - s / n]], color=INK, capsize=3, lw=1)
        ax.plot(i, ub, marker="v", ms=8, color=INK, mec="white")
        rows.append({"variant": t, "s": s, "n": n, "p_hat": s / n, "cp_lo": lo, "cp_hi": hi, "p_bar": ub})
    ax.set_xticks(range(len(inp.names)), inp.names, rotation=30, ha="right")
    ax.set_ylabel(r"attack success rate $\hat p_\tau$ (fraction)")
    ax.set_title("F2  Static ASR with one-sided Clopper–Pearson upper bound ▼ (Slides 15–16)" + tag(inp.simulated),
                 loc="left")
    save(fig, "F2_static_asr", pd.DataFrame(rows), r"$\bar p_\tau=\mathrm{Beta}^{-1}(1-\alpha;\,s+1,\,n-s)$",
         "Bars: p̂_τ; whiskers: two-sided CP interval; triangles: one-sided CP upper bound p̄_τ.",
         footer_of(cfg, n=inp.n))


def f3_dro(cfg, inp):
    a = cfg.stage1["alpha"]
    rho = cfg.rho_grid()
    rows = []
    fig, ax = plt.subplots(figsize=(7, 4.2))
    col = color_of(inp.names)
    for i, t in enumerate(inp.names):
        pb = stats.cp_upper(int(inp.s[i]), inp.n, a)
        g = [stats.dro_chi2(pb, r) for r in rho]
        ax.plot(rho, g, color=col[t], label=t)
        sat = stats.dro_saturation_rho(pb)
        if sat <= rho[-1]:
            ax.plot(sat, 1.0, marker="o", ms=8, color=col[t], mec="white", mew=2)
        rows += [{"variant": t, "rho": r, "p_bar": pb, "g_rho": gg, "rho_saturation": sat} for r, gg in zip(rho, g)]
    ax.set_xlabel(r"$\chi^2$ shift budget $\rho$")
    ax.set_ylabel(r"worst-case ASR $g_\rho(\bar p_\tau)$ (fraction)")
    ax.set_title("F3  DRO bound vs shift budget; ● = saturation ρ=(1−p̄)/(2p̄) (Slides 17–19)" + tag(inp.simulated),
                 loc="left")
    ax.legend(fontsize=7, loc="lower left", bbox_to_anchor=(1.0, 0.0))
    save(fig, "F3_dro", pd.DataFrame(rows),
         r"$g_\rho(p)=p+\sqrt{2\rho\,p(1-p)}$  for  $\rho\leq\frac{1-p}{2p}$, else 1",
         "Closed-form χ²-DRO worst case of a binary success rate, applied to p̄_τ.", footer_of(cfg, n=inp.n))


def f4_adaptive(cfg):
    d = ROOT / "results" / "stage1" / (RUNS["stage1"] or cfg.run["split"])
    if not (d / "adaptive_trace.csv").exists():
        return print("  F4 skipped: needs results/stage1/<split>/adaptive_trace.csv (pilot or full run)")
    tr = pd.read_csv(d / "adaptive_trace.csv")
    ev = pd.read_csv(d / "adaptive_eval.csv")
    a = cfg.stage1["alpha"]
    names = list(dict.fromkeys(tr["variant"]))
    col = color_of(list(build_variants(cfg.stage1)))
    fig, ax = plt.subplots(figsize=(7, 4.2))
    rows = []
    for t in names:
        sub = tr[tr["variant"] == t]
        first = sub[sub["success"] == 1].groupby("target_id")["query"].min()
        ntar = sub["target_id"].nunique()
        qs = np.arange(1, cfg.stage1["attack_budget"] + 1)
        succ = [int((first <= q).sum()) for q in qs]
        lo, hi = zip(*[stats.cp_interval(s, ntar, a) for s in succ])
        y = np.array(succ) / ntar
        ax.plot(qs, y, color=col[t], label=t, marker="o", ms=4)
        ax.fill_between(qs, lo, hi, color=col[t], alpha=0.25, lw=0)
        e = ev[ev["variant"] == t]
        padp = stats.cp_upper(int(e["A"].sum()), len(e), a)
        ax.axhline(padp, color=col[t], lw=1, ls="--")
        rows += [{"variant": t, "query": q, "success_rate": yy, "cp_lo": l, "cp_hi": h, "p_adp_bar": padp}
                 for q, yy, l, h in zip(qs, y, lo, hi)]
    ax.set_xlabel("attacker-LLM queries (count)")
    ax.set_ylabel("targets broken so far (fraction)")
    ax.set_title("F4  Adaptive attack: success vs queries; dashed = p̄^adp_τ (Slide 20)", loc="left")
    ax.legend(fontsize=7, loc="lower left", bbox_to_anchor=(1.0, 0.0))
    save(fig, "F4_adaptive", pd.DataFrame(rows),
         r"$\delta^\star=\arg\max_{\delta\in\Delta_B}\mathbb{E}_\kappa[A(\delta,\kappa)]$,  $\bar p^{adp}_\tau=\mathrm{Beta}^{-1}(1-\alpha;s_a+1,n_a-s_a)$",
         "Cumulative fraction of targets broken within the attack budget (CP bands); fresh κ for every query.",
         footer_of(cfg, B=cfg.stage1["attack_budget"], n_a=len(ev) // max(1, len(names))))


def _risks(cfg, inp, q, rho):
    a = cfg.stage1["alpha"]
    out = {}
    for i, t in enumerate(inp.names):
        pb = stats.cp_upper(int(inp.s[i]), inp.n, a)
        pa = stats.cp_upper(int(inp.s_adp[i]), inp.n_adp, a)
        out[t] = stats.combined_risk(q, inp.log_K[i], pb, pa, rho)
    return out


def f5_combined(cfg, inp):
    rho = cfg.rho_grid()
    qs = cfg.stage1["q_report"]
    col = color_of(inp.names)
    fig, axes = plt.subplots(1, len(qs) + 1, figsize=(4 * (len(qs) + 1), 3.8))
    for ax in axes[1:len(qs)]:
        ax.sharey(axes[0])
    rows = []
    for ax, q in zip(axes, qs):
        for t in inp.names:
            r = [_risks(cfg, inp, q, rr)[t].total for rr in rho]
            ax.plot(rho, r, color=col[t], label=t)
            rows += [{"q": q, "variant": t, "rho": rr, "R_bar": v} for rr, v in zip(rho, r)]
        ax.set_title(f"q = {q:g}", loc="left")
        ax.set_xlabel(r"$\rho$")
    axes[0].set_ylabel(r"combined risk $\bar{\mathcal{R}}_\tau(\rho)$")
    axes[0].legend(fontsize=7, loc="upper center", bbox_to_anchor=(len(qs) / 2, -0.2), ncol=len(inp.names) // 2)
    ax = axes[-1]
    sel = [rho[len(rho) // 4], rho[len(rho) // 2], rho[-1]]
    width = 0.8 / len(sel)
    q = cfg.theory["q_main"]
    for j, rr in enumerate(sel):
        rk = _risks(cfg, inp, q, rr)
        x = np.arange(len(inp.names)) + j * width
        forge = np.array([min(1.0, rk[t].forging) for t in inp.names])
        dro = np.array([rk[t].dro for t in inp.names])
        adp = np.array([rk[t].adaptive for t in inp.names])
        top = np.maximum(dro, adp)
        ax.bar(x, forge, width * 0.9, color=SEQ[3], label="forging" if j == 0 else None)
        ax.bar(x, np.where(dro >= adp, top, 0), width * 0.9, bottom=forge, color=SEQ[1],
               label="DRO term (max branch)" if j == 0 else None)
        ax.bar(x, np.where(adp > dro, top, 0), width * 0.9, bottom=forge, color=SERIES[1],
               label="adaptive term (max branch)" if j == 0 else None)
        rows += [{"q": q, "rho_bar": rr, "variant": t, "forging": f, "dro": d, "adaptive": ad}
                 for t, f, d, ad in zip(inp.names, forge, dro, adp)]
    ax.axhline(1.0, color=INK, lw=1, ls="--")
    ax.annotate("cap: R̄ = min{1, ·}", (0, 1.0), xytext=(0, 3), textcoords="offset points", fontsize=7, color=INK2)
    ax.set_ylabel("uncapped terms (forging clipped at 1)")
    ax.set_xticks(np.arange(len(inp.names)) + width, inp.names, rotation=TICK_ROT, ha="right", fontsize=7)
    ax.set_title(f"decomposition, q={q:g}, ρ∈{{{', '.join(f'{s:.2g}' for s in sel)}}}", loc="left", fontsize=8)
    ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("F5  Combined risk per variant vs ρ, and its decomposition (Slide 21)" + tag(inp.simulated),
                 x=LEFT, ha="left")
    save(fig, "F5_combined_risk", pd.DataFrame(rows),
         r"$\bar{\mathcal{R}}_\tau(\rho)=\min\{1,\,q/|\mathcal{K}_\tau|+\max(g_\rho(\bar p_\tau),\bar p^{adp}_\tau)\}$",
         "Main curves per q; right: stacked decomposition at three ρ values (bars grouped by ρ).",
         footer_of(cfg, n=inp.n, n_a=inp.n_adp))


def f6_risk_utility(cfg, inp):
    q, rho = cfg.theory["q_main"], cfg.theory["rho_main"]
    rk = _risks(cfg, inp, q, rho)
    u_none = float(inp.U[inp.names.index("none")])
    col = color_of(inp.names)
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    rows = []
    for j, eta in enumerate(sorted(cfg.stage1["eta_list"], reverse=True)):
        ax.axvspan(u_none - eta, max(1.0, u_none), color=SEQ[j % len(SEQ)], alpha=0.25, lw=0,
                   label=f"feasible U ≥ U_none − η, η={eta:g}")
    for i, t in enumerate(inp.names):
        ax.plot(inp.U[i], rk[t].total, marker="o", ms=9, color=col[t], mec="white", mew=2, ls="")
        ax.annotate(t, (inp.U[i], rk[t].total), fontsize=7, color=INK, xytext=(5, 3), textcoords="offset points")
        rows.append({"variant": t, "U": inp.U[i], "R_bar": rk[t].total})
    for eta in cfg.stage1["eta_list"]:
        best = stats.choose_tau({t: rk[t].total for t in inp.names}, dict(zip(inp.names, inp.U)), u_none, eta)
        if best:
            i = inp.names.index(best)
            ax.plot(inp.U[i], rk[best].total, marker="*", ms=16, color=INK, ls="")
            rows.append({"variant": f"tau_star(eta={eta})", "U": inp.U[i], "R_bar": rk[best].total})
    ax.set_xlabel(r"clean-task utility $U_\tau$ (token-F1, fraction)")
    ax.set_ylabel(r"combined risk $\bar{\mathcal{R}}_\tau(\rho)$")
    ax.set_title("F6  Risk–utility plane; ★ = τ* per η (Slide 22)" + tag(inp.simulated), loc="left")
    ax.legend(fontsize=7, loc="upper left")
    save(fig, "F6_risk_utility", pd.DataFrame(rows),
         r"$\tau^\star=\arg\min_\tau\bar{\mathcal{R}}_\tau(\rho)\ \ \mathrm{s.t.}\ U_\tau\geq U_{none}-\eta$",
         "Each point is one defense; shaded bands are the feasible regions for each η.",
         footer_of(cfg, n=inp.n, q=q, ρ=rho))


def v1_sweep(cfg, inp, q):
    a = cfg.stage1["alpha"]
    padp = np.array([stats.cp_upper(int(s), inp.n_adp, a) for s in inp.s_adp])
    u_none = float(inp.U[inp.names.index("none")])
    out = []
    for eta in cfg.stage1["eta_list"]:
        for rr in cfg.rho_grid():
            res = solve_v1(inp.A, inp.names, inp.U, u_none, eta, q, inp.log_K, padp, rr, cfg.stage1["solver"])
            out.append({"eta": eta, "rho": rr, "V1": res.value, **{f"pi_{t}": res.pi.get(t, np.nan) for t in inp.names}})
    return pd.DataFrame(out)


def f7_v1(cfg, inp):
    q = cfg.theory["q_main"]
    df = v1_sweep(cfg, inp, q)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    for j, eta in enumerate(cfg.stage1["eta_list"]):
        sub = df[df["eta"] == eta]
        ax1.plot(sub["rho"], sub["V1"], color=SEQ[(j + 1) % len(SEQ)], label=f"η={eta:g}")
    ax1.set_xlabel(r"$\rho$")
    ax1.set_ylabel(r"game value $V_1(\rho,\eta)$")
    ax1.legend(fontsize=7)
    eta0 = cfg.theory["eta_main"]
    sub = df[df["eta"] == eta0].fillna(0.0)
    col = color_of(inp.names)
    ax2.stackplot(sub["rho"], *[sub[f"pi_{t}"] for t in inp.names], colors=[col[t] for t in inp.names],
                  labels=inp.names, edgecolor="white", linewidth=0.5)
    ax2.set_xlabel(r"$\rho$")
    ax2.set_ylabel(rf"$\pi^\star_\tau$ (probability), η={eta0:g}")
    ax2.legend(fontsize=7, loc="lower left", bbox_to_anchor=(1.0, 0.0))
    fig.suptitle(f"F7  Sion game V1 vs ρ and the optimal mix π* (q={q:g}) (Slides 24, 30)" + tag(inp.simulated),
                 x=LEFT, ha="left")
    save(fig, "F7_v1", df,
         r"$V_1=\min_{\pi\in\Pi_\eta}\min\{1,\sum_\tau\pi_\tau\frac{q}{|\mathcal{K}_\tau|}+\max(\mathbb{E}_P[L_\pi]+\sqrt{2\rho\mathrm{Var}_P(L_\pi)},\sum_\tau\pi_\tau\bar p^{adp}_\tau)\}$",
         "SOCP solution per (ρ, η); right: composition of π* as ρ increases.", footer_of(cfg, n=inp.n, q=q))


def f8_mcnemar(cfg, inp):
    T = len(inp.names)
    P = np.ones((T, T))
    rows = []
    for i in range(T):
        for j in range(T):
            if i != j:
                P[i, j] = stats.mcnemar_pvalue(inp.A[:, i], inp.A[:, j])
            rows.append({"a": inp.names[i], "b": inp.names[j], "p_value": P[i, j]})
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(np.log10(np.clip(P, 1e-12, 1)), cmap="Blues_r", vmin=-12, vmax=0)
    ax.set_xticks(range(T), inp.names, rotation=45, ha="right")
    ax.set_yticks(range(T), inp.names)
    ax.grid(False)
    for i in range(T):
        for j in range(T):
            ax.text(j, i, f"{P[i, j]:.0e}" if P[i, j] < 1 else "1", ha="center", va="center", fontsize=6,
                    color="white" if P[i, j] < 1e-6 else INK)
    fig.colorbar(im, ax=ax, label=r"$\log_{10}$ p-value (exact McNemar)")
    ax.set_title("F8  Paired McNemar tests between variants (Slide 27)" + tag(inp.simulated), loc="left")
    save(fig, "F8_mcnemar", pd.DataFrame(rows), r"exact McNemar: $b\sim\mathrm{Bin}(b+c,\frac{1}{2})$",
         "Paired comparison on the same attacks; small p = the two variants fail on different attacks.",
         footer_of(cfg, n=inp.n))


# ================================================================= Stage 2 ==
def stage1_handoff(cfg, inp):
    """R̄_Q and |K| from π* at the main (ρ, η, q) — the Stage-2 inputs (Slide 32)."""
    p = ROOT / "results" / "stage1" / "pi_star.json"
    if p.exists():
        d = json.loads(p.read_text())
        return d["R_Q"], d["log_K"], False
    from horizon.stage1.sion import stage1_output
    q, rho, eta = cfg.theory["q_main"], cfg.theory["rho_main"], cfg.theory["eta_main"]
    a = cfg.stage1["alpha"]
    padp = np.array([stats.cp_upper(int(s), inp.n_adp, a) for s in inp.s_adp])
    res = solve_v1(inp.A, inp.names, inp.U, float(inp.U[inp.names.index("none")]), eta, q, inp.log_K, padp, rho,
                   cfg.stage1["solver"])
    rk = {t: r.total for t, r in _risks(cfg, inp, q, rho).items()}
    out = stage1_output(res, rk, dict(zip(inp.names, inp.log_K)), [], cfg.stage1["support_tol"], rho, eta, q)
    return out.R_Q, out.log_K, True


def _read_or_none(p: Path):
    try:
        return pd.read_csv(p) if p.exists() and p.stat().st_size else None
    except pd.errors.EmptyDataError:
        return None


def stage2_inputs(cfg, R_Q):
    d = ROOT / "results" / "stage2" / (RUNS["stage2"] or cfg.run["split"])
    if (d / "episodes.csv").exists():
        inp, notes = build_stage2_inputs(pd.read_csv(d / "episodes.csv"), _read_or_none(d / "calibration.csv"),
                                         _read_or_none(d / "runs.csv"), cfg.stage2.get("sion_min_cell", 20))
        for n in notes:
            print(f"  [stage 2 data] {n}", flush=True)
        DATA_NOTES.extend(notes)
        return inp
    return theory.stage2_theory(cfg, R_Q)


def ckey(c) -> str:
    """Theory configs are (m, parser, policy); measured ones are the episode tag split on '|':
    (ablation, 'm=…', parser, policy) — the label is then the full tag."""
    return "|".join(map(str, c)) if len(c) == 4 else f"m={c[0]}|{c[1]}|{c[2]}"


def parser_of(c) -> str:
    return c[2] if len(c) == 4 else c[1]


def loss_ub(cfg, G, C, F) -> float:
    """Upper bound of the mean loss per stage2.loss_ci (bonferroni: joint 1−α; bernstein; none: per component)."""
    a, h, al = cfg.stage2["user_mistake_rate"], cfg.stage2["harm_rate"], cfg.stage1["alpha"]
    mode = cfg.stage2.get("loss_ci", "none")
    if mode == "bernstein":
        return bernstein_loss_bound(G, C, F, a, h, al)
    return cp_loss_bound(G, C, F, a, h, al, "bonferroni" if mode == "bonferroni" else "none").total


def f9_hoeffding(cfg, s2, R_Q, simulated):
    m_list = cfg.stage2["m_list"]
    a = cfg.stage1["alpha"]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    rows = []
    for j, r in enumerate(cfg.stage2["hoeffding_R_list"]):
        b = [hoeffding_vote_bound(m, r) for m in m_list]
        y = [v if isinstance(v, float) else np.nan for v in b]
        ax.plot(m_list, y, color=SEQ[j % len(SEQ)], lw=1.5, label=rf"bound, $\bar{{\mathcal{{R}}}}_Q$={r:g}")
        ax.annotate(f"R̄_Q={r:g}", (m_list[-1], y[-1]), xytext=(4, 0), textcoords="offset points",
                    fontsize=7, color=INK2, va="center")
        rows += [{"series": f"bound_R={r}", "m": m, "value": v} for m, v in zip(m_list, y)]
    R = s2.run_success.shape[1]
    ms = [m for m in m_list if m <= R]
    emp = [empirical_vote_fooling(s2.run_success, m) for m in ms]
    k = [int(e.sum()) for e in emp]
    n = s2.run_success.shape[0]
    lo, hi = zip(*[stats.cp_interval(kk, n, a) for kk in k])
    y = np.array(k) / n
    ax.errorbar(ms, y, yerr=[y - np.array(lo), np.array(hi) - y], color=SERIES[1], marker="o", ms=6, capsize=3,
                lw=2, label="empirical P(vote fooled) ± CP")
    rows += [{"series": "empirical", "m": m, "value": v, "cp_lo": l, "cp_hi": h} for m, v, l, h in zip(ms, y, lo, hi)]
    if R_Q < 0.5:
        ch = [chernoff_vote_bound(m, R_Q) for m in m_list]
        ex = [exact_vote_fooling(m, R_Q) for m in m_list]
        ax.plot(m_list, [v if isinstance(v, float) else np.nan for v in ch], color=INK2, ls="--", lw=1.2,
                label=r"Chernoff–KL bound at $\bar{\mathcal{R}}_Q$")
        ax.plot(m_list, ex, color=INK2, ls=":", lw=1.2, label=r"exact binomial at $\bar{\mathcal{R}}_Q$")
        rows += [{"series": "chernoff_RQ", "m": m, "value": v if isinstance(v, float) else np.nan}
                 for m, v in zip(m_list, ch)]
        rows += [{"series": "exact_RQ", "m": m, "value": v} for m, v in zip(m_list, ex)]
    mix = [mixture_vote_fooling(s2.run_success, m) for m in m_list]
    ax.plot(m_list, mix, color=SERIES[2], lw=1.5, marker=".", label="mixture over measured p(x) (keeps correlation)")
    rows += [{"series": "mixture", "m": m, "value": v} for m, v in zip(m_list, mix)]
    rows += [{"series": "icc_runs", "m": R, "value": run_correlation(s2.run_success)}]
    bQ = hoeffding_vote_bound(1, R_Q)
    ax.set_yscale("log")
    ax.set_xlabel("number of quarantined runs m")
    ax.set_ylabel("P(vote fooled)")
    ax.set_title(f"F9  Hoeffding bound vs empirical vote-fooling (R̄_Q={R_Q:.3f}"
                 f"{'' if isinstance(bQ, float) else ', no guarantee'}) (Slide 36)" + tag(simulated), loc="left")
    ax.legend(fontsize=7, loc="lower left")
    save(fig, "F9_hoeffding", pd.DataFrame(rows),
         r"$P(\mathrm{vote\ fooled})\leq\exp(-2m(\frac{1}{2}-\bar{\mathcal{R}}_Q)^2)$, $\bar{\mathcal{R}}_Q<\frac{1}{2}$",
         "Bound assumes independent runs with per-attack failure < ½; runs on the same text are correlated, "
         "so the empirical rate is shown alongside.", footer_of(cfg, n=n, runs=R))


def f10_failure_hist(cfg, s2, simulated):
    p = s2.run_success.mean(axis=1)
    fig, ax = plt.subplots(figsize=(6.5, 4))
    bins = np.linspace(0, 1, s2.run_success.shape[1] + 2)
    ax.hist(p, bins=bins, color=SERIES[0], edgecolor="white", linewidth=2)
    ax.axvline(0.5, color=SERIES[7], lw=2)
    frac = float((p >= 0.5).mean())
    ax.annotate(f"{frac:.1%} of attacks have p(x) ≥ ½\n(voting cannot help them)", (0.5, ax.get_ylim()[1] * 0.85),
                xytext=(8, 0), textcoords="offset points", fontsize=8, color=INK)
    ax.set_xlabel("per-attack single-run failure rate p(x) (fraction)")
    ax.set_ylabel("attacks (count)")
    ax.set_title("F10  Where the Hoeffding assumption breaks (Slide 36)" + tag(simulated), loc="left")
    save(fig, "F10_failure_hist", pd.DataFrame({"p_x": p}), r"$p(x)=\frac{1}{R}\sum_{r}A_r(x)$, Hoeffding needs $p(x)<\frac{1}{2}$",
         "Histogram of per-attack failure rates over independent keys.",
         footer_of(cfg, n=len(p), runs=s2.run_success.shape[1]))


def f11_conformal(cfg, s2, simulated):
    res = theory.crc_theory(cfg, s2)
    alpha_u = cfg.stage2["alpha_user"]
    n = len(s2.scores)
    half = n // 2
    from horizon.stage2.conformal import calibrate_lambda, crc_losses
    a_ = cfg.stage2["user_mistake_rate"]
    cal = calibrate_lambda(s2.scores[:half], s2.dangerous[:half], a_, alpha_u, cfg.lambda_grid(),
                           a_ if cfg.stage2.get("crc_loss_bound") == "a" else 1.0)
    realized = [crc_losses(s2.scores[half:], s2.dangerous[half:], lam, cfg.stage2["user_mistake_rate"]).mean()
                for lam in cal.grid]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(cal.grid, cal.risk_curve, color=SERIES[0], label=r"empirical loss $\hat L_n(\lambda)$ (calibration)")
    ax.plot(cal.grid, cal.bound_curve, color=SERIES[0], ls="--", lw=1.5, label=r"$\frac{n}{n+1}\hat L_n+\frac{B}{n+1}$")
    ax.plot(cal.grid, realized, color=SERIES[1], label="realized risk (report split)")
    ax.axhline(alpha_u, color=INK2, lw=1, ls=":", label=r"$\alpha_{user}$")
    if math.isfinite(cal.lambda_hat):
        ax.axvline(cal.lambda_hat, color=INK, lw=1.5)
        ax.annotate(rf"$\hat\lambda$={cal.lambda_hat:.2f}", (cal.lambda_hat, alpha_u), xytext=(5, 8),
                    textcoords="offset points", fontsize=8)
    ax.set_xlabel(r"threshold $\lambda$ on vote agreement s(x)")
    ax.set_ylabel("P(user approves a dangerous call)")
    ax.set_title("F11  Conformal risk control of the confirmation threshold (Slide 39)" + tag(simulated), loc="left")
    ax.legend(fontsize=7)
    df = pd.DataFrame({"lambda": cal.grid, "risk_cal": cal.risk_curve, "bound": cal.bound_curve,
                       "risk_report": realized, "lambda_hat": cal.lambda_hat})
    save(fig, "F11_conformal", df,
         r"$\hat\lambda=\inf\{\lambda:\frac{n}{n+1}\hat L_n(\lambda)+\frac{B}{n+1}\leq\alpha_{user}\}$, $L_i=a\,\mathbb{1}[\mathrm{dang}_i\wedge s_i\geq\lambda]$",
         "Ask user iff s(x) ≥ λ, otherwise DENY. Calibrated on one half, realized risk on the other.",
         footer_of(cfg, n_cal=half, a=cfg.stage2["user_mistake_rate"], **{"α_user": alpha_u}))
    return res


def loss_tables(cfg, s2):
    a, h, al = cfg.stage2["user_mistake_rate"], cfg.stage2["harm_rate"], cfg.stage1["alpha"]
    L = np.column_stack([per_attack_loss(s2.G[:, j], s2.C[:, j], s2.F[:, j], a, h) for j in range(len(s2.configs))])
    mean_cp = np.array([loss_ub(cfg, s2.G[:, j], s2.C[:, j], s2.F[:, j]) for j in range(len(s2.configs))])
    K = len(s2.styles)
    lbar = np.zeros((K, len(s2.configs)))
    for k in range(K):
        idx = s2.style_of == k
        for j in range(len(s2.configs)):
            lbar[k, j] = loss_ub(cfg, s2.G[idx, j], s2.C[idx, j], s2.F[idx, j])
    return L, mean_cp, lbar


def f12_loss(cfg, s2, simulated):
    a, h, al = cfg.stage2["user_mistake_rate"], cfg.stage2["harm_rate"], cfg.stage1["alpha"]
    names = [ckey(c) for c in s2.configs]
    G = s2.G.mean(axis=0)
    C = a * s2.C.mean(axis=0)
    F = h * s2.F.mean(axis=0)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    x = np.arange(len(names))
    ax.bar(x, G, color=SERIES[7], label="G (policy bypassed)", edgecolor="white", linewidth=1)
    ax.bar(x, C, bottom=G, color=SERIES[3], label="a·C (reached ask-user)", edgecolor="white", linewidth=1)
    ax.bar(x, F, bottom=G + C, color=SERIES[0], label="h·F (vote+parser fooled)", edgecolor="white", linewidth=1)
    ub = [loss_ub(cfg, s2.G[:, j], s2.C[:, j], s2.F[:, j]) for j in range(len(names))]
    ax.plot(x, ub, marker="v", ls="", color=INK, ms=6, label="CP upper bound of mean loss")
    ax.set_xticks(x, names, rotation=75, ha="right", fontsize=6)
    ax.set_ylabel(r"mean loss $\ell_\theta$")
    ax.set_title("F12  Loss decomposition per configuration θ=(m, parser, policy) (Slide 40)" + tag(simulated),
                 loc="left")
    ax.legend(fontsize=7)
    save(fig, "F12_loss", pd.DataFrame({"config": names, "G": G, "aC": C, "hF": F, "cp_upper": ub}),
         r"$\ell_\theta(x)=G_\theta(x)+a\,C_\theta(x)+h\,F_\theta(x)$",
         "Stacked mean loss components; triangles: p̄_G + a p̄_C + h p̄_F.",
         footer_of(cfg, n=s2.G.shape[0], a=a, h=h))


def leakage_bits(cfg, s2):
    from pydantic import BaseModel, EmailStr  # noqa: F401

    class ExtractedEmail(BaseModel):
        email: str

    class ExtractedDate(BaseModel):
        date: str

    class YesNo(BaseModel):
        answer: bool

    reps = [ExtractedEmail, ExtractedDate, YesNo]
    return {p: config_leakage(reps, p, cfg.stage2).total for p in cfg.stage2["parsers"]}


def f13_lp(cfg, s2, simulated):
    _, _, lbar = loss_tables(cfg, s2)
    names = [ckey(c) for c in s2.configs]
    leak = leakage_bits(cfg, s2)
    lv = np.array([leak[parser_of(c)] for c in s2.configs])
    r = solve_sion_lp_robust(lbar, s2.styles, names, s2.utility, cfg.stage2["U_min"], lv,
                             cfg.stage2["leakage_cap_bits"])
    for n in r.notes:
        print(f"  [F13] {n}", flush=True)
    DATA_NOTES.extend(f"F13: {n}" for n in r.notes)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [3, 1]})
    if r.value is None:
        ax1.text(0.5, 0.5, f"no game value: {r.status}", ha="center",
                 transform=ax1.transAxes, color=INK2)
    else:
        ax1.bar(np.arange(len(names)), [r.pi.get(n, 0.0) for n in names], color=SERIES[0])   # dropped configs: 0
        ax1.set_xticks(np.arange(len(names)), names, rotation=75, ha="right", fontsize=6)
        ax2.bar(np.arange(len(s2.styles)), [r.w.get(s, 0.0) for s in s2.styles], color=SERIES[1])
        ax2.set_xticks(np.arange(len(s2.styles)), s2.styles, rotation=45, ha="right", fontsize=7)
        ax1.annotate(f"game value = {r.value:.4f}", (LEFT, 0.92), xycoords="axes fraction", fontsize=9)
    ax1.set_ylabel(r"$\pi^\star_{Horizon}$ (probability)")
    ax2.set_ylabel(r"$w^\star$ (attacker mix)")
    fig.suptitle("F13  Sion LP: defender mix π*_Horizon and attacker worst case w* (Slide 41)" + tag(simulated),
                 x=LEFT, ha="left")
    df = pd.DataFrame([{"kind": "pi", "name": k, "value": v} for k, v in r.pi.items()] +
                      [{"kind": "w", "name": k, "value": v} for k, v in r.w.items()] +
                      [{"kind": "game_value", "name": "", "value": r.value}] +
                      [{"kind": "note", "name": n, "value": np.nan} for n in r.notes])
    save(fig, "F13_sion_lp", df, r"$\min_{\pi\in\Pi}\max_{w\in\mathcal{W}}\ \pi^\top\bar\ell\,w$",
         "ℓ̄[k,θ] = CP upper bound of the loss of style k under θ; Π: utility ≥ U_min, per-config leakage cap.",
         footer_of(cfg, U_min=cfg.stage2["U_min"], cap_bits=cfg.stage2["leakage_cap_bits"]))


def f14_v_horizon(cfg, s2, log_K, simulated):
    L, mean_cp, _ = loss_tables(cfg, s2)
    names = [ckey(c) for c in s2.configs]
    leak = leakage_bits(cfg, s2)
    lv = np.array([leak[parser_of(c)] for c in s2.configs])
    logk = np.full(len(names), log_K)
    adp = mean_cp.copy()  # ℓ̄^adp := static bound, except where Stage-2 adaptive runs exist (full pipeline)
    tr = ROOT / "results" / "stage2" / (RUNS["stage2"] or cfg.run["split"]) / "adaptive_trace.csv"
    if tr.exists() and not simulated:
        t = pd.read_csv(tr)
        hit = t.groupby(["config", "suite", "user_task", "injection_task"])["success"].max()
        for j, c in enumerate(s2.configs):
            if len(c) == 4 and c[0] == "full" and len(hit):
                adp[j] = max(adp[j], stats.cp_upper(int(hit.sum()), len(hit), cfg.stage1["alpha"]))
    q = cfg.theory["q_main"]
    s2c = cfg.stage2

    def solve(rho, cap, umin):
        return solve_v_horizon_robust(L, names, mean_cp, adp, q, logk, rho, s2.utility, umin, lv, cap,
                                      cfg.stage1["solver"]).value

    rows = []
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
    rho = cfg.rho_grid()
    v = [np.nan if (x := solve(r, s2c["leakage_cap_bits"], s2c["U_min"])) is None else x for r in rho]
    axes[0].plot(rho, v, color=SERIES[0])
    axes[0].set_xlabel(r"$\rho$")
    rows += [{"sweep": "rho", "x": x, "V": y} for x, y in zip(rho, v)]
    caps = s2c["leakage_cap_bits_list"]
    v = [np.nan if (x := solve(cfg.theory["rho_main"], c, s2c["U_min"])) is None else x for c in caps]
    axes[1].plot(caps, v, color=SERIES[1], marker="o", ms=6)
    axes[1].set_xscale("log", base=2)
    axes[1].set_xlabel("leakage_cap_bits (bits)")
    rows += [{"sweep": "leakage_cap_bits", "x": x, "V": y} for x, y in zip(caps, v)]
    um = s2c["U_min_list"]
    v = [np.nan if (x := solve(cfg.theory["rho_main"], s2c["leakage_cap_bits"], u)) is None else x for u in um]
    axes[2].plot(um, v, color=SERIES[2], marker="o", ms=6)
    axes[2].set_xlabel(r"$U_{min}$ (utility floor)")
    rows += [{"sweep": "U_min", "x": x, "V": y} for x, y in zip(um, v)]
    for ax in axes:
        if not ax.get_lines() or all(np.all(np.isnan(np.asarray(l.get_ydata(), float))) for l in ax.get_lines()):
            ax.text(0.5, 0.5, "Π infeasible for every value", ha="center", transform=ax.transAxes, color=INK2)
    axes[0].set_ylabel(r"$V_{Horizon}$ (gap = infeasible Π)")
    fig.suptitle("F14  V_Horizon vs ρ, leakage cap and utility floor (Slide 42)" + tag(simulated), x=LEFT, ha="left")
    save(fig, "F14_v_horizon", pd.DataFrame(rows),
         r"$V_{H}=\min_{\pi\in\Pi}\min\{1,\sum_\theta\pi_\theta\frac{q}{|\mathcal{K}_\theta|}+\max(\mathbb{E}_P[L_\pi]+\sqrt{2\rho\mathrm{Var}_P(L_\pi)},\sum_\theta\pi_\theta\bar\ell^{adp}_\theta)\}$",
         "E-term = CP bounds p̄_G + a p̄_C + h p̄_F; Var from per-attack losses (pending author confirmation). "
         "ℓ̄^adp equals the static bound until Stage-2 adaptive runs exist.",
         footer_of(cfg, n=L.shape[0], q=q, ρ=cfg.theory["rho_main"]))


def f15_leakage(cfg, s2, simulated):
    leak = leakage_bits(cfg, s2)
    names = list(leak)
    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.bar(range(len(names)), [leak[n] for n in names], color=[SERIES[i] for i in range(len(names))], width=0.5)
    ax.axhline(cfg.stage2["leakage_cap_bits"], color=SERIES[7], lw=2, label="leakage_cap_bits")
    for i, n in enumerate(names):
        ax.annotate(f"{leak[n]:.0f} bits", (i, leak[n]), ha="center", xytext=(0, 3), textcoords="offset points",
                    fontsize=8)
    ax.set_yscale("log", base=2)
    ax.set_xticks(range(len(names)), [f"parser={n}" for n in names])
    ax.set_ylabel("worst-case leakage (bits, log2 scale)")
    ax.set_title("F15  Schema leakage per parser mode vs cap (Slide 41)", loc="left")
    ax.legend(fontsize=7)
    save(fig, "F15_leakage", pd.DataFrame({"parser": names, "bits": [leak[n] for n in names],
                                           "cap": cfg.stage2["leakage_cap_bits"]}),
         r"$\mathrm{leak}(\theta)=\sum_{outputs}\log_2|S|+L_{side}$",
         f"Representative schemas (email, date, bool). {SIDE_CHANNEL_FLAG}.",
         footer_of(cfg, side_bits=cfg.stage2["side_channel_bits"]))


# ============================================================ testing phase ==
def f16_f18(cfg):
    d = ROOT / "results" / "testing"
    if (d / "ablation.csv").exists():
        df = pd.read_csv(d / "ablation.csv")
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
        for ax, col in zip(axes, ["asr", "utility"]):
            for j, kind in enumerate(sorted(df["attack_kind"].unique())):
                sub = df[df["attack_kind"] == kind]
                x = np.arange(len(sub)) + j * 0.4
                ax.bar(x, sub[col], 0.38, color=SERIES[j], label=kind)
                if f"{col}_lo" in sub:
                    ax.errorbar(x, sub[col], yerr=[sub[col] - sub[f"{col}_lo"], sub[f"{col}_hi"] - sub[col]],
                                ls="", color=INK, capsize=3)
            ax.set_xticks(np.arange(len(sub)) + 0.2, sub["ablation"], rotation=30)
            ax.set_ylabel(col)
            ax.legend(fontsize=7)
        fig.suptitle("F16  ASR and utility per ablation", x=LEFT, ha="left")
        save(fig, "F16_ablation", df, r"ASR $=\frac{1}{n}\sum_i A_i$", "Static vs adaptive per ablation.",
             footer_of(cfg))
    else:
        print("  F16 skipped: needs results/testing/ablation.csv (eval/agentdojo_runner.py)")
    if (d / "queries.csv").exists():
        df = pd.read_csv(d / "queries.csv")
        fig, ax = plt.subplots(figsize=(7, 4))
        for j, (k, sub) in enumerate(df.groupby("ablation")):
            ax.plot(sub["queries"], sub["asr"], color=SERIES[j % len(SERIES)], label=k)
        ax.set_xscale("log")
        ax.set_xlabel("attacker queries")
        ax.set_ylabel("ASR")
        ax.legend(fontsize=7)
        ax.set_title("F17  ASR vs number of queries", loc="left")
        save(fig, "F17_asr_queries", df, r"ASR$(q)$", "As in Fig. 3 of 'The Attacker Moves Second'.",
             footer_of(cfg))
    else:
        print("  F17 skipped: needs results/testing/queries.csv")
    if (d / "direct.csv").exists():
        df = pd.read_csv(d / "direct.csv")
        fig, ax = plt.subplots(figsize=(6, 4))
        for j, (k, sub) in enumerate(df.groupby("detector")):
            ax.plot(sub["fpr"], sub["tpr"], marker="o", color=SERIES[j], label=k)
        ax.set_xlabel("false-positive rate (benign sets)")
        ax.set_ylabel("detection rate (attack sets)")
        ax.legend(fontsize=7)
        ax.set_title("F18  Direct layer: detection vs FPR", loc="left")
        save(fig, "F18_direct", df, r"TPR vs FPR", "Per detector / threshold.", footer_of(cfg))
    else:
        print("  F18 skipped: needs results/testing/direct.csv (eval/jailbreak_runner.py)")


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--theory", action="store_true", help="math-only / simulated inputs, no LLM results needed")
    ap.add_argument("--stage1-run", help="results/stage1/<name>, e.g. fit_pilot")
    ap.add_argument("--stage2-run", help="results/stage2/<name>")
    ap.add_argument("--out", default="figures", help="output folder (relative to the project root)")
    args = ap.parse_args()
    RUNS.update(stage1=args.stage1_run, stage2=args.stage2_run)
    global OUT
    OUT = ROOT / args.out
    cfg = config_from_args(args)
    mode = "theory" if args.theory else "results"
    print(f"figures → {OUT}  (mode={mode})")
    inp = stage1_inputs(cfg, mode)
    f1_forging(cfg)
    f2_static_asr(cfg, inp)
    f3_dro(cfg, inp)
    f4_adaptive(cfg)
    f5_combined(cfg, inp)
    f6_risk_utility(cfg, inp)
    f7_v1(cfg, inp)
    f8_mcnemar(cfg, inp)
    R_Q, log_K, sim_handoff = stage1_handoff(cfg, inp)
    try:
        s2 = stage2_inputs(cfg, R_Q) if mode == "results" else theory.stage2_theory(cfg, R_Q)
    except Exception as e:                      # no / unusable Stage 2 data: say why, keep every other figure
        print(f"  Stage 2 figures skipped: {type(e).__name__}: {e}", flush=True)
        s2 = None
    if s2 is not None:
        sim = s2.simulated
        # one starved figure must not abort the others (an empty calibration set, a single configuration, ...)
        for name, fn, fargs in (("F9", f9_hoeffding, (cfg, s2, R_Q, sim)), ("F10", f10_failure_hist, (cfg, s2, sim)),
                               ("F11", f11_conformal, (cfg, s2, sim)), ("F12", f12_loss, (cfg, s2, sim)),
                               ("F13", f13_lp, (cfg, s2, sim)), ("F14", f14_v_horizon, (cfg, s2, log_K, sim)),
                               ("F15", f15_leakage, (cfg, s2, sim))):
            try:
                fn(*fargs)
            except Exception as e:
                plt.close("all")
                msg = f"{name} skipped: {type(e).__name__}: {e}"
                print(f"  {msg}", flush=True)
                DATA_NOTES.append(msg)
    f16_f18(cfg)
    (OUT / "captions.json").write_text(json.dumps(CAPTIONS, indent=2, ensure_ascii=False))
    if DATA_NOTES:                              # what the small-sample safeguards changed or skipped
        (OUT / "data_notes.txt").write_text("\n".join(dict.fromkeys(DATA_NOTES)) + "\n")
    print(f"wrote {len(CAPTIONS)} figures")


if __name__ == "__main__":
    main()
