"""Key generation and keyspace computation for Stage 1 spotlighting defenses.

Cites: Slide 14, Slide 23.
Ground Rules:
- All secret keys generated using CSPRNG (`secrets` module).
- Large numbers (|Sigma|!, 26!) handled in log-space via `math.lgamma`.
- No numeric parameters hard-coded; all passed via function arguments or config.
"""
import math
import secrets
from typing import Any, Dict, Tuple


def get_keyspace_log_size(variant: str, dyn_n: int = 12, dyn_k: int = 4, scramble_alphabet_len: int = 36) -> float:
    """Calculate natural log of keyspace size ln(|K_tau|).

    Cites: Slide 14.
    """
    if variant in ("none", "delimit", "datamark", "rot13", "base64"):
        return 0.0  # ln(1) = 0
    elif variant == "caesar":
        return math.log(25.0)  # shifts 1..25
    elif variant == "datamark_dyn":
        return dyn_k * math.log(float(dyn_n))
    elif variant in ("substitution", "scramble"):
        # ln(n!) = lgamma(n + 1)
        return math.lgamma(scramble_alphabet_len + 1)
    else:
        raise ValueError(f"Unknown spotlighting variant: {variant}")


def get_keyspace_size(variant: str, dyn_n: int = 12, dyn_k: int = 4, scramble_alphabet_len: int = 36) -> float:
    """Return keyspace size |K_tau| as float (may be inf or large float).

    Cites: Slide 14.
    """
    log_size = get_keyspace_log_size(variant, dyn_n, dyn_k, scramble_alphabet_len)
    try:
        return math.exp(log_size)
    except OverflowError:
        return float("inf")


def generate_key(
    variant: str,
    dyn_marker_alphabet: str = "#&@!%*~+=?$|",
    dyn_marker_k: int = 4,
    scramble_alphabet: str = "abcdefghijklmnopqrstuvwxyz0123456789",
) -> Any:
    """Generate a fresh secret key kappa ~ Unif(K_tau) using CSPRNG.

    Cites: Slide 11, Slide 14.
    """
    if variant in ("none", "delimit", "datamark", "rot13", "base64"):
        return None
    elif variant == "caesar":
        # Shift 1 to 25 uniformly using secrets
        return secrets.randbelow(25) + 1
    elif variant == "datamark_dyn":
        # Select k characters uniformly from alphabet of N choices with replacement
        chars = [secrets.choice(dyn_marker_alphabet) for _ in range(dyn_marker_k)]
        return "".join(chars)
    elif variant in ("substitution", "scramble"):
        # Uniform random permutation of scramble_alphabet via Fisher-Yates using secrets
        chars = list(scramble_alphabet)
        n = len(chars)
        for i in range(n - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            chars[i], chars[j] = chars[j], chars[i]
        # Mapping from original -> scrambled
        forward_map = dict(zip(scramble_alphabet, chars))
        inverse_map = dict(zip(chars, scramble_alphabet))
        return {"forward": forward_map, "inverse": inverse_map, "scrambled_alphabet": "".join(chars)}
    else:
        raise ValueError(f"Unknown spotlighting variant: {variant}")
