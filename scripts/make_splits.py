"""Build the Stage 1 corpus and the fixed fit / report splits (saved to disk, §3).

fit split    → choose τ*, π*, π*_Horizon, λ̂
report split → compute and report R̄, V1, V_Horizon, conformal risk
Never report a value computed on the split used to choose it.

    uv run python scripts/make_splits.py [--set splits.fit_fraction=0.5]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402
from horizon.stage1 import corpus  # noqa: E402

OUT = ROOT / "data" / "processed"
SPLITS = ROOT / "data" / "splits"


def main() -> None:
    cfg = config_from_args(add_config_args(argparse.ArgumentParser()).parse_args())
    data = corpus.build(ROOT / "data" / "raw", cfg.stage1, cfg.splits["fit_fraction"], cfg.run["seed"])
    OUT.mkdir(parents=True, exist_ok=True)
    SPLITS.mkdir(parents=True, exist_ok=True)
    summary = {"seed": cfg.run["seed"], "fit_fraction": cfg.splits["fit_fraction"], "splits": {}}
    for split, parts in data.items():
        for kind, rows in parts.items():
            with open(OUT / f"stage1_{split}_{kind}.jsonl", "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        summary["splits"][split] = {
            "clean": len(parts["clean"]), "attacks": len(parts["attacks"]),
            "attack_sources": dict(collections.Counter(a["source"] for a in parts["attacks"])),
            "attack_categories": len({a["category"] for a in parts["attacks"]}),
            "attack_ids": [a["id"] for a in parts["attacks"]],
            "clean_ids": [c["id"] for c in parts["clean"]],
        }
    (SPLITS / "splits.json").write_text(json.dumps(summary, indent=1))
    short = {s: {k: v for k, v in d.items() if not k.endswith("_ids")} for s, d in summary["splits"].items()}
    print(json.dumps(short, indent=1))
    from scripts.download_data import CARD_JSON, write_card
    if CARD_JSON.exists():
        write_card(json.loads(CARD_JSON.read_text()))      # refresh data/README.md with the split summary


if __name__ == "__main__":
    main()
