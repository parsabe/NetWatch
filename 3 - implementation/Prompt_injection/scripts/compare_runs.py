"""Compare campaign runs with different models side by side (e.g. Qwen3-8B vs Gemma 3 12B).

    .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b            # print + report/compare_runs.md
    .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b --send     # also a short summary to Telegram

Reads each run's report/campaign/results_by_group.csv (written by the campaign at the end of every job part), so it
works while runs are still going (on what has finished). Per (area, group, configuration): attacks, attack success
rate with its 95 % Clopper–Pearson interval in every run, and — for two runs — the difference with a two-sided Fisher
exact test (the runs used different models, so the outcomes are independent samples, not paired per attack).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, load_config  # noqa: E402
from horizon.stage1 import stats  # noqa: E402

KEYS = ["area", "group", "config"]


def run_name(d: Path) -> str:
    """The victim model of that run folder (config/local.yaml over params.yaml)."""
    import yaml
    model = load_config().models["q_llm"]
    local = d / "config" / "local.yaml"
    if local.exists():
        model = (yaml.safe_load(local.read_text()) or {}).get("models", {}).get("q_llm", model)
    elif d.resolve() != ROOT.resolve():
        p = d / "config" / "params.yaml"
        if p.exists():
            model = yaml.safe_load(p.read_text())["models"]["q_llm"]
    return model.split("/", 1)[-1]


def load(d: Path) -> pd.DataFrame | None:
    p = d / "report" / "campaign" / "results_by_group.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df = df[df["attacks"] > 0][[*KEYS, "attacks", "got_through"]]
    return df.groupby(KEYS, as_index=False)[["attacks", "got_through"]].sum()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path, help="run folders (the project root, ~/0_runs/<name>, ...)")
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()
    alpha = load_config().stage1["alpha"]
    names, frames = [], []
    for d in args.runs:
        df = load(d.expanduser())
        name = run_name(d.expanduser())
        if df is None:
            print(f"{name} ({d}): no report/campaign/results_by_group.csv yet")
            continue
        names.append(name)
        frames.append(df.rename(columns={"attacks": f"n·{name}", "got_through": f"k·{name}"}))
    if len(frames) < 2:
        print("need at least two runs with results")
        return 1
    m = frames[0]
    for f in frames[1:]:
        m = m.merge(f, on=KEYS, how="outer")
    rows = []
    for _, r in m.iterrows():
        row = {k: r[k] for k in KEYS}
        for nm in names:
            n, k = r.get(f"n·{nm}"), r.get(f"k·{nm}")
            if pd.isna(n) or not n:
                row[nm] = "—"
                continue
            lo, hi = stats.cp_interval(int(k), int(n), alpha)
            row[nm] = f"{k / n:.1%} [{lo:.0%}–{hi:.0%}] n={int(n)}"
        if len(names) == 2 and all(pd.notna(r.get(f"n·{nm}")) and r.get(f"n·{nm}") for nm in names):
            from scipy.stats import fisher_exact
            (n1, k1), (n2, k2) = [(int(r[f"n·{nm}"]), int(r[f"k·{nm}"])) for nm in names]
            p = fisher_exact([[k1, n1 - k1], [k2, n2 - k2]])[1]
            row["Δ ASR"] = f"{k2 / n2 - k1 / n1:+.1%}"
            row["p (Fisher)"] = f"{p:.3g}" + (" *" if p < alpha else "")
        rows.append(row)
    out = pd.DataFrame(rows).sort_values(KEYS)
    md = (f"# Runs compared: {' vs '.join(names)}\n\nAttack success rate (ASR) with 95 % Clopper–Pearson interval; "
          f"Δ = {names[-1]} − {names[0]}; * = significant at α = {alpha}.\n\n" + out.to_markdown(index=False) + "\n")
    dest = ROOT / "report" / "compare_runs.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md)
    print(md)
    print(f"written: {dest}")
    if args.send:
        from horizon.notify import send
        sig = out[out.get("p (Fisher)", pd.Series(dtype=str)).astype(str).str.endswith("*")] \
            if "p (Fisher)" in out else out.iloc[:0]
        lines = [f"⚖️ {' vs '.join(names)}: {len(out)} groups compared, {len(sig)} significant differences"]
        lines += [f"• {r['group']} · {r['config']}: {r[names[0]]} → {r[names[-1]]} ({r['Δ ASR']})"
                  for _, r in sig.head(15).iterrows()]
        send("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
