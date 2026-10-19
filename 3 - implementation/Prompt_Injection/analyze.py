"""Turn raw results into the numbers and plots for the thesis.

Usage:
    python analyze.py --static results/static.jsonl --adaptive-dir results/ --rho 0.1 --eta 0.05
"""
import argparse
import glob
import json

import matplotlib.pyplot as plt
import pandas as pd

from stats import (choose_defense, clopper_pearson_upper, combined_risk,
                   dro_worst_case)
from transforms import TRANSFORMS

RHOS = [0, 0.01, 0.05, 0.1, 0.2, 0.5]


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", required=True)
    ap.add_argument("--adaptive-dir", default=None)
    ap.add_argument("--rho", type=float, default=0.1)
    ap.add_argument("--eta", type=float, default=0.05)
    ap.add_argument("--attempts", type=int, default=1000, help="q for forging")
    args = ap.parse_args()

    df = pd.DataFrame(load_jsonl(args.static))
    att, cln = df[df["kind"] == "attack"], df[df["kind"] == "clean"]

    adp = {}
    if args.adaptive_dir:
        for p in glob.glob(f"{args.adaptive_dir}/adaptive_*.jsonl"):
            a = pd.DataFrame(load_jsonl(p))
            adp[a["transform"].iloc[0]] = (int(a["success"].sum()), len(a))

    rows = []
    for t, g in att.groupby("transform"):
        s, n = int(g["success"].sum()), len(g)
        p_bar = clopper_pearson_upper(s, n)
        sa, na = adp.get(t, (0, 0))
        p_adp = clopper_pearson_upper(sa, na) if na else 0.0
        rows.append({
            "transform": t, "s": s, "n": n, "asr": s / n, "p_bar": p_bar,
            **{f"dro_{r}": dro_worst_case(p_bar, r) for r in RHOS},
            "s_adp": sa, "n_adp": na, "p_adp_bar": p_adp,
            "risk": combined_risk(TRANSFORMS[t][1], args.attempts, p_bar, p_adp, args.rho),
            "utility": cln.loc[cln["transform"] == t, "utility"].mean(),
        })
    res = pd.DataFrame(rows).sort_values("risk")
    pd.set_option("display.width", 200)
    print(res.round(4).to_string(index=False))
    res.to_csv("results/summary.csv", index=False)

    # ASR per attack category (useful for discussing which types break through)
    print("\nASR by category:\n", att.pivot_table(index="category", columns="transform",
                                                    values="success", aggfunc="mean").round(3))

    best = choose_defense({r.transform: {"risk": r.risk, "utility": r.utility}
                           for r in res.itertuples()}, eta=args.eta)
    print(f"\nChosen defense (eta={args.eta}, rho={args.rho}): {best}")

    # Plot 1: DRO curves
    plt.figure()
    for r in res.itertuples():
        plt.plot(RHOS, [dro_worst_case(r.p_bar, x) for x in RHOS], marker="o", label=r.transform)
    plt.xlabel("rho (shift budget)"); plt.ylabel("worst-case ASR"); plt.legend()
    plt.title("DRO worst-case ASR vs. rho"); plt.savefig("results/dro_curves.png", dpi=200)

    # Plot 2: risk vs utility trade-off
    plt.figure()
    plt.scatter(res.utility, res.risk)
    for r in res.itertuples():
        plt.annotate(r.transform, (r.utility, r.risk))
    plt.xlabel("utility (clean docs)"); plt.ylabel("combined risk")
    plt.title("Security vs. usefulness"); plt.savefig("results/tradeoff.png", dpi=200)
    print("Saved results/summary.csv, results/dro_curves.png, results/tradeoff.png")


if __name__ == "__main__":
    main()
