"""Direct / jailbreak layer evaluation (§11). AgentDojo never exercises this layer.

Modes
  --detect   detection rate on attack sets and false-positive rate on benign sets, per detector
             (Prompt Guard 2 via LlamaFirewall on input; Llama Guard on the prompt) → results/testing/direct.csv (F18)
  --pyrit    PAIR / TAP / Crescendo (PyRIT) against the guarded endpoint (scripts/guarded_server.py)
  --garak    writes a garak REST-generator config for the guarded endpoint and prints the command

Attack sets: HarmBench, JBB harmful, deepset injections (label 1), Gandalf, Tensor Trust hijacking.
Benign sets: JBB benign, XSTest safe prompts, Alpaca, deepset (label 0). HackAPrompt joins once access is granted.
Nothing runs at scale unless asked: --limit caps every set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402
from horizon.direct.guards import LlamaGuardOutput, PromptGuardInput  # noqa: E402
from horizon.logging_utils import log_examples, mlflow_run  # noqa: E402
from horizon.stage1 import stats  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "results" / "testing"


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
    a = cfg.stage1["alpha"]
    sets = load_sets(cfg.run["seed"], limit)
    detectors = {"prompt_guard_2": PromptGuardInput(cfg.direct), "llama_guard": LlamaGuardOutput(cfg.direct)}
    rows, per_item = [], []
    for dname, det in detectors.items():
        for sname, (kind, prompts) in sets.items():
            flags = []
            for p in prompts:
                v = det.check(p) if dname == "prompt_guard_2" else det.check(p, "")
                flags.append(int(v.blocked))
                per_item.append({"detector": dname, "set": sname, "kind": kind, "blocked": int(v.blocked),
                                 "score": v.score, "prompt": p[:200]})
            s, n = sum(flags), len(flags)
            lo, hi = stats.cp_interval(s, n, a)
            rows.append({"detector": dname, "set": sname, "kind": kind, "n": n, "flagged": s, "rate": s / n,
                         "cp_lo": lo, "cp_hi": hi})
            print(f"  {dname:15s} {sname:20s} {kind:6s} {s}/{n}", flush=True)
    df = pd.DataFrame(rows)
    agg = []
    for dname, g in df.groupby("detector"):
        att, ben = g[g.kind == "attack"], g[g.kind == "benign"]
        agg.append({"detector": dname, "tpr": att["flagged"].sum() / att["n"].sum(),
                    "fpr": ben["flagged"].sum() / ben["n"].sum()})
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "direct_per_set.csv", index=False)
    pd.DataFrame(agg).to_csv(OUT / "direct.csv", index=False)
    pd.DataFrame(per_item).to_csv(OUT / "direct_items.csv", index=False)
    return df


async def _pyrit(cfg, endpoint: str, objectives: list[str], kinds: list[str]) -> list[dict]:
    """PAIR / TAP / Crescendo with the local attacker model; scoring by the attacker model as judge."""
    from pyrit.executor.attack import (AttackAdversarialConfig, AttackScoringConfig, CrescendoAttack, PAIRAttack,
                                       TAPAttack)
    from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
    from pyrit.score import FloatScaleThresholdScorer, SelfAskScaleScorer
    from pyrit.prompt_target import OpenAIChatTarget
    from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async

    await initialize_pyrit_async(memory_db_type=IN_MEMORY)
    base = f"http://127.0.0.1:{cfg.models['proxy_port']}/v1"
    model = cfg.models["attacker"].split("/", 1)[1]
    adversary = OpenAIChatTarget(endpoint=base + "/chat/completions", api_key="local", model_name=model,
                                 temperature=cfg.models["attacker_temperature"])
    judge = OpenAIChatTarget(endpoint=base + "/chat/completions", api_key="local", model_name=model)
    target = OpenAIChatTarget(endpoint=endpoint, api_key="local", model_name="horizon-guarded")
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
    rows = []
    for k in kinds:
        for obj in objectives:
            res = await makers[k]().execute_async(objective=obj)
            rows.append({"attack": k, "objective": obj[:200], "outcome": str(res.outcome),
                         "turns": getattr(res, "executed_turns", None)})
            print(f"  {k}: {str(res.outcome)}", flush=True)
    return rows


def garak_config(cfg, endpoint: str) -> Path:
    conf = {"rest": {"RestGenerator": {
        "name": "horizon-guarded", "uri": endpoint, "method": "post",
        "headers": {"Content-Type": "application/json"},
        "req_template_json_object": {"model": "horizon-guarded",
                                     "messages": [{"role": "user", "content": "$INPUT"}]},
        "response_json": True, "response_json_field": "$.choices[0].message.content"}}}
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "garak_rest.json"
    p.write_text(json.dumps(conf, indent=1))
    return p


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--pyrit", nargs="*", choices=["pair", "tap", "crescendo"])
    ap.add_argument("--garak", action="store_true")
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
            rows = asyncio.run(_pyrit(cfg, args.endpoint, objs, args.pyrit or ["pair", "tap", "crescendo"]))
            pd.DataFrame(rows).to_csv(OUT / "pyrit.csv", index=False)
            log_examples(rows, "pyrit")
        if args.garak:
            p = garak_config(cfg, args.endpoint)
            print(f"uv run python -m garak --model_type rest --generator_option_file {p} "
                  "--probes dan,promptinject,encoding,latentinjection")


if __name__ == "__main__":
    main()
