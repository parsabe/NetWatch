"""Information leakage computation and bounding for typed schemas.

Cites: Slide 41 (Allowed mixes and Sion's theorem; Smith 2009 Quantitative Information Flow).
Formula:
    bits <= sum log2 |S| + side_channel_term
Constraint:
    leakage <= leakage_cap_bits
"""
import math
from typing import Dict, List, Optional


def compute_schema_leakage_bits(
    schema_cardinalities: List[float],
    side_channel_bits: float = 0.0,
) -> float:
    """Calculate upper bound on channel capacity in bits.

    Cites: Slide 41.
    Note: side_channel_bits is currently set to placeholder 0.0 (flagged for future timing/token channel analysis).
    """
    total_bits = 0.0
    for card in schema_cardinalities:
        if card > 1.0:
            total_bits += math.log2(float(card))
    total_bits += side_channel_bits
    return float(total_bits)


def check_leakage_constraint(
    leakage_bits: float,
    leakage_cap_bits: float,
) -> bool:
    """Check if configuration satisfies the information leakage cap.

    Cites: Slide 41.
    """
    return leakage_bits <= leakage_cap_bits
