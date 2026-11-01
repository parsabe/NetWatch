"""Charts for the campaign report (one run) and for the model comparison (several runs).

    from horizon.charts import run_charts, compare_charts

Both read the tidy table the campaign writes (report/campaign/results_by_group.csv: one row per area · group ·
configuration with attacks, got_through, benign, benign_ok, utility, false_alarms) and return
[(png path, caption), …] for the Markdown / LaTeX report. Static PNG + PDF (the reports are PDF and Markdown).

Design (one system for every chart):
  * horizontal bars — configuration names are long; share of attacks that GOT THROUGH (lower = better defence) with
    the 95 % Clopper–Pearson interval as a whisker, and "k/n" next to each bar (the samples of a test run are small,
    the reader must see n);
  * one measure per axis (attack success and utility are separate panels, never a second y-axis);
  * colour = identity only: a single run is one blue; in a comparison every model keeps its own colour in every
    chart (slot 1 blue, slot 2 orange, … — the validated categorical order of the dataviz reference palette);
  * recessive grid and axes, text in ink colours, legend whenever there are ≥ 2 models.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from horizon.stage1 import stats  # noqa: E402

MINUTE_S = timedelta(minutes=1).total_seconds()
SLUG_CHARS = 64                  # longest chart file stem

# dataviz reference palette (light surface); categorical slots in their fixed order
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "text.color": INK, "xtick.color": MUTED, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "axes.grid.axis": "x", "grid.color": GRID, "grid.linewidth": 0.6,
    "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight",
})

ABLATION_ORDER = ["none", "stage1", "stage2", "stage1+2", "full"]
MARKING_ORDER = ["none", "delimiting", "datamark_fixed", "datamark_dynamic", "random_shift", "random_scramble",
                 "base64", "keyed_delimiting"]


# ----------------------------------------------------------------------------------------------- data helpers
def _order(configs, preferred: list[str]) -> list[str]:
    configs = list(dict.fromkeys(configs))
    known = [c for c in preferred if c in configs]
    return known + sorted(c for c in configs if c not in known)


def stage1_split(df: pd.DataFrame) -> str | None:
    """The Stage 1 split to show: the report split (never used for choosing π*), else the fit split."""
    s1 = df[df["area"] == "Stage 1"]["group"].str.extract(r"\((.+)\)")[0].dropna().unique().tolist()
    for pref in ("report", "report_pilot", "fit", "fit_pilot"):
        if pref in s1:
            return pref
    return s1[0] if s1 else None


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """results_by_group.csv of any age → the columns the charts need (tables written before 1 Nov have no `utility`;
    their Stage 1 clean rows keep the mean token-F1 only in the note)."""
    df = df.copy()
    for c in ("attacks", "got_through", "benign", "benign_ok", "false_alarms"):
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0).astype(int)
    if "utility" not in df:
        df["utility"] = df["benign_ok"] / df["benign"].where(df["benign"] > 0)
        f1 = df["note"].astype(str).str.extract(r"mean token-F1 ([0-9.]+)")[0].astype(float)
        df.loc[f1.notna(), "utility"] = f1[f1.notna()]
    df["utility"] = pd.to_numeric(df["utility"], errors="coerce")
    return df


def panels(df: pd.DataFrame) -> list[dict]:
    """Every chart panel that the data supports. Each panel: title, kind (rate | utility), and the rows to plot
    (config label → (k, n) or utility value). Shared by the single-run and the comparison charts."""
    df = normalise(df)
    out = []

    def rate(title, sub, order=(), note="share of attacks that got through", axis="attack success rate"):
        sub = sub[sub["attacks"] > 0]
        if len(sub):
            out.append({"title": title, "kind": "rate", "note": note, "axis": axis,
                        "order": _order(sub["config"], list(order)),
                        "rows": sub.groupby("config")[["got_through", "attacks"]].sum()})

    def util(title, sub, order=(), note="", axis="utility"):
        sub = sub[sub["benign"] > 0]
        if len(sub):
            out.append({"title": title, "kind": "utility", "note": note, "axis": axis,
                        "order": _order(sub["config"], list(order)),
                        "rows": sub.groupby("config")[["utility", "benign"]].agg({"utility": "mean",
                                                                                   "benign": "sum"})})

    sp = stage1_split(df)
    if sp:
        s = df[df["area"] == "Stage 1"]
        rate(f"Stage 1 · document attacks ({sp})", s[s["group"] == f"document attacks ({sp})"], MARKING_ORDER,
             "one injected document per marking τ")
        rate(f"Stage 1 · adaptive attacker ({sp})", s[s["group"] == f"adaptive attacker ({sp})"], MARKING_ORDER,
             "attacker LLM optimises δ against each τ, then fresh keys")
        util(f"Stage 1 · clean documents ({sp})", s[s["group"] == f"clean documents ({sp})"], MARKING_ORDER,
             "mean token-F1 vs. the reference answer (compare against 'none')", "mean token-F1")
    s2 = df[df["area"] == "Stage 2"]
    for g in ("AgentDojo (report)", "AgentDojo (calibration)"):
        if (s2["group"] == g).any():
            rate(f"Stage 2 · {g} · attacks", s2[s2["group"] == g], ABLATION_ORDER,
                 "attack task completed by the agent")
            util(f"Stage 2 · {g} · benign tasks", s2[s2["group"] == g], ABLATION_ORDER,
                 "share of user tasks solved without an attack", "tasks solved")
            break
    rate("Stage 2 · adaptive attacker vs. full Horizon", s2[s2["group"] == "AgentDojo adaptive attacker"])
    rate("Guards · attacks NOT detected", df[df["group"] == "guard detection"], (),
         "attack prompts the guard let through (lower = better)", "missed")
    fa = df[df["group"] == "guard false alarms"].copy()
    if len(fa):
        fa["utility"] = fa["false_alarms"] / fa["benign"].where(fa["benign"] > 0)
        util("Guards · false alarms on harmless prompts", fa, (), "harmless prompts blocked (lower = better)",
             "false-alarm rate")
    rate("PyRIT · jailbreak attacks (direct)", df[df["group"] == "PyRIT jailbreak"])
    xp = df[df["group"].astype(str).str.startswith("PyRIT XPIA")]
    for g, sub in xp.groupby("group"):
        rate(f"PyRIT · {g.replace('PyRIT ', '')} · cross-prompt injection", sub, ABLATION_ORDER)
    for area, sub in df[df["group"] == "garak probes"].groupby("area"):
        rate(f"garak · {area.replace('Red team · ', '')} probes", sub, ["bare", "guarded"],
             "bare = model alone · guarded = with Horizon's direct layer")
    return out


def _kn(rows, c):
    if c not in rows.index:
        return None
    k, n = int(rows.loc[c, "got_through"]), int(rows.loc[c, "attacks"])
    return (k, n) if n else None


# ----------------------------------------------------------------------------------------------- drawing
def _draw(ax, panel: dict, runs: list[tuple[str, dict]], alpha: float) -> None:
    """Grouped horizontal bars: one group per configuration, one bar per run (model)."""
    order = panel["order"]
    m = len(runs)
    h = 0.8 / m
    for j, (name, p) in enumerate(runs):
        col = SERIES[j % len(SERIES)]
        rows = p["rows"] if p else None
        for i, c in enumerate(order):
            y = i + (j - (m - 1) / 2) * h
            if rows is None or c not in rows.index:
                ax.text(0.005, y, "not run", va="center", fontsize=7, color=MUTED)
                continue
            if panel["kind"] == "rate":
                k, n = _kn(rows, c)
                v = k / n
                lo, hi = stats.cp_interval(k, n, alpha)
                label = f"{k}/{n}"
            else:
                v = float(rows.loc[c, "utility"])
                if pd.isna(v):
                    continue
                lo = hi = None
                label = f"{v:.2f} (n={int(rows.loc[c, 'benign'])})"
            ax.barh(y, max(v, 0.004), height=h * 0.86, color=col, edgecolor=SURFACE, linewidth=1)
            if lo is not None:
                ax.plot([lo, hi], [y, y], color=INK2, linewidth=1, solid_capstyle="butt")
                ax.plot([lo, lo], [y - h * 0.2, y + h * 0.2], color=INK2, linewidth=1)
                ax.plot([hi, hi], [y - h * 0.2, y + h * 0.2], color=INK2, linewidth=1)
            ax.text(min(max(v, hi or v) + 0.015, 1.0), y, label, va="center", fontsize=7, color=INK2)
    ax.set_yticks(range(len(order)), order)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.12)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0", "25 %", "50 %", "75 %", "100 %"] if panel["kind"] == "rate"
                  or panel["axis"] in ("tasks solved", "false-alarm rate") else ["0", ".25", ".5", ".75", "1"])
    ax.set_xlabel(panel["axis"])
    ax.tick_params(axis="y", length=0)
    ax.set_title(panel["title"], pad=16 if panel.get("note") else 6)
    if panel.get("note"):
        ax.text(0, 1.012, panel["note"], transform=ax.transAxes, fontsize=7, color=MUTED, va="bottom")


def _save(fig, out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png")
    fig.savefig(out / f"{name}.pdf")
    plt.close(fig)
    return out / f"{name}.png"


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s.lower()).strip("_")[:SLUG_CHARS]


def _panel_figure(panel, runs, alpha, names=None):
    n = len(panel["order"])
    fig, ax = plt.subplots(figsize=(7.2, 0.9 + 0.32 * n * max(1, len(runs)) ** 0.8))
    _draw(ax, panel, runs, alpha)
    if names and len(names) > 1:
        _legend(fig, names)
    return fig


def _legend(fig, names):
    """Above the plot, right-aligned, so it never covers a bar or a k/n label."""
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=SERIES[i], label=nm) for i, nm in enumerate(names)], loc="lower right",
               bbox_to_anchor=(1.0, 1.0), ncol=len(names), fontsize=8, handlelength=1.2)


def run_charts(df: pd.DataFrame, process: pd.DataFrame | None, out: Path, alpha: float) -> list[tuple]:
    """Charts for ONE run: every panel the data supports, plus the phase timeline."""
    charts = []
    if df is not None and len(df):
        for i, p in enumerate(panels(df), 1):
            fig = _panel_figure(p, [("", p)], alpha)
            charts.append((_save(fig, out, f"{i:02d}_{_slug(p['title'])}"), p["title"] + " — " + p["note"]))
    if process is not None and len(process) and process["start"].astype(str).str.len().gt(0).any():
        charts.append((timeline(process, out), "Phases over time (bar = start → end; label = status · tests done)"))
    return charts


def timeline(process: pd.DataFrame, out: Path) -> Path:
    p = process[process["start"].astype(str).str.len() > 0].copy()
    p["s"] = pd.to_datetime(p["start"], errors="coerce")
    p["e"] = pd.to_datetime(p["end"], errors="coerce")
    t0 = p["s"].min()
    p["e"] = p["e"].fillna(p["e"].max() if p["e"].notna().any() else p["s"].max())
    fig, ax = plt.subplots(figsize=(7.2, 0.6 + 0.28 * len(p)))
    for i, (_, r) in enumerate(p.iterrows()):
        a = (r["s"] - t0).total_seconds() / MINUTE_S
        b = max((r["e"] - t0).total_seconds() / MINUTE_S, a + 0.3)
        ax.barh(i, b - a, left=a, height=0.62, color=SERIES[0], edgecolor=SURFACE)
        ax.text(b + 1, i, f"{r['status']} · {r['tests done']} tests" + (" · capped" if "cap" in str(r.get("note"))
                                                                         else ""),
                va="center", fontsize=7, color=INK2)
    ax.set_yticks(range(len(p)), p["phase"])
    ax.invert_yaxis()
    ax.set_xlabel("minutes since the first phase started")
    ax.set_xlim(0, ax.get_xlim()[1] * 1.25)
    ax.tick_params(axis="y", length=0)
    ax.set_title("Campaign timeline")
    return _save(fig, out, "99_timeline")


def compare_charts(frames: dict[str, pd.DataFrame], out: Path, alpha: float) -> list[tuple]:
    """One chart per panel with every model side by side (same configurations, same axis), plus an overview."""
    names = list(frames)
    per = {nm: {p["title"]: p for p in panels(df)} for nm, df in frames.items()}
    titles = list(dict.fromkeys(t for nm in names for t in per[nm]))
    charts = []
    for i, t in enumerate(titles, 1):
        base = next(per[nm][t] for nm in names if t in per[nm])
        order = list(dict.fromkeys(c for nm in names if t in per[nm] for c in per[nm][t]["order"]))
        panel = {**base, "order": order}
        fig = _panel_figure(panel, [(nm, per[nm].get(t)) for nm in names], alpha, names)
        charts.append((_save(fig, out, f"{i:02d}_{_slug(t)}"), t + " — " + base["note"]))
    ov = overview(frames, out, alpha)
    return ([ov] if ov else []) + charts


def overview(frames: dict[str, pd.DataFrame], out: Path, alpha: float) -> tuple | None:
    """Headline: attack success with NO defence vs. with the FULL pipeline, per test family and model."""
    names = list(frames)
    rows = []
    picks = [("Stage 1 documents", "document attacks", "none", None),
             ("AgentDojo agent", "AgentDojo (report)", "none", "full"),
             ("PyRIT XPIA", "PyRIT XPIA (none)", "none", "full"),
             ("garak indirect", "garak probes", "bare", "guarded")]
    for label, grp, off, on in picks:
        for nm, df in frames.items():
            g = df[df["group"].astype(str).str.startswith(grp) & (df["attacks"] > 0)]
            if grp == "garak probes":
                g = g[g["area"].str.contains("indirect")]
            if grp == "document attacks":
                sp = stage1_split(df)
                g = g[g["group"] == f"document attacks ({sp})"]
                on_c = "datamark_dynamic"
            else:
                on_c = on
            for state, c in (("no defence", off), ("Horizon", on_c)):
                s = g[g["config"] == c]
                if len(s):
                    rows.append({"family": label, "model": nm, "state": state,
                                 "k": int(s["got_through"].sum()), "n": int(s["attacks"].sum())})
    if not rows:
        return None
    d = pd.DataFrame(rows)
    fams = list(dict.fromkeys(d["family"]))
    # no sharey: each _draw inverts its y-axis, and inverting a SHARED axis twice flips it back
    fig, axes = plt.subplots(1, 2, figsize=(9, 0.9 + 0.55 * len(fams) * len(names) ** 0.6))
    for ax, state in zip(axes, ("no defence", "Horizon")):
        sub = d[d["state"] == state]
        panel = {"title": f"{state}", "kind": "rate", "axis": "attack success rate", "order": fams,
                 "note": "Stage 1 datamark_dynamic · agent, XPIA full · garak guarded" if state == "Horizon"
                 else "Stage 1 none · agent, XPIA none · garak bare"}
        runs = []
        for nm in names:
            r = sub[sub["model"] == nm].set_index("family").rename(columns={"k": "got_through", "n": "attacks"})
            runs.append((nm, {"rows": r} if len(r) else None))
        _draw(ax, panel, runs, alpha)
    axes[1].set_yticklabels([])
    fig.tight_layout()
    _legend(fig, names)
    fig.suptitle("Attacks that got through: without vs. with Horizon", x=0.012, y=1.06, ha="left", fontweight="bold")
    return _save(fig, out, "00_overview"), "Headline: attack success without defence vs. with Horizon, per model"
