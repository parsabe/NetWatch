"""Compare campaign runs with different models side by side (e.g. Qwen3-8B vs Gemma 3 12B): tables AND charts.

    .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b            # → report/compare/ (md + pdf + charts)
    .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b --send     # also a short summary to Telegram

Reads each run's report/campaign/results_by_group.csv (written by the campaign at the end of every job part), so it
works while runs are still going (on what has finished). Writes to report/compare/:
  compare_runs.md / .pdf      overview chart, one chart per test family (every model side by side), and two tables
  attacks.csv                 per (area, group, configuration): attack success rate with its 95 % Clopper–Pearson
                              interval per run and — for two runs — the difference with a two-sided Fisher exact test
                              (different models ⇒ independent samples, not paired per attack)
  utility.csv                 benign side: utility (Stage 1 token-F1, agent tasks solved) and false alarms per run
  run_status.csv              per run: profile, status, tests, phases that did not finish
  charts/*.png|pdf
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, load_config  # noqa: E402
from horizon.stage1 import stats  # noqa: E402

KEYS = ["area", "group", "config"]
OUT = ROOT / "report" / "compare"


def run_models(d: Path) -> dict:
    """models.* of that run folder (params.yaml, then that folder's config/local.yaml)."""
    import yaml
    from horizon.config import _merge
    m = dict(load_config().models)
    if d.resolve() != ROOT.resolve() and (d / "config" / "params.yaml").exists():
        m = yaml.safe_load((d / "config" / "params.yaml").read_text())["models"]
    local = d / "config" / "local.yaml"
    if local.exists():
        m = _merge(m, (yaml.safe_load(local.read_text()) or {}).get("models", {}))
    return m


def run_name(d: Path) -> str:
    """The model of that run; 'P <planner> · Q <reader>' when the two roles use different models."""
    m = run_models(d)
    p, q = (m[k].split("/", 1)[-1] for k in ("p_llm", "q_llm"))
    return q if p == q else f"P {p} · Q {q}"


def pair_name(d: Path) -> str:
    m = run_models(d)
    return "P {} · Q {}".format(*(m[k].split("/", 1)[-1] for k in ("p_llm", "q_llm")))


def load(d: Path) -> pd.DataFrame | None:
    from horizon.charts import normalise
    p = d / "report" / "campaign" / "results_by_group.csv"
    return normalise(pd.read_csv(p)) if p.exists() else None


def status_of(d: Path, name: str, df: pd.DataFrame) -> dict:
    s = d / "report" / "campaign" / "summary.json"
    j = json.loads(s.read_text()) if s.exists() else {}
    bad = [k for k, v in (j.get("phases") or {}).items() if v not in ("ok",)]
    return {"model": name, "folder": str(d), "profile": j.get("profile", "?"), "status": j.get("status", "?"),
            "tests": int(df["tests"].sum()), "attacks": int(df["attacks"].sum()),
            "finished": j.get("finished", "?"), "phases not ok": ", ".join(bad) or "—"}


def attack_table(frames: dict[str, pd.DataFrame], alpha: float) -> pd.DataFrame:
    names = list(frames)
    m = None
    for nm, df in frames.items():
        a = df[df["attacks"] > 0].groupby(KEYS, as_index=False)[["attacks", "got_through"]].sum()
        a = a.rename(columns={"attacks": f"n·{nm}", "got_through": f"k·{nm}"})
        m = a if m is None else m.merge(a, on=KEYS, how="outer")
    rows = []
    for _, r in m.iterrows():
        row = {k: r[k] for k in KEYS}
        for nm in names:
            n, k = r.get(f"n·{nm}"), r.get(f"k·{nm}")
            if pd.isna(n) or not n:
                row[nm] = "—"
                continue
            lo, hi = stats.cp_interval(int(k), int(n), alpha)
            row[nm] = f"{int(k)}/{int(n)} = {k / n:.0%} [{lo:.0%}–{hi:.0%}]"
        if len(names) == 2 and all(pd.notna(r.get(f"n·{nm}")) and r.get(f"n·{nm}") for nm in names):
            from scipy.stats import fisher_exact
            (n1, k1), (n2, k2) = [(int(r[f"n·{nm}"]), int(r[f"k·{nm}"])) for nm in names]
            p = fisher_exact([[k1, n1 - k1], [k2, n2 - k2]])[1]
            row["Δ ASR"] = f"{k2 / n2 - k1 / n1:+.0%}"
            row["p (Fisher)"] = f"{p:.3g}" + (" *" if p < alpha else "")
        rows.append(row)
    return pd.DataFrame(rows).fillna("—").sort_values(KEYS).reset_index(drop=True)


def utility_table(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    m = None
    for nm, df in frames.items():
        b = df[df["benign"] > 0].copy()
        b[nm] = [(f"{fa}/{n} false alarms" if "false alarms" in g else
                  f"{u:.2f} (n={n})" + (" mean F1" if g.startswith("clean documents") else " solved"))
                 for g, u, n, fa in zip(b["group"], b["utility"], b["benign"], b["false_alarms"])]
        b = b[[*KEYS, nm]]
        m = b if m is None else m.merge(b, on=KEYS, how="outer")
    return m.fillna("—").sort_values(KEYS).reset_index(drop=True) if m is not None else pd.DataFrame()


SCORES = [  # (role it tells about, label, group prefix, config, measure, better)
    ("reader (Q)", "Stage 1 attacks, no marking", "document attacks (report", "none", "attack", "lower"),
    ("reader (Q)", "Stage 1 attacks, datamark_dynamic", "document attacks (report", "datamark_dynamic", "attack", "lower"),
    ("reader (Q)", "Stage 1 answer quality (F1), no marking", "clean documents (report", "none", "utility", "higher"),
    ("reader (Q)", "XPIA attacks, no defence", "PyRIT XPIA", "none", "attack", "lower"),
    ("planner (P)", "AgentDojo attacks, no defence", "AgentDojo (report)", "none", "attack", "lower"),
    ("planner (P)", "AgentDojo tasks solved, no defence", "AgentDojo (report)", "none", "benign", "higher"),
    ("planner (P)", "PyRIT jailbreaks", "PyRIT jailbreak", None, "attack", "lower"),
    ("planner (P)", "garak probes, model alone", "garak probes", "bare", "attack", "lower"),
    ("pair (P+Q)", "AgentDojo attacks, full Horizon", "AgentDojo (report)", "full", "attack", "lower"),
    ("pair (P+Q)", "AgentDojo tasks solved, full Horizon", "AgentDojo (report)", "full", "benign", "higher"),
    ("pair (P+Q)", "XPIA attacks, full Horizon", "PyRIT XPIA", "full", "attack", "lower"),
    ("pair (P+Q)", "adaptive attacker vs full Horizon", "AgentDojo adaptive attacker", "full", "attack", "lower"),
    ("pair (P+Q)", "garak probes, with Horizon's guard", "garak probes", "guarded", "attack", "lower"),
]


def scoreboard(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per question ('which model is the better planner / reader?'), one column per run: k/n (rate)."""
    rows = []
    for role, label, grp, conf, kind, better in SCORES:
        row = {"role": role, "measure": label, "better": better}
        for nm, df in frames.items():
            g = df[df["group"].astype(str).str.startswith(grp)]
            if conf is not None:
                g = g[g["config"].astype(str) == conf]
            if kind == "attack":
                n, k = int(g["attacks"].sum()), int(g["got_through"].sum())
                row[nm] = f"{k}/{n} ({k / n:.0%})" if n else "—"
            elif kind == "benign":
                n, k = int(g["benign"].sum()), int(g["benign_ok"].sum())
                row[nm] = f"{k}/{n} ({k / n:.0%})" if n else "—"
            else:
                g = g[g["benign"] > 0]
                row[nm] = f"{g['utility'].mean():.2f} (n={int(g['benign'].sum())})" if len(g) else "—"
        rows.append(row)
    return pd.DataFrame(rows)


def write_pdf(md_title: str, status: pd.DataFrame, charts, att: pd.DataFrame, util: pd.DataFrame, notes,
              sb: pd.DataFrame | None = None) -> bool:
    if not shutil.which("xelatex"):
        return False
    sys.path.insert(0, str(ROOT / "eval"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from campaign import PREAMBLE, _latex_fonts
    from make_report import esc, latex_table
    from datetime import datetime
    tex = [(PREAMBLE % esc(datetime.now().isoformat(timespec="minutes"))).replace("@FONTS@", _latex_fonts())
           .replace("Horizon campaign report", esc(md_title)),
           "\\section*{Runs}\n", latex_table(status, "Runs compared"), "\\begin{itemize}\n",
           *[f"\\item {esc(x)}\n" for x in notes], "\\end{itemize}\n",
           *(["\\section*{Scoreboard: better planner / reader}\n",
              latex_table(sb, "Per run: k/n (rate); reader rows depend on the Q-LLM, planner rows on the P-LLM",
                          longtable=True)] if sb is not None else []),
           "\\section*{Charts}\n"]
    for p, c in charts:
        tex.append("\\begin{figure}[H]\\centering\\includegraphics[height=0.42\\textheight,width=\\linewidth,"
                   f"keepaspectratio]{{charts/{p.with_suffix('.pdf').name}}}\\caption*{{{esc(c)}}}\\end{{figure}}\n")
    tex += ["\\section*{Attacks: share that got through}\n", latex_table(att, "Attack success per model "
            "(k/n = rate [95% CI]); Δ and Fisher p for two models", longtable=True),
            "\\section*{Benign side: utility and false alarms}\n",
            latex_table(util, "Utility per model", longtable=True) if len(util) else "", "\\end{document}\n"]
    (OUT / "compare_runs.tex").write_text("".join(tex))
    r = None
    for _ in range(2):
        r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "compare_runs.tex"], cwd=OUT,
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            break
    for ext in ("aux", "out", "xdv"):
        (OUT / f"compare_runs.{ext}").unlink(missing_ok=True)
    return r is not None and r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path, help="run folders (the project root, ~/0_runs/<name>, ...)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()
    alpha = load_config().stage1["alpha"]
    frames, status = {}, []
    for d in args.runs:
        d = d.expanduser()
        df, name = load(d), run_name(d)
        if df is None:
            print(f"{name} ({d}): no report/campaign/results_by_group.csv yet")
            continue
        name = name if name not in frames else f"{name} ({d.name})"
        frames[name] = df
        status.append(status_of(d, name, df))
    if len(frames) < 2:
        print("need at least two runs with results")
        return 1
    names = list(frames)
    OUT.mkdir(parents=True, exist_ok=True)
    att, util, st = attack_table(frames, alpha), utility_table(frames), pd.DataFrame(status)
    att.to_csv(OUT / "attacks.csv", index=False)
    util.to_csv(OUT / "utility.csv", index=False)
    st.to_csv(OUT / "run_status.csv", index=False)

    from horizon.charts import compare_charts
    for old in (OUT / "charts").glob("*.*"):
        old.unlink()
    charts = compare_charts(frames, OUT / "charts", alpha)

    profiles = set(st["profile"])
    notes = [f"Colours: {', '.join(f'{nm} = slot {i + 1}' for i, nm in enumerate(names))} "
             "(blue, orange, … in every chart).",
             "Bars = share of attacks that got through (lower = better defence), whisker = 95 % Clopper–Pearson "
             "interval, k/n beside each bar. Utility panels are separate charts.",
             "Stage 1 utility = mean token-F1 against a short reference answer: long, correct answers still score "
             "low, so compare each marking against 'none' of the same model rather than the absolute value."]
    if len(profiles) > 1:
        notes.append(f"WARNING: the runs used different profiles ({', '.join(sorted(map(str, profiles)))}), so "
                     "sample sizes differ — compare rates and intervals, not counts.")
    title = f"Runs compared: {' vs '.join(names)}"
    sb = scoreboard(frames)
    sb.to_csv(OUT / "scoreboard.csv", index=False)
    md = [f"# {title}", "", st.to_markdown(index=False), "", *[f"- {x}" for x in notes], "",
          "## Scoreboard: which model is the better planner / reader?", "",
          "k/n (rate) per run; `better` says which direction is good. Reader rows depend only on the Q-LLM, planner "
          "rows only on the P-LLM, pair rows on both.", "", sb.to_markdown(index=False), "", "## Charts", ""]
    md += [f"![{c}](charts/{p.name})\n\n*{c}*\n" for p, c in charts]
    md += ["## Attacks: share that got through", "",
           f"`k/n = rate [95 % CI]`; Δ = {names[-1]} − {names[0]}; * = significant at α = {alpha}.", "",
           att.to_markdown(index=False), "", "## Benign side: utility and false alarms", "",
           util.to_markdown(index=False) if len(util) else "(none)", ""]
    (OUT / "compare_runs.md").write_text("\n".join(md))
    ok = False if args.no_pdf else write_pdf(title, st, charts, att, util, notes, sb)
    print(sb.to_markdown(index=False), end="\n\n")
    print(att.to_markdown(index=False))
    print(f"\nwritten: {OUT}/compare_runs.md" + (" + compare_runs.pdf" if ok else "") + f" · {len(charts)} charts")
    if args.send:
        from horizon.notify import send
        sig = att[att["p (Fisher)"].astype(str).str.endswith("*")] if "p (Fisher)" in att else att.iloc[:0]
        lines = [f"⚖️ {' vs '.join(names)}: {len(att)} groups compared, {len(sig)} significant differences"]
        lines += [f"• {r['group']} · {r['config']}: {r[names[0]]} → {r[names[-1]]} ({r['Δ ASR']})"
                  for _, r in sig.head(15).iterrows()]
        send("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
