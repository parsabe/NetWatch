"""Stage 1 experiments (§11): paired static matrix A[x, τ], clean utility U_τ, adaptive p̄^adp_τ,
then the math (Slides 13-27) — fit split chooses π*, report split evaluates it.

    uv run python eval/stage1_runner.py --split fit    --static --clean --adaptive --analyze
    uv run python eval/stage1_runner.py --split report --static --clean --adaptive --analyze
    uv run python eval/stage1_runner.py --split fit --pilot --static --adaptive --analyze   # §10 step 9 pilot

Outputs (resumable, append-only) in results/stage1/<split>[_pilot]/:
  matrix.csv  clean.csv  adaptive_trace.csv  adaptive_eval.csv  summary.json
and, from the fit split, results/stage1/pi_star.json (π*, R̄_Q, |K|, T) used by Stage 2.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402
from horizon.llm import LLM, server_alive  # noqa: E402
from horizon.logging_utils import log_examples, mlflow_run, save_json  # noqa: E402
from horizon.stage1 import stats  # noqa: E402
from horizon.stage1.adaptive import attack_target, evaluate_fresh_keys  # noqa: E402
from horizon.stage1.canary import attack_indicator  # noqa: E402
from horizon.stage1.prompts import build_prompt, inject  # noqa: E402
from horizon.stage1.sion import evaluate_v1_objective, solve_v1, stage1_output  # noqa: E402
from horizon.stage1.transforms import build_variants  # noqa: E402
from horizon.stage1.utility import utility  # noqa: E402

PROC = ROOT / "data" / "processed"


def _load(split: str, kind: str) -> list[dict]:
    return [json.loads(l) for l in (PROC / f"stage1_{split}_{kind}.jsonl").read_text().splitlines()]


def _done(path: Path, keys: list[str]) -> set[tuple]:
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    return set(map(tuple, df[keys].astype(str).to_numpy()))


def _append(path: Path, row: dict) -> None:
    pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)


_LOCK = threading.Lock()


def _parallel(cfg, jobs, fn, label: str) -> None:
    """Run fn(job) over a thread pool sized to the server's parallel slots; rows are written under a lock."""
    with ThreadPoolExecutor(max_workers=cfg.models["parallel_requests"]) as ex:
        futures = [ex.submit(fn, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if i % len(cfg.stage1["variants"]) == 0 or i == len(jobs):
                print(f"  {label} {i}/{len(jobs)}", flush=True)


def run_static(cfg, out: Path, attacks: list[dict], variants: dict, victim: LLM) -> None:
    """Paired design: every attack x on every variant τ, fresh κ per call → A[x, τ] (Slides 12, 15)."""
    path = out / "matrix.csv"
    done = _done(path, ["attack_id", "variant"])
    jobs = [(a, name) for a in attacks for name in variants if (a["id"], name) not in done]

    def one(job):
        a, name = job
        v = variants[name]
        p = build_prompt(v, a["task"], inject(a["document"], a["injection"], a["position"]))
        reply = victim.chat(p.messages)
        row = {"attack_id": a["id"], "variant": name, "category": a["category"], "source": a["source"],
               "A": attack_indicator(reply, a["canary"], v, p.key), "reply": reply[:300]}
        with _LOCK:
            _append(path, row)
    _parallel(cfg, jobs, one, "static")


def run_clean(cfg, out: Path, clean: list[dict], variants: dict, victim: LLM) -> None:
    """U_τ = E[u(M(x_κ(∅)), y*)] with u = token-F1 (Slide 12)."""
    path = out / "clean.csv"
    done = _done(path, ["doc_id", "variant"])
    jobs = [(c, name) for c in clean for name in variants if (c["id"], name) not in done]

    def one(job):
        c, name = job
        p = build_prompt(variants[name], c["task"], c["document"])
        reply = victim.chat(p.messages)
        row = {"doc_id": c["id"], "variant": name,
               "utility": utility(reply, c["reference"], cfg.stage1["utility_metric"])}
        with _LOCK:
            _append(path, row)
    _parallel(cfg, jobs, one, "clean")


def run_adaptive(cfg, out: Path, targets: list[dict], variants: dict, victim: LLM, attacker: LLM) -> None:
    """Slide 20: attacker-LLM search within budget, then δ* on fresh keys → (s_a, n_a)."""
    s1 = cfg.stage1
    tpath, epath = out / "adaptive_trace.csv", out / "adaptive_eval.csv"
    done = _done(epath, ["target_id", "variant"])
    jobs = [(t, name) for t in targets for name in variants if (t["id"], name) not in done]

    # transfer seeds: static-matrix injections that already beat τ on other documents (a stronger attacker)
    k_tr = int(s1.get("adaptive_transfer_examples", 0))
    wins: dict[str, list[str]] = {}
    if k_tr and (out / "matrix.csv").exists():
        texts = {a["id"]: a["injection"] for a in _load(out.name.split("_")[0], "attacks")}
        mt = pd.read_csv(out / "matrix.csv")
        for name, g in mt[mt["A"] == 1].groupby("variant"):
            wins[name] = [texts[i] for i in g["attack_id"].astype(str) if i in texts]

    def one(job):
        t, name = job
        v = variants[name]
        pool = [x for x in wins.get(name, []) if x != t["injection"]]
        tr = attack_target(attacker, victim, v, t["task"], t["document"], t["position"], t["canary"],
                           t["injection"], s1["attack_budget"], t["id"], pool[:k_tr],
                           bool(s1.get("adaptive_early_stop")))
        res = evaluate_fresh_keys(victim, v, t["task"], t["document"], t["position"], t["canary"],
                                  tr.best_injection, s1["n_adaptive_eval_keys"])
        with _LOCK:
            for q in tr.queries:
                _append(tpath, {"target_id": t["id"], "variant": name, "query": q["query"], "success": q["success"],
                                "injection": q["injection"][:300]})
            for j, a in enumerate(res):
                _append(epath, {"target_id": t["id"], "variant": name, "eval_idx": j, "A": a})
        print(f"  adaptive {t['id']} {name}: search best={tr.best_score} eval={sum(res)}/{len(res)}", flush=True)
    _parallel(cfg, jobs, one, "adaptive")


def analyze(cfg, out: Path, split: str, variants: dict) -> dict:
    """Slides 13-27 on the saved outcomes. Fit split: choose τ*, π*. Report split: evaluate them."""
    s1 = cfg.stage1
    a = s1["alpha"]
    m = pd.read_csv(out / "matrix.csv")
    names = [t for t in variants if t in set(m["variant"])]
    piv = m.pivot_table(index="attack_id", columns="variant", values="A").dropna()[names]
    A = piv.to_numpy().astype(int)
    n = A.shape[0]
    U = (pd.read_csv(out / "clean.csv").groupby("variant")["utility"].mean().reindex(names)
         if (out / "clean.csv").exists() else pd.Series(np.nan, index=names))
    if (out / "adaptive_eval.csv").exists():
        ev = pd.read_csv(out / "adaptive_eval.csv")
        sa = ev.groupby("variant")["A"].sum().reindex(names).fillna(0).astype(int)
        na = ev.groupby("variant")["A"].count().reindex(names).fillna(0).astype(int)
    else:
        sa = pd.Series(0, index=names)
        na = pd.Series(0, index=names)
    logk = np.array([variants[t].log_key_space() for t in names])
    p_bar = {t: stats.cp_upper(int(A[:, i].sum()), n, a) for i, t in enumerate(names)}
    p_adp = {t: (stats.cp_upper(int(sa[t]), int(na[t]), a) if na[t] > 0 else float("nan")) for t in names}
    per_variant = {t: {"s": int(A[:, i].sum()), "n": n, "p_hat": float(A[:, i].mean()), "p_bar": p_bar[t],
                       "U": float(U[t]), "s_a": int(sa[t]), "n_a": int(na[t]), "p_adp_bar": p_adp[t],
                       "log10_K": float(logk[i] / math.log(10))} for i, t in enumerate(names)}
    summary = {"split": split, "n": n, "alpha": a, "variants": per_variant,
               "mcnemar": {f"{x}|{y}": stats.mcnemar_pvalue(A[:, i], A[:, j])
                           for i, x in enumerate(names) for j, y in enumerate(names) if i < j}}
    if s1.get("holm"):
        summary["mcnemar_holm"] = stats.holm(summary["mcnemar"])
    if "none" in names:                                  # how much each τ lowers the ASR, with a bootstrap CI
        i0 = names.index("none")
        summary["asr_drop_vs_none"] = {
            t: dict(zip(("drop", "ci_lo", "ci_hi"), stats.paired_bootstrap_diff(
                A[:, i0], A[:, i], a, s1["bootstrap_resamples"], cfg.run["seed"])))
            for i, t in enumerate(names) if t != "none"}
    src = m.drop_duplicates("attack_id").set_index("attack_id").loc[piv.index]
    rho_info = stats.estimate_rho({s_: g["category"].value_counts().to_dict() for s_, g in src.groupby("source")})
    summary["rho_hat"] = rho_info
    mean_bound = (np.array([p_bar[t] for t in names]) if s1.get("v1_mean_bound", "empirical") == "cp" else None)
    have_all = not (np.isnan(U.to_numpy()).any() or any(math.isnan(v) for v in p_adp.values()))
    if not have_all:
        summary["note"] = "risk/V1 skipped: needs clean utility and adaptive results for every variant"
        save_json(summary, out / "summary.json")
        return summary
    padp = np.array([p_adp[t] for t in names])
    Uv = U.to_numpy()
    u_none = float(U["none"])
    sweeps = []
    for q in cfg.q_grid():
        for rho in cfg.rho_grid():
            risks = {t: stats.combined_risk(q, logk[i], p_bar[t], p_adp[t], rho).total for i, t in enumerate(names)}
            for eta in s1["eta_list"]:
                row = {"q": q, "rho": rho, "eta": eta, **{f"R_{t}": risks[t] for t in names},
                       "tau_star": stats.choose_tau(risks, dict(zip(names, Uv)), u_none, eta)}
                if split == "fit":
                    res = solve_v1(A, names, Uv, u_none, eta, q, logk, padp, rho, s1["solver"], mean_bound)
                    row.update({"V1": res.value, **{f"pi_{t}": res.pi.get(t) for t in names}})
                sweeps.append(row)
    pd.DataFrame(sweeps).to_csv(out / "sweeps.csv", index=False)
    th = cfg.theory
    q0, rho0, eta0 = th["q_main"], th["rho_main"], th["eta_main"]
    if s1.get("rho_from_data") and rho_info["rho_hat"] > 0:
        rho0 = rho_info["rho_hat"]                       # headline ρ = the shift observed between attack sources
    risks0 = {t: stats.combined_risk(q0, logk[i], p_bar[t], p_adp[t], rho0).total for i, t in enumerate(names)}
    if split == "fit" and out.name in (split, f"{split}_pilot"):           # tagged runs never replace π*
        res = solve_v1(A, names, Uv, u_none, eta0, q0, logk, padp, rho0, s1["solver"], mean_bound)
        summary["v1_main"] = {"value": res.value, "status": res.status, "rho": rho0, "q": q0, "eta": eta0}
        if res.value is not None:
            o = stage1_output(res, risks0, dict(zip(names, logk)), [], s1["support_tol"], rho0, eta0, q0)
            save_json({"pi_star": o.pi_star, "R_Q": o.R_Q, "log_K": o.log_K, "V1_fit": o.V1, "rho": rho0,
                       "eta": eta0, "q": q0, "rho_source": "data" if rho0 != th["rho_main"] else "config",
                       "v1_mean_bound": s1.get("v1_mean_bound", "empirical"), "tau_star": stats.choose_tau(risks0, dict(zip(names, Uv)), u_none, eta0),
                       "chosen_on": out.name}, ROOT / "results" / "stage1" / "pi_star.json")
            summary["pi_star"] = o.pi_star
    else:
        ps = ROOT / "results" / "stage1" / "pi_star.json"
        if ps.exists():
            d = json.loads(ps.read_text())
            w = np.array([d["pi_star"].get(t, 0.0) for t in names])
            rho0 = d.get("rho", rho0)                    # evaluate at the fit-split choices, ρ included
            risks0 = {t: stats.combined_risk(q0, logk[i], p_bar[t], p_adp[t], rho0).total for i, t in enumerate(names)}
            ev = evaluate_v1_objective(A, w, q0, logk, padp, rho0, mean_bound)
            summary["report"] = {"V1_at_pi_star_fit": ev.value, "terms": ev.terms,
                                 "R_Q": float(sum(d["pi_star"].get(t, 0.0) * risks0[t] for t in names)),
                                 "R_tau_star_fit": risks0.get(d["tau_star"]) if d.get("tau_star") else None}
    save_json(summary, out / "summary.json")
    return summary


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--split", choices=["fit", "report"], required=True)
    ap.add_argument("--pilot", action="store_true", help="small pilot (stage1.pilot_* sizes)")
    for f in ["static", "clean", "adaptive", "analyze"]:
        ap.add_argument(f"--{f}", action="store_true")
    ap.add_argument("--tag", help="results/stage1/<split>_<tag>/ (e.g. a second victim model); the fit split's "
                                  "π* is never overwritten by a tagged run")
    args = ap.parse_args()
    cfg = config_from_args(args)
    s1 = cfg.stage1
    variants = build_variants(s1)
    out = ROOT / "results" / "stage1" / (args.split + ("_pilot" if args.pilot else "") + (f"_{args.tag}" if args.tag else ""))
    out.mkdir(parents=True, exist_ok=True)
    attacks, clean = _load(args.split, "attacks"), _load(args.split, "clean")
    if args.pilot:
        attacks, clean = attacks[:s1["pilot_n"]], clean[:s1["pilot_clean"]]
        targets = [a for a in attacks if a["source"] == "bipia"][:s1["pilot_adaptive_targets"]]
    else:
        targets = attacks[:s1["n_adaptive_targets"]]
    if (args.static or args.clean or args.adaptive) and not server_alive(cfg.models):
        sys.exit("model server not reachable — start it with scripts/serve_local.sh")
    victim = LLM(cfg.models["q_llm"], cfg.models)
    attacker = LLM(cfg.models["attacker"], cfg.models, temperature=cfg.models["attacker_temperature"])
    with mlflow_run(cfg, f"stage1-{out.name}", {"stage": "1", "split": args.split, "pilot": str(args.pilot)}):
        import mlflow
        if args.static:
            run_static(cfg, out, attacks, variants, victim)
        if args.clean:
            run_clean(cfg, out, clean, variants, victim)
        if args.adaptive:
            run_adaptive(cfg, out, targets, variants, victim, attacker)
        if args.analyze:
            summary = analyze(cfg, out, args.split, variants)
            mlflow.log_dict(summary, "summary.json")
            for t, v in summary["variants"].items():
                mlflow.log_metrics({f"p_hat.{t}": v["p_hat"], f"p_bar.{t}": v["p_bar"]})
            print(json.dumps({k: v for k, v in summary.items() if k != "mcnemar"}, indent=1, default=str))
        for f in ["matrix.csv", "clean.csv", "adaptive_eval.csv"]:
            if (out / f).exists():
                log_examples(pd.read_csv(out / f).to_dict("records"), f.replace(".csv", ""))
        mlflow.log_metrics({"llm_calls": victim.calls + attacker.calls, "cache_hits": victim.cache_hits})


if __name__ == "__main__":
    main()
