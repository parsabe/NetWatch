"""Secret keys. Every key is drawn from the OS CSPRNG via the `secrets` module.

Never use `random` here: the security argument (Slides 10, 13) assumes κ is
uniform on K_τ and unpredictable to the attacker.
"""
from __future__ import annotations

import secrets

_SYS = secrets.SystemRandom()


def choice(seq):
    return secrets.choice(seq)


def randint(lo: int, hi: int) -> int:
    """Uniform integer in [lo, hi] (inclusive)."""
    return lo + secrets.randbelow(hi - lo + 1)


def permutation(seq: str) -> str:
    items = list(seq)
    _SYS.shuffle(items)
    return "".join(items)


def marker(pool: str, k: int) -> str:
    return "".join(secrets.choice(pool) for _ in range(k))


def token_hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def weighted_choice(names: list[str], probs: list[float]) -> str:
    """Draw τ ~ π with CSPRNG randomness (π* sampling at runtime, Slide 34)."""
    total = sum(probs)
    resolution = 1 << 53
    r = secrets.randbelow(resolution) / resolution * total
    acc = 0.0
    for name, p in zip(names, probs):
        acc += p
        if r < acc:
            return name
    return names[-1]
