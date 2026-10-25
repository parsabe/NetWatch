"""Quantitative-information-flow leakage of a configuration (Slide 41; Smith 2009).

Each Q-LLM output is a value of a typed schema S, so it leaks at most log2|S| bits
(min-entropy leakage ≤ log2 of the number of possible outputs). A configuration
leaks Σ log2|S| over the Q-LLM outputs a program can produce, plus a side-channel
term that is a PLACEHOLDER (default 0) and flagged in every report.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from horizon.stage2.schemas import schema_log2_size

SIDE_CHANNEL_FLAG = "side-channel leakage term is a placeholder (not measured)"


@dataclass
class Leakage:
    schema_bits: float
    side_channel_bits: float
    flag: str = SIDE_CHANNEL_FLAG

    @property
    def total(self) -> float:
        return self.schema_bits + self.side_channel_bits


def config_leakage(schemas: list[type[BaseModel]], parser: str, stage2_cfg: dict) -> Leakage:
    """Σ_outputs log2|S| (+ side-channel placeholder) for one configuration's parser mode."""
    bits = sum(schema_log2_size(s, parser, stage2_cfg) for s in schemas)
    return Leakage(bits, float(stage2_cfg["side_channel_bits"]))


def within_cap(leak: Leakage, leakage_cap_bits: float) -> bool:
    return leak.total <= leakage_cap_bits
