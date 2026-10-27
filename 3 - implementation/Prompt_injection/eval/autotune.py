"""Auto-tuner: searches for upgrades in a loop and adopts only the ones that are clearly better.

    uv run python eval/autotune.py                  # all tracks (campaign phase 'autotune', before Stage 1 fit)
    uv run python eval/autotune.py --track estimators   # CPU only, no model server needed
    uv run python eval/autotune.py --dry-run        # evaluate and report, adopt nothing

Three tracks (results/autotune/: trials.csv, clean.csv, summary.json; adoptions → config/autotuned.yaml):

1. estimators (simulation, no LLM) — for each analysis choice with several VALID options (stage2.loss_ci,
   stage2.crc_loss_bound) simulate coverage over a grid of scenarios and adopt the tightest option whose
   coverage stays ≥ 1 − α. Choosing on simulated data never touches the real results, so the guarantees hold.
2. defence (LLM, fit split only) — candidate Stage 1 forms (datamark_dynamic with longer keys / larger pools,
   keyed delimiting, each ± sandwich reminder) against the existing keyed variants, by successive halving:
   every round evaluates the surviving candidates on twice as many paired attacks and keeps the better half.
   Score = combined risk R̄ = min{1, q/|K| + g_ρ(p̄_CP)} subject to utility ≥ U_none − η (Slides 13–22).
   A candidate is adopted (added to the Stage 1 menu, never replacing anything) only if its R̄ beats the best
   existing keyed variant by ≥ min_risk_gain AND an exact McNemar test does not find it significantly worse on
   the paired attacks. π* then decides how much to use it; the report split stays untouched for evaluation.
3. models (report only) — the same quick test for every Ollama model listed in autotune.models. The victim model
   is the object of study, so it is never switched automatically (autotune.adopt_models: false).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import AUTOTUNED, ROOT, add_config_args, config_from_args, load_config  # noqa: E402
from horizon.llm import LLM, server_alive  # noqa: E402
from horizon.stage1 import stats  # noqa: E402
from horizon.stage1.canary import attack_indicator  # noqa: E402
from horizon.stage1.prompts import build_prompt, inject  # noqa: E402
from horizon.stage1.transforms import build_variants  # noqa: E402
from horizon.stage1.utility import utility  # noqa: E402
from horizon.stage2.conformal import calibrate_lambda, crc_losses  # noqa: E402
from horizon.stage2.loss import bernstein_loss_bound, cp_loss_bound  # noqa: E402

OUT = ROOT / "results" / "autotune"
MC_Z = 2.0                   # standard errors of Monte-Carlo slack when checking a simulated guarantee
PROC = ROOT / "data" / "processed"
_LOCK = threading.Lock()


def notify(text: str) -> None:
    try:
        from horizon.notify import send
        send(text)
    except Exception:
        pass


# =============================================================================== 1 · estimators
def estimator_track(cfg, rng: np.random.Generator) -> dict:
    """Simulated coverage + width of each valid option; adopt the tightest one that keeps coverage ≥ 1 − α."""
    at, al = cfg.autotune, cfg.stage1["alpha"]
    a, h = cfg.stage2["user_mistake_rate"], cfg.stage2["harm_rate"]
    T = at["sim_trials"]
    target = 1 - al - at["sim_coverage_slack"]          # Monte-Carlo slack: T trials estimate coverage ± ~1/√T
    rows = []
    for n, (pg, pc, pf) in itertools.product(at["sim_n"], at["sim_rates"]):
        truth = pg + a * pc + h * pf
        draws = [(rng.random(n) < pg, rng.random(n) < pc, rng.random(n) < pf) for _ in range(T)]
        for opt in ("none", "bonferroni", "bernstein"):
            b = np.array([bernstein_loss_bound(G, C, F, a, h, al) if opt == "bernstein" else
                          cp_loss_bound(G, C, F, a, h, al, opt).total for G, C, F in draws])
            rows.append({"choice": "stage2.loss_ci", "option": opt, "n": n, "truth": truth,
                         "coverage": float((b >= truth).mean()), "width": float(np.mean(b - truth))})
    grid = cfg.lambda_grid()
    for n in at["sim_n"]:
        for opt, B in (("one", 1.0), ("a", a)):
            risk, lam = [], []
            for _ in range(T):
                dang = rng.random(n + 1) < at["sim_dangerous"]
                sc = np.where(dang, rng.beta(*at["sim_beta_dangerous"], n + 1), rng.beta(*at["sim_beta_benign"], n + 1))
                l_ = calibrate_lambda(sc[:n], dang[:n], a, cfg.stage2["alpha_user"], grid, B).lambda_hat
                lam.append(l_ if math.isfinite(l_) else 1.0)
                risk.append(crc_losses(sc[n:], dang[n:], l_, a)[0] if math.isfinite(l_) else 0.0)
            se = float(np.std(risk, ddof=1) / math.sqrt(len(risk))) if len(risk) > 1 else 0.0
            rows.append({"choice": "stage2.crc_loss_bound", "option": opt, "n": n, "truth": cfg.stage2["alpha_user"],
                         "mean_risk": float(np.mean(risk)),
                         # Monte-Carlo noise: the guarantee E[L] ≤ α_user is rejected only if the simulated mean
                         # risk exceeds α_user by more than two standard errors
                         "coverage": float(np.mean(risk) <= cfg.stage2["alpha_user"] + MC_Z * se),
                         "width": float(np.mean(lam))})       # smaller λ̂ = more calls go to the user
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "estimators.csv", index=False)
    adopt, empirical_only = {}, {}
    proven = at.get("proven_options") or {}
    for choice, g in df.groupby("choice"):
        ok = g.groupby("option").agg(cov=("coverage", "min"), width=("width", "mean"))
        ok = ok[ok["cov"] >= (target if choice == "stage2.loss_ci" else 1.0)]
        if at.get("require_proven", True) and choice in proven:
            best_any = ok["width"].idxmin() if len(ok) else None
            ok = ok[ok.index.isin(proven[choice])]
            if best_any is not None and best_any not in ok.index:
                empirical_only[choice] = best_any          # tighter, covered in simulation, but no proof
        if len(ok):
            adopt[choice] = ok["width"].idxmin()
    before = {c: cfg.stage2.get(c.split(".", 1)[1]) for c in df["choice"].unique()}
    return {"adopt": adopt, "empirical_only": empirical_only, "before": before, "table": df.groupby(["choice", "option"])[["coverage", "width"]].mean().round(4)
            .reset_index().to_dict("records")}


# =============================================================================== 2 · defence
def candidate_specs(cfg) -> dict[str, dict]:
    """Every combination in autotune.candidates × autotune.sandwich, named by its parameters."""
    out = {}
    for base, grid in cfg.autotune["candidates"].items():
        keys_, vals = list(grid), [grid[k] for k in grid]
        for combo in itertools.product(*vals):
            for sw in cfg.autotune["sandwich"]:
                spec = {"base": base, **dict(zip(keys_, combo)), "sandwich": bool(sw)}
                tag = "_".join(f"{k.split('_')[-1]}{v}" for k, v in zip(keys_, combo))
                out[f"{base}_{tag}" + ("_sw" if sw else "")] = spec
    return out


def _same_as_builtin(cfg, spec: dict) -> bool:
    s1 = cfg.stage1
    return (not spec["sandwich"] and spec["base"] in s1["variants"]
            and all(s1.get(k) == v for k, v in spec.items() if k not in ("base", "sandwich")))


def _load(split: str, kind: str) -> list[dict]:
    return [json.loads(line) for line in (PROC / f"stage1_{split}_{kind}.jsonl").read_text().splitlines()]


def _evaluate(cfg, variants: dict, attacks: list[dict], clean: list[dict], victim: LLM, model_tag: str) -> None:
    """Append A[x, τ] (trials.csv) and token-F1 (clean.csv) for every missing (model, variant, case)."""
    OUT.mkdir(parents=True, exist_ok=True)
    tp, cp_ = OUT / "trials.csv", OUT / "clean.csv"
    done_t = set(map(tuple, pd.read_csv(tp)[["model", "variant", "attack_id"]].astype(str).to_numpy())) \
        if tp.exists() else set()
    done_c = set(map(tuple, pd.read_csv(cp_)[["model", "variant", "doc_id"]].astype(str).to_numpy())) \
        if cp_.exists() else set()
    jobs = [("a", n, a) for n in variants for a in attacks if (model_tag, n, a["id"]) not in done_t]
    jobs += [("c", n, c) for n in variants for c in clean if (model_tag, n, c["id"]) not in done_c]

    def one(job):
        kind, name, x = job
        v = variants[name]
        if kind == "a":
            p = build_prompt(v, x["task"], inject(x["document"], x["injection"], x["position"]))
            row = {"model": model_tag, "variant": name, "attack_id": x["id"],
                   "A": attack_indicator(victim.chat(p.messages), x["canary"], v, p.key)}
            path = tp
        else:
            p = build_prompt(v, x["task"], x["document"])
            row = {"model": model_tag, "variant": name, "doc_id": x["id"],
                   "utility": utility(victim.chat(p.messages), x["reference"], cfg.stage1["utility_metric"])}
            path = cp_
        with _LOCK:
            pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)

    with ThreadPoolExecutor(max_workers=cfg.models["parallel_requests"]) as ex:
        list(ex.map(one, jobs))


def _score(cfg, variants: dict, attack_ids: list[str], clean_ids: list[str], model_tag: str) -> pd.DataFrame:
    """Per variant on the given paired cases: p̂, p̄_CP, U, forging, R̄ (adaptive term measured later in Stage 1)."""
    t = pd.read_csv(OUT / "trials.csv")
    c = pd.read_csv(OUT / "clean.csv")
    t = t[(t["model"] == model_tag) & t["attack_id"].astype(str).isin(attack_ids)]
    c = c[(c["model"] == model_tag) & c["doc_id"].astype(str).isin(clean_ids)]
    q, rho, al = cfg.theory["q_main"], cfg.theory["rho_main"], cfg.stage1["alpha"]
    rows = []
    for name, v in variants.items():
        g = t[t["variant"] == name]
        s, n = int(g["A"].sum()), len(g)
        pbar = stats.cp_upper(s, n, al)
        risk = stats.combined_risk(q, v.log_key_space(), pbar, 0.0, rho)
        rows.append({"variant": name, "n": n, "s": s, "p_hat": s / n if n else float("nan"), "p_bar": pbar,
                     "U": float(c.loc[c["variant"] == name, "utility"].mean()), "log10_K": v.log10_key_space(),
                     "forging": risk.forging, "R": risk.total})
    df = pd.DataFrame(rows).set_index("variant")
    u_none = df.loc["none", "U"] if "none" in df.index else df["U"].max()
    df["feasible"] = df["U"] >= u_none - cfg.theory["eta_main"]
    return df


def _paired(variant_a: str, variant_b: str, attack_ids: list[str], model_tag: str) -> float:
    t = pd.read_csv(OUT / "trials.csv")
    t = t[(t["model"] == model_tag) & t["attack_id"].astype(str).isin(attack_ids)]
    piv = t.pivot_table(index="attack_id", columns="variant", values="A").dropna()
    if variant_a not in piv or variant_b not in piv:
        return 1.0
    return stats.mcnemar_pvalue(piv[variant_a].to_numpy(), piv[variant_b].to_numpy())


def defence_track(cfg, victim: LLM, dry_run: bool) -> dict:
    at, s1 = cfg.autotune, cfg.stage1
    specs = {k: v for k, v in candidate_specs(cfg).items() if not _same_as_builtin(cfg, v) and k not in s1["variants"]}
    base = build_variants(s1)
    incumbents = [n for n, v in base.items() if n == "none" or v.log_key_space() > 0]   # none + keyed variants
    cand = build_variants({**s1, "variants": list(specs), "extra_variants": specs})
    attacks = _load("fit", "attacks")                  # fit split only: the report split stays untouched
    clean = _load("fit", "clean")[:at["n_clean"]]
    perm = np.random.default_rng(cfg.run["seed"]).permutation(len(attacks))
    attacks = [attacks[i] for i in perm]
    alive, n, rounds = list(cand), at["n_start"], []
    while True:
        sub = attacks[:min(n, at["n_max"], len(attacks))]
        _evaluate(cfg, {**{k: base[k] for k in incumbents}, **{k: cand[k] for k in alive}}, sub, clean, victim,
                  "victim")
        ids = [a["id"] for a in sub]
        sc = _score(cfg, {**{k: base[k] for k in incumbents}, **{k: cand[k] for k in alive}}, ids,
                    [c["id"] for c in clean], "victim")
        ranked = sc.loc[alive].sort_values(["feasible", "R"], ascending=[False, True])
        rounds.append({"n_attacks": len(sub), "alive": alive, "scores": sc.round(4).reset_index().to_dict("records")})
        print(f"[autotune] round {len(rounds)}: {len(sub)} attacks, {len(alive)} candidates; best "
              f"{ranked.index[0]} R̄={ranked['R'].iloc[0]:.3f}", flush=True)
        last = len(alive) <= 1 or len(rounds) >= at["max_rounds"] or len(sub) >= min(at["n_max"], len(attacks))
        kept = alive if last else list(ranked.index[:max(1, math.ceil(len(alive) / 2))])
        from horizon import messages
        notify(messages.autotune_round(cfg, len(rounds), len(sub), alive,
                                       sc.reset_index().to_dict("records"), kept))
        if last:
            break
        alive = kept
        n *= 2
    keyed = sc.loc[[k for k in incumbents if k != "none"]]
    keyed = keyed[keyed["feasible"]] if keyed["feasible"].any() else keyed
    inc = keyed["R"].idxmin()
    adopted = []
    for name in ranked.index:
        r = sc.loc[name]
        worse = r["p_hat"] > sc.loc[inc, "p_hat"] and _paired(name, inc, ids, "victim") < cfg.stage1["alpha"]
        if r["feasible"] and r["R"] <= sc.loc[inc, "R"] - at["min_risk_gain"] and not worse:
            adopted.append(name)
        if len(adopted) >= at["max_adopt"]:
            break
    result = {"incumbent": inc, "incumbent_R": float(sc.loc[inc, "R"]), "adopted": adopted,
              "adopted_specs": {k: specs[k] for k in adopted}, "rounds": rounds,
              "final": sc.round(4).reset_index().to_dict("records")}
    if adopted and not dry_run:
        over = yaml.safe_load(AUTOTUNED.read_text()) if AUTOTUNED.exists() else {}
        st = over.setdefault("stage1", {})
        st["variants"] = list(dict.fromkeys([*s1["variants"], *adopted]))
        st["extra_variants"] = {**(s1.get("extra_variants") or {}), **result["adopted_specs"]}
        AUTOTUNED.write_text(yaml.safe_dump(over, sort_keys=False, allow_unicode=True))
    return result


# =============================================================================== 3 · models
def model_track(cfg, variant_name: str) -> list[dict]:
    """Same quick paired test for every listed Ollama model: none vs the chosen variant. Report only."""
    import urllib.request
    try:
        with urllib.request.urlopen(cfg.models["api_base"] + "/api/tags", timeout=POLL_S) as r:
            have = {m["name"] for m in json.loads(r.read())["models"]}
    except Exception:
        have = set()
    at = cfg.autotune
    attacks = _load("fit", "attacks")[:at["model_n"]]
    clean = _load("fit", "clean")[:at["n_clean"]]
    s1 = load_config().stage1
    variants = {k: v for k, v in build_variants(s1).items() if k in ("none", variant_name)}
    out = []
    for m in at["models"]:
        if m not in have and f"{m}:latest" not in have:
            out.append({"model": m, "status": "not pulled"})
            continue
        mc = dict(cfg.models, disable_thinking=cfg.models["disable_thinking"] and m.startswith("qwen3"))
        _evaluate(cfg, variants, attacks, clean, LLM(f"ollama_chat/{m}", mc), m)
        sc = _score(cfg, variants, [a["id"] for a in attacks], [c["id"] for c in clean], m)
        out += [{"model": m, "variant": k, "asr": round(float(r["p_hat"]), 3), "U": round(float(r["U"]), 3)}
                for k, r in sc.iterrows()]
    return out


POLL_S = 10


def main() -> int:
    ap = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                 formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--track", choices=["all", "estimators", "defence", "models"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="evaluate and report, adopt nothing")
    args = ap.parse_args()
    cfg = config_from_args(args)
    if not cfg.autotune.get("enabled", True):
        print("autotune disabled (autotune.enabled: false)")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    summary = json.loads((OUT / "summary.json").read_text()) if (OUT / "summary.json").exists() else {}
    rng = np.random.default_rng(cfg.run["seed"])
    if args.track in ("all", "estimators"):
        est = estimator_track(cfg, rng)
        summary["estimators"] = est
        if est["adopt"] and not args.dry_run:
            over = yaml.safe_load(AUTOTUNED.read_text()) if AUTOTUNED.exists() else {}
            for dotted, opt in est["adopt"].items():
                sec, key = dotted.split(".")
                over.setdefault(sec, {})[key] = opt
            AUTOTUNED.write_text(yaml.safe_dump(over, sort_keys=False, allow_unicode=True))
        print("[autotune] estimators:", est["adopt"], flush=True)
        from horizon import messages
        notify(messages.autotune_estimators(cfg, est, est["before"]))
    needs_llm = args.track in ("all", "defence", "models")
    if needs_llm and not server_alive(cfg.models):
        sys.exit("model server not reachable — start it with scripts/serve_local.sh")
    if args.track in ("all", "defence"):
        victim = LLM(cfg.models["q_llm"], cfg.models)
        d = defence_track(cfg, victim, args.dry_run)
        summary["defence"] = d
        (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        from horizon import messages
        notify(messages.autotune_defence(cfg, d, args.dry_run))
    if args.track in ("all", "models"):
        name = (summary.get("defence") or {}).get("adopted", [None])[0] if (summary.get("defence") or {}).get(
            "adopted") else (summary.get("defence") or {}).get("incumbent", "datamark_dynamic")
        summary["models"] = model_track(cfg, name)
        from horizon import messages
        notify(messages.autotune_models(cfg, summary["models"]))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
