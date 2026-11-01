"""Configuration: config/params.yaml + CLI overrides (`--set a.b=value`).

Nothing numeric is hard-coded in the library; every function receives its
parameters from the object returned by `load_config`.
"""
from __future__ import annotations

import argparse
import copy
import os
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PARAMS = ROOT / "config" / "params.yaml"


class Flags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_firewall: bool
    use_stage1: bool
    stage1_variant: str | None
    stage1_mix: str | None
    use_stage2: bool
    m: int
    use_vote: bool
    use_typing: bool
    use_policy: bool
    use_conformal: bool
    use_output_guard: bool
    use_canary: bool


class Config(BaseModel):
    """Thin typed wrapper; sections stay plain dicts so new keys need no code change."""
    model_config = ConfigDict(extra="allow")
    run: dict[str, Any]
    models: dict[str, Any]
    flags: Flags
    stage1: dict[str, Any]
    stage2: dict[str, Any]
    splits: dict[str, Any]
    direct: dict[str, Any]
    theory: dict[str, Any]

    # ---- derived grids -------------------------------------------------------
    def rho_grid(self) -> np.ndarray:
        g = self.stage1["rho_grid"]
        pts = np.logspace(g["log_min"], g["log_max"], g["num"])
        return np.concatenate([[0.0], pts]) if g.get("include_zero") else pts

    def q_grid(self) -> np.ndarray:
        g = self.stage1["q_list"]
        return np.logspace(g["log10_min"], g["log10_max"], g["num"])

    def lambda_grid(self) -> np.ndarray:
        return np.linspace(0.0, 1.0, self.stage2["lambda_grid_num"])

    def flat(self) -> dict[str, str]:
        """Flattened view for MLflow params."""
        out: dict[str, str] = {}

        def rec(prefix: str, obj: Any) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    rec(f"{prefix}.{k}" if prefix else str(k), v)
            else:
                out[prefix] = json.dumps(obj) if not isinstance(obj, str) else obj

        rec("", self.model_dump())
        return out


def _set_dotted(d: dict, dotted: str, raw: str) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        if k not in cur:
            raise KeyError(f"unknown config section '{k}' in '{dotted}'")
        cur = cur[k]
    if keys[-1] not in cur:
        raise KeyError(f"unknown config key '{dotted}'")
    cur[keys[-1]] = yaml.safe_load(raw)


AUTOTUNED = ROOT / "config" / "autotuned.yaml"     # upgrades adopted by eval/autotune.py (delete to undo)
LOCAL = ROOT / "config" / "local.yaml"             # this copy's own settings (e.g. another model; cluster/launch_variant.sh)


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        base[k] = _merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def load_config(path: str | Path | None = None, overrides: list[str] | None = None) -> Config:
    """params.yaml, then config/autotuned.yaml (adopted upgrades), then config/local.yaml (this copy's own settings) —
    both for the default config only — then --set overrides."""
    raw = yaml.safe_load(Path(path or DEFAULT_PARAMS).read_text())
    raw = copy.deepcopy(raw)
    if (path is None or Path(path).resolve() == DEFAULT_PARAMS.resolve()) and AUTOTUNED.exists() \
            and not os.environ.get("HORIZON_NO_AUTOTUNED"):
        raw = _merge(raw, yaml.safe_load(AUTOTUNED.read_text()) or {})
    if (path is None or Path(path).resolve() == DEFAULT_PARAMS.resolve()) and LOCAL.exists() \
            and not os.environ.get("HORIZON_NO_LOCAL"):
        raw = _merge(raw, yaml.safe_load(LOCAL.read_text()) or {})
    for ov in overrides or []:
        key, _, val = ov.partition("=")
        _set_dotted(raw, key.strip(), val)
    return Config(**raw)


def add_config_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--config", default=str(DEFAULT_PARAMS))
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override any params.yaml key, e.g. --set stage1.alpha=0.01")
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    return load_config(args.config, args.set)
