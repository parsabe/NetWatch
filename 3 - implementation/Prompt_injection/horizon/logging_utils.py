"""MLflow logging: config, seed, model, split and per-example outcomes for every run.

Logging must never decide whether an experiment succeeds. The first Stage 1 / detect attempts died with
`sqlite3.OperationalError: duplicate column name: step` when four campaign workers opened a brand-new MLflow SQLite
database in the same second and raced each other's schema migration. So: retry with a random back-off (the other
process finishes the migration meanwhile); if the database still cannot be opened, track in a plain file store
(no schema, nothing to migrate); and if even that fails, carry on without tracking (the CSV/JSON results that the
reports are built from are written by the runners themselves, not by MLflow).
"""
from __future__ import annotations

import contextlib
import json
import random
import tempfile
import time
from pathlib import Path

import pandas as pd

from horizon.config import ROOT, Config

MAX_PARAM_CHARS = 250
OPEN_ATTEMPTS = 3
_TRACKED = {"on": True}          # False once tracking had to be switched off for this process


def _open(cfg: Config, run_name: str, tags: dict | None, tracking_uri: str):
    import mlflow
    mlflow.set_tracking_uri(tracking_uri)
    name = cfg.run["experiment_name"]
    if mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(name, artifact_location=(ROOT / cfg.run["artifact_dir"]).resolve().as_uri())
    mlflow.set_experiment(name)
    run = mlflow.start_run(run_name=run_name)
    try:
        flat = cfg.flat()
        # MLflow caps param length; the full config is logged as the config.json artifact.
        mlflow.log_params({k: v[:MAX_PARAM_CHARS] for k, v in flat.items()})
        mlflow.set_tags({"seed": cfg.run["seed"], "split": cfg.run["split"],
                         "p_llm": cfg.models["p_llm"], "q_llm": cfg.models["q_llm"], **(tags or {})})
        mlflow.log_dict(cfg.model_dump(), "config.json")
    except Exception:
        mlflow.end_run("FAILED")
        raise
    return run


def _disable_tracking() -> None:
    """Make every later mlflow.log_* call in this process a no-op (the runners call them directly)."""
    import mlflow
    _TRACKED["on"] = False
    for fn in ("log_params", "log_param", "log_metrics", "log_metric", "log_dict", "log_artifact", "set_tags",
               "set_tag", "log_text"):
        setattr(mlflow, fn, lambda *a, **k: None)


@contextlib.contextmanager
def mlflow_run(cfg: Config, run_name: str, tags: dict | None = None):
    import mlflow
    db = (ROOT / cfg.run["tracking_uri"]).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    fallback = (ROOT / "results" / "mlruns_fallback")
    fallback.mkdir(parents=True, exist_ok=True)
    uris = [f"sqlite:///{db}"] * OPEN_ATTEMPTS + [fallback.resolve().as_uri()]
    run = None
    for attempt, uri in enumerate(uris):
        try:
            run = _open(cfg, run_name, tags, uri)
            break
        except Exception as e:
            print(f"[mlflow] cannot track in {uri} ({type(e).__name__}: {str(e)[:140]})"
                  f"{'; retrying' if attempt < len(uris) - 1 else '; tracking switched off'}", flush=True)
            time.sleep(random.uniform(2.0, 6.0) * (attempt + 1) if attempt < OPEN_ATTEMPTS else 0)
    if run is None:
        _disable_tracking()
        yield None
        return
    try:
        yield run
    except BaseException:
        with contextlib.suppress(Exception):
            mlflow.end_run("FAILED")
        raise
    else:
        with contextlib.suppress(Exception):
            mlflow.end_run()


def log_examples(rows: list[dict], name: str) -> None:
    """Per-example outcomes as a CSV artifact (best effort: a logging problem never fails a phase)."""
    if not _TRACKED["on"]:
        return
    try:
        import mlflow
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"{name}.csv"
            pd.DataFrame(rows).to_csv(p, index=False)
            mlflow.log_artifact(str(p))
    except Exception as e:
        print(f"[mlflow] could not log {name}: {type(e).__name__}: {str(e)[:120]}", flush=True)


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))
