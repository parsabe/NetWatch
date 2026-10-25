"""MLflow logging: config, seed, model, split and per-example outcomes for every run."""
from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path

import pandas as pd

from horizon.config import ROOT, Config

MAX_PARAM_CHARS = 250


@contextlib.contextmanager
def mlflow_run(cfg: Config, run_name: str, tags: dict | None = None):
    import mlflow
    db = (ROOT / cfg.run["tracking_uri"]).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{db}")
    name = cfg.run["experiment_name"]
    if mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(name, artifact_location=(ROOT / cfg.run["artifact_dir"]).resolve().as_uri())
    mlflow.set_experiment(name)
    with mlflow.start_run(run_name=run_name) as run:
        flat = cfg.flat()
        # MLflow caps param length; the full config is logged as the config.json artifact.
        mlflow.log_params({k: v[:MAX_PARAM_CHARS] for k, v in flat.items()})
        mlflow.set_tags({"seed": cfg.run["seed"], "split": cfg.run["split"],
                         "p_llm": cfg.models["p_llm"], "q_llm": cfg.models["q_llm"],
                         **(tags or {})})
        mlflow.log_dict(cfg.model_dump(), "config.json")
        yield run


def log_examples(rows: list[dict], name: str) -> None:
    """Per-example outcomes as a CSV artifact."""
    import mlflow
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"{name}.csv"
        pd.DataFrame(rows).to_csv(p, index=False)
        mlflow.log_artifact(str(p))


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))
