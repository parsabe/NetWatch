"""Quantitative-information-flow leakage of a configuration (Slide 41; Smith 2009).

Each Q-LLM output is a value of a typed schema S, so it leaks at most log2|S| bits
(min-entropy leakage ≤ log2 of the number of possible outputs). A configuration
leaks Σ log2|S| over the Q-LLM outputs a program can produce, plus a side-channel
term that is a PLACEHOLDER (default 0) and flagged in every report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import BaseModel

from horizon.stage2.schemas import schema_log2_size

SIDE_CHANNEL_FLAG = "side-channel leakage term is a placeholder (not measured)"
TRACE_FLAG = "side-channel term = upper bound from the policy-decision trace (timing channels not covered)"


@dataclass
class Leakage:
    schema_bits: float
    side_channel_bits: float
    flag: str = SIDE_CHANNEL_FLAG

    @property
    def total(self) -> float:
        return self.schema_bits + self.side_channel_bits


def trace_bits(max_decisions: int, outcomes: int) -> float:
    """Side channel through the observable decision trace: at most `max_decisions` policy decisions, each one of
    `outcomes` values (ALLOW / CONFIRM / DENY) or absent → log2 Σ_{k≤D} outcomes^k ≤ D·log2(outcomes + 1) bits."""
    return float(max_decisions * math.log2(outcomes + 1))


def side_channel_bits(stage2_cfg: dict) -> float:
    """`side_channel_mode: trace` → trace_bits(...); otherwise the fixed placeholder `side_channel_bits`."""
    if stage2_cfg.get("side_channel_mode") == "trace":
        return trace_bits(int(stage2_cfg["side_channel_max_decisions"]), int(stage2_cfg["side_channel_outcomes"]))
    return float(stage2_cfg["side_channel_bits"])


def config_leakage(schemas: list[type[BaseModel]], parser: str, stage2_cfg: dict) -> Leakage:
    """Σ_outputs log2|S| + side-channel term for one configuration's parser mode."""
    bits = sum(schema_log2_size(s, parser, stage2_cfg) for s in schemas)
    sc = side_channel_bits(stage2_cfg)
    flag = SIDE_CHANNEL_FLAG if stage2_cfg.get("side_channel_mode") != "trace" else TRACE_FLAG
    return Leakage(bits, sc, flag)


def within_cap(leak: Leakage, leakage_cap_bits: float) -> bool:
    return leak.total <= leakage_cap_bits
