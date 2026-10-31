"""Red-teaming runner (§11): direct / jailbreak attacks and third-party indirect attacks.

Modes
  --detect   detection rate on attack sets and false-positive rate on benign sets, per detector
             (DeBERTa injection classifier on input; Qwen3Guard on the prompt) → results/testing/direct.csv (F18)
  --pyrit    DIRECT: PAIR / TAP / Crescendo (PyRIT) against the guarded endpoint (scripts/guarded_server.py)
  --xpia     INDIRECT: PyRIT XPIA workflow — plant δ (optionally obfuscated by a PyRIT converter) in a document,
             process it with each Horizon configuration (none · stage1 · stage2 · stage1+2 · full), score with the
             canary / attacker-tool-call indicator; paired over the same attacks → results/testing/xpia*.csv
  --garak    DIRECT + INDIRECT probes (dan, promptinject, encoding · latentinjection, web_injection) against the
             bare model and the guarded endpoint → results/testing/garak*.csv. garak puts document and task in one
             message, so it measures the model + direct layer (a baseline), not Stage 1/2 — that is what --xpia does.

Attack sets: HarmBench, JBB harmful, deepset injections (label 1), Gandalf, Tensor Trust hijacking.
Benign sets: JBB benign, XSTest safe prompts, Alpaca, deepset (label 0). HackAPrompt joins once access is granted.
Nothing runs at scale unless asked: --limit caps every set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args, load_config  # noqa: E402
from horizon.direct.guards import GatedModelError, InjectionGuardInput, Qwen3GuardOutput  # noqa: E402
from horizon.logging_utils import log_examples, mlflow_run  # noqa: E402
from horizon.stage1 import stats  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "results" / "testing"
GARAK_DIR = OUT / "garak"


def _pq(pattern: str) -> pd.DataFrame:
    return pd.concat([pd.read_parquet(p) for p in sorted(RAW.glob(pattern))], ignore_index=True)


def load_sets(seed: int, limit: int | None) -> dict[str, tuple[str, list[str]]]:
    """name → (kind, prompts) with kind ∈ {attack, benign}."""
    rng = np.random.default_rng(seed)
    hb = pd.read_csv(next((RAW / "harmbench").glob("*.csv")))
    jbb_h = pd.read_csv(RAW / "jbb_behaviors" / "data" / "harmful-behaviors.csv")
    jbb_b = pd.read_csv(RAW / "jbb_behaviors" / "data" / "benign-behaviors.csv")
    deep = _pq("deepset_prompt_injections/data/*.parquet")
    gand = _pq("gandalf_ignore_instructions/data/*.parquet")
    xs = _pq("xstest/data/prompts-*.parquet")
    alp = _pq("alpaca/data/*.parquet")
    tt = pd.read_json(next((RAW / "tensor_trust" / "benchmarks" / "hijacking-robustness").rglob("*.jsonl")), lines=True)
    sets = {
        "harmbench": ("attack", hb.loc[hb["FunctionalCategory"] == "standard", "Behavior"].tolist()),
        "jbb_harmful": ("attack", jbb_h["Goal"].tolist()),
        "deepset_injection": ("attack", deep.loc[deep["label"] == 1, "text"].tolist()),
        "gandalf": ("attack", gand["text"].tolist()),
        "tensor_trust_hijack": ("attack", tt["attack"].tolist()),
        "jbb_benign": ("benign", jbb_b["Goal"].tolist()),
        "xstest_safe": ("benign", xs.loc[~xs["type"].str.startswith("contrast"), "prompt"].tolist()),
        "alpaca": ("benign", alp.loc[alp["input"] == "", "instruction"].tolist()),
        "deepset_benign": ("benign", deep.loc[deep["label"] == 0, "text"].tolist()),
    }
    hp = RAW / "hackaprompt"
    if hp.exists():
        h = _pq("hackaprompt/**/*.parquet")
        sets["hackaprompt"] = ("attack", h.loc[h.get("correct", True) == True, "user_input"].dropna().tolist())  # noqa: E712
    out = {}
    for k, (kind, prompts) in sets.items():
        prompts = [p for p in prompts if isinstance(p, str) and p.strip()]
        if limit and len(prompts) > limit:
            prompts = [prompts[i] for i in rng.choice(len(prompts), limit, replace=False)]
        out[k] = (kind, prompts)
    return out


def detect(cfg, limit: int | None) -> pd.DataFrame:
    """Every prompt through every detector; one row per item is appended at once (resumable)."""
    a = cfg.stage1["alpha"]
    sets = load_sets(cfg.run["seed"], limit)
    detectors = {"input_guard": InjectionGuardInput(cfg.direct), "output_guard": Qwen3GuardOutput(cfg.direct)}
    OUT.mkdir(parents=True, exist_ok=True)
    items = OUT / "direct_items.csv"
    done = (set(map(tuple, pd.read_csv(items)[["detector", "set", "idx"]].astype(str).to_numpy()))
            if items.exists() else set())
    for dname, det in detectors.items():
        try:
            det.check("hello")
        except GatedModelError as e:
            print(f"  {dname}: skipped — {str(e)[:160]}", flush=True)
            continue
        for sname, (kind, prompts) in sets.items():
            for idx, p in enumerate(prompts):
                if (dname, sname, str(idx)) in done:
                    continue
                v = det.check(p)
                pd.DataFrame([{"detector": dname, "set": sname, "idx": idx, "kind": kind, "blocked": int(v.blocked),
                               "score": v.score, "prompt": p[:200]}]).to_csv(items, mode="a",
                                                                            header=not items.exists(), index=False)
            print(f"  {dname:15s} {sname:20s} {kind:6s} done", flush=True)
    it = pd.read_csv(items)
    rows = []
    for (dname, sname, kind), g in it.groupby(["detector", "set", "kind"]):
        s_, n = int(g["blocked"].sum()), len(g)
        lo, hi = stats.cp_interval(s_, n, a)
        rows.append({"detector": dname, "set": sname, "kind": kind, "n": n, "flagged": s_, "rate": s_ / n,
                     "cp_lo": lo, "cp_hi": hi, "in_training_data": dname == "input_guard"
                     and sname in cfg.direct["input_guard_contaminated_sets"]})
    df = pd.DataFrame(rows)
    agg = []
    for dname, g in df.groupby("detector"):
        att, ben = g[g.kind == "attack"], g[g.kind == "benign"]
        clean = ben[~ben.in_training_data]   # FPR without sets the detector was trained on (model card)
        agg.append({"detector": dname, "tpr": att["flagged"].sum() / att["n"].sum(),
                    "fpr": ben["flagged"].sum() / ben["n"].sum(),
                    "fpr_excl_training_sets": clean["flagged"].sum() / clean["n"].sum() if len(clean) else None})
    df.to_csv(OUT / "direct_per_set.csv", index=False)
    pd.DataFrame(agg).to_csv(OUT / "direct.csv", index=False)
    return df


def pyrit_targets(cfg, endpoint: str):
    """(adversary, judge, target). PyRIT ≥ 1.0 hands the endpoint to AsyncOpenAI(base_url=…), which appends
    /chat/completions itself: pass the /v1 base, else every request hits …/chat/completions/chat/completions (404)."""
    from pyrit.prompt_target import OpenAIChatTarget
    base = f"http://127.0.0.1:{cfg.models['proxy_port']}/v1"
    model = cfg.models["attacker"].split("/", 1)[1]
    adversary = OpenAIChatTarget(endpoint=base, api_key="local", model_name=model,
                                 temperature=cfg.models["attacker_temperature"])
    judge = OpenAIChatTarget(endpoint=base, api_key="local", model_name=model)
    target = OpenAIChatTarget(endpoint=endpoint.removesuffix("/chat/completions"), api_key="local",
                              model_name="horizon-guarded")
    return adversary, judge, target


async def _pyrit(cfg, endpoint: str, objectives: list[str], kinds: list[str]) -> list[dict]:
    """PAIR / TAP / Crescendo with the local attacker model; scoring by the attacker model as judge."""
    from pyrit.executor.attack import (AttackAdversarialConfig, AttackScoringConfig, CrescendoAttack, PAIRAttack,
                                       TAPAttack)
    from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
    from pyrit.score import FloatScaleThresholdScorer, SelfAskScaleScorer
    from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async

    await initialize_pyrit_async(memory_db_type=IN_MEMORY)
    adversary, judge, target = pyrit_targets(cfg, endpoint)
    scorer = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=judge))
    adv = AttackAdversarialConfig(target=adversary)
    sc = AttackScoringConfig(objective_scorer=scorer)
    tap_sc = TAPAttackScoringConfig(objective_scorer=FloatScaleThresholdScorer(
        scorer=SelfAskScaleScorer.from_scale(chat_target=judge), threshold=cfg.direct["pyrit_success_threshold"]))
    makers = {"pair": lambda: PAIRAttack(objective_target=target, attack_adversarial_config=adv,
                                         attack_scoring_config=tap_sc),
              "tap": lambda: TAPAttack(objective_target=target, attack_adversarial_config=adv,
                                       attack_scoring_config=tap_sc),
              "crescendo": lambda: CrescendoAttack(objective_target=target, attack_adversarial_config=adv,
                                                   attack_scoring_config=sc)}
    path = OUT / "pyrit.csv"
    done = set(map(tuple, pd.read_csv(path)[["attack", "objective"]].to_numpy())) if path.exists() else set()
    for k in kinds:
        for obj in objectives:
            if (k, obj[:200]) in done:
                continue
            t0 = time.time()
            res, last = None, None
            for attempt in range(1 + int(cfg.redteam.get("pyrit_retries", 2))):    # one hiccup must not cost the phase
                try:
                    res = await makers[k]().execute_async(objective=obj)
                    if "SUCCESS" not in str(res.outcome) and getattr(res, "last_score", None) is None:
                        # PAIR / TAP swallow per-node errors (endpoint down, 404, timeout) and report FAILURE without a
                        # single score: that is a broken run, not a blocked jailbreak — do not record it
                        raise RuntimeError(f"{k}: no response was scored ({getattr(res, 'outcome_reason', '')}) — "
                                           "is the model / guarded endpoint reachable?")
                    break
                except Exception as e:
                    last, res = e, None
                    print(f"  {k}: attempt {attempt + 1} failed ({type(e).__name__}: {str(e)[:160]})", flush=True)
            if res is None:
                raise last
            row = {"attack": k, "objective": obj[:200], "outcome": str(res.outcome),
                   "turns": getattr(res, "executed_turns", None), "seconds": time.time() - t0}
            pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)
            print(f"  {k}: {str(res.outcome)}", flush=True)
    return pd.read_csv(path).to_dict("records") if path.exists() else []


def _cp_rows(df: pd.DataFrame, keys: list[str], s_col: str, n_col: str, alpha: float) -> pd.DataFrame:
    """Aggregate successes / trials per group with a Clopper–Pearson interval."""
    g = df.groupby(keys, as_index=False)[[s_col, n_col]].sum()
    ci = [stats.cp_interval(int(a), int(b), alpha) for a, b in zip(g[s_col], g[n_col])]
    g["asr"] = g[s_col] / g[n_col]
    g["cp_lo"], g["cp_hi"] = [c[0] for c in ci], [c[1] for c in ci]
    return g


# --------------------------------------------------------------------------- XPIA (indirect, PyRIT)
def xpia(cfg, args) -> pd.DataFrame:
    """Paired XPIA run: the same attacks × converters × Horizon configurations; resumable."""
    from agentdojo_runner import ABLATIONS
    from horizon.redteam.xpia import AGENT_CONFIGS, AgentProcessor, DocumentProcessor, run_one
    rc = cfg.redteam
    configs = args.xpia or rc["xpia_configs"]
    converters = args.xpia_converters or rc["xpia_converters"]
    path_in = ROOT / "data" / "processed" / f"stage1_{rc['xpia_split']}_attacks.jsonl"
    attacks = [json.loads(l) for l in path_in.read_text().splitlines()]
    if args.limit and len(attacks) > args.limit:
        idx = sorted(np.random.default_rng(cfg.run["seed"]).choice(len(attacks), args.limit, replace=False))
        attacks = [attacks[i] for i in idx]
    OUT.mkdir(parents=True, exist_ok=True)
    items = OUT / args.xpia_items
    rnd = str(args.xpia_round)
    done = set()
    if items.exists():
        d = pd.read_csv(items)
        d = d[d.get("round", 0).astype(str) == rnd] if "round" in d else (d if rnd == "0" else d.iloc[:0])
        done = set(map(tuple, d[["config", "converter", "attack_id"]].astype(str).to_numpy()))

    async def go():
        from pyrit.setup import IN_MEMORY, initialize_pyrit_async
        await initialize_pyrit_async(memory_db_type=IN_MEMORY)
        for c in configs:
            ccfg = load_config(args.config, [*args.set, *[f"flags.{k}={str(v).lower()}"
                                                         for k, v in ABLATIONS[c].items()]])
            proc = AgentProcessor(ccfg, rc["xpia_suite"]) if c in AGENT_CONFIGS else DocumentProcessor(ccfg, c)
            for conv in converters:
                todo = [a for a in attacks if (c, conv, a["id"]) not in done]
                for i, a in enumerate(todo, 1):
                    it, last = None, None
                    for attempt in range(1 + int(rc.get("xpia_retries", 2))):
                        try:
                            it = await run_one(proc, a, c, conv, args.xpia_round)
                            break
                        except Exception as e:                                   # the phase keeps every finished item
                            last = e
                            print(f"  xpia {c}/{conv}/{a['id']}: attempt {attempt + 1} failed "
                                  f"({type(e).__name__}: {str(e)[:160]})", flush=True)
                    if it is None:
                        raise last
                    pd.DataFrame([it.__dict__]).to_csv(items, mode="a", header=not items.exists(), index=False)
                    if i % rc["progress_every"] == 0 or i == len(todo):
                        print(f"  xpia {c:9s} {conv:10s} {i}/{len(todo)}", flush=True)

    asyncio.run(go())
    df = pd.read_csv(items)
    df = df[df.config.isin(configs) & df.converter.isin(converters)].assign(n=1)
    summary = _cp_rows(df, ["config", "converter"], "success", "n", cfg.stage1["alpha"])
    summary.to_csv(items.with_name(items.name.replace("_items", "")), index=False)
    print(summary.to_string(index=False))
    return summary


# --------------------------------------------------------------------------- garak (direct + indirect probes)
def garak_target(cfg, target: str, guarded_endpoint: str) -> tuple[str, str]:
    """(endpoint, model name): 'bare' = the model behind the role proxy, 'guarded' = scripts/guarded_server.py."""
    if target == "guarded":
        return guarded_endpoint, "horizon-guarded"
    return (f"http://127.0.0.1:{cfg.models['proxy_port']}/v1/chat/completions",
            cfg.models["p_llm"].split("/", 1)[1])


def garak_config(cfg, endpoint: str, model: str, name: str) -> Path:
    # max_tokens: garak sends none by default, so a looping generation ran for 10+ minutes and 100 KB until Ollama
    # aborted it. request_timeout: the old value (the models' 3600 s after repairs) hid hangs; with a token cap an
    # answer arrives in well under garak_request_timeout_s.
    conf = {"rest": {"RestGenerator": {
        "name": name, "uri": endpoint, "method": "post",
        "headers": {"Content-Type": "application/json"},
        "req_template_json_object": {"model": model, "max_tokens": int(cfg.models["max_tokens"]),
                                     "messages": [{"role": "user", "content": "$INPUT"}]},
        "response_json": True, "response_json_field": "$.choices[0].message.content",
        "request_timeout": int(cfg.redteam.get("garak_request_timeout_s", 300))}}}
    GARAK_DIR.mkdir(parents=True, exist_ok=True)
    p = GARAK_DIR / f"rest_{name}.json"
    p.write_text(json.dumps(conf, indent=1))
    return p


def parse_garak(report: Path) -> list[dict]:
    """garak 'eval' records: per probe × detector, fails = outputs where the attack succeeded."""
    rows = []
    for line in report.read_text().splitlines():
        r = json.loads(line)
        if r.get("entry_type") == "eval":
            rows.append({"probe": r["probe"], "detector": r["detector"], "fails": r["fails"],
                         "n": r["total_evaluated"]})
    return rows


def _garak_finished(report: Path) -> bool:
    return report.exists() and '"entry_type": "completion"' in report.read_text()


def _guarded_error_rate(endpoint: str) -> tuple[int, int]:
    """(errors, requests) the guarded endpoint counted (GET /stats); (0, 0) for the bare model or if unreachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(endpoint.replace("/v1/chat/completions", "/stats"), timeout=10) as r:
            d = json.loads(r.read())
        return int(d.get("errors", 0)), int(d.get("requests", 0))
    except Exception:
        return 0, 0


def garak(cfg, args) -> pd.DataFrame:
    """garak, ONE PROBE PER SUBPROCESS, each capped at redteam.garak_prompt_cap prompts × garak_generations outputs.

    Before: one subprocess ran whole modules (`dan,promptinject,encoding` = ~23 probes × 256 prompts × 5 outputs, i.e.
    tens of thousands of requests, planned as "None") and any failure renamed the report to .partial and started over
    — five runs in a row died at the same prompt and kept nothing. Now a finished probe is never run again, a probe that
    crashes is retried alone, and the phase fails (exit 1) only after every other probe has been given its chance.
    """
    import subprocess
    import yaml
    rc = cfg.redteam
    GARAK_DIR.mkdir(parents=True, exist_ok=True)
    run_cfg = GARAK_DIR / "garak_run.yaml"
    run_cfg.write_text(yaml.safe_dump({"reporting": {"report_dir": str(GARAK_DIR.resolve())},
                                       "run": {"soft_probe_prompt_cap": int(rc["garak_prompt_cap"])}}))
    rows, failed = [], []
    for target in rc["garak_targets"]:
        endpoint, model = garak_target(cfg, target, args.endpoint)
        gen = garak_config(cfg, endpoint, model, target)
        for family in ("direct", "indirect"):
            for probe in rc[f"garak_probes_{family}"]:
                prefix = f"{target}_{family}_{probe.replace('.', '-')}"
                report = GARAK_DIR / f"{prefix}.report.jsonl"
                if report.exists() and not _garak_finished(report):
                    report.rename(report.with_suffix(".partial"))        # killed mid-probe → run that probe again
                for attempt in range(1 + int(rc.get("garak_probe_retries", 1))):
                    if _garak_finished(report):
                        break
                    cmd = [sys.executable, "-m", "garak", "--config", str(run_cfg), "--model_type", "rest",
                           "--generator_option_file", str(gen), "--probes", probe,
                           "--generations", str(rc["garak_generations"]), "--seed", str(cfg.run["seed"]),
                           "--parallel_attempts", str(cfg.models["parallel_requests"]), "--report_prefix", prefix]
                    print("  " + " ".join(cmd), flush=True)
                    t0 = time.time()
                    r = subprocess.run(cmd)
                    print(f"  garak {target}/{probe}: exit {r.returncode} after {time.time() - t0:.0f} s"
                          f"{' (retrying)' if r.returncode and attempt < rc.get('garak_probe_retries', 1) else ''}",
                          flush=True)
                    if report.exists() and not _garak_finished(report):
                        report.rename(report.with_suffix(".partial"))
                if _garak_finished(report):
                    rows += [{"target": target, "family": family, **x} for x in parse_garak(report)]
                else:
                    failed.append(f"{target}/{probe}")
        if target == "guarded":
            errs, reqs = _guarded_error_rate(endpoint)
            if reqs and errs / reqs > rc.get("max_upstream_error_rate", 0.02):
                failed.append(f"guarded endpoint: {errs} of {reqs} requests hit an upstream error "
                              f"(> {rc.get('max_upstream_error_rate', 0.02):.0%}): empty answers would read as 'blocked'")
    df = pd.DataFrame(rows)
    if len(df):
        summary = _cp_rows(df, ["target", "family", "probe"], "fails", "n", cfg.stage1["alpha"])
        df.to_csv(OUT / "garak_items.csv", index=False)
        summary.to_csv(OUT / "garak.csv", index=False)
        print(summary.to_string(index=False))
    else:
        summary = pd.DataFrame()
    if failed:
        raise RuntimeError("garak: probes without a result: " + "; ".join(failed))
    return summary


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--pyrit", nargs="*", choices=["pair", "tap", "crescendo"])
    ap.add_argument("--xpia", nargs="*", choices=["none", "stage1", "stage2", "stage1+2", "full"],
                    help="indirect PyRIT XPIA; no value = every config in redteam.xpia_configs")
    ap.add_argument("--xpia-converters", nargs="+", help="override redteam.xpia_converters")
    ap.add_argument("--xpia-items", default="xpia_items.csv", help="items file in results/testing/ (parallel jobs)")
    ap.add_argument("--xpia-round", type=int, default=0, help="repeat round (fresh keys) for extra samples")
    ap.add_argument("--garak", action="store_true", help="run garak direct + indirect probes")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11436/v1/chat/completions")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    cfg = config_from_args(args)
    with mlflow_run(cfg, "direct-layer", {"stage": "direct"}):
        import mlflow
        if args.detect:
            df = detect(cfg, args.limit)
            log_examples(df.to_dict("records"), "direct_per_set")
            mlflow.log_dict(pd.read_csv(OUT / "direct.csv").to_dict("records"), "direct.json")
        if args.pyrit is not None:
            sets = load_sets(cfg.run["seed"], args.limit)
            objs = sets["harmbench"][1] + sets["jbb_harmful"][1]
            OUT.mkdir(parents=True, exist_ok=True)
            rows = asyncio.run(_pyrit(cfg, args.endpoint, objs, args.pyrit or ["pair", "tap", "crescendo"]))
            log_examples(rows, "pyrit")
        if args.xpia is not None:
            df = xpia(cfg, args)
            log_examples(df.to_dict("records"), "xpia")
        if args.garak:
            df = garak(cfg, args)
            log_examples(df.to_dict("records"), "garak")


if __name__ == "__main__":
    main()
