"""Typed values with capabilities (Slide 37).

v = (data, type, cap),  cap ∈ {USER, SYSTEM, UNTRUSTED}.

Capability lattice (most restrictive = top): SYSTEM ⊑ USER ⊑ UNTRUSTED.
A derived value inherits the most restrictive label (the join) of ALL its
inputs: data dependencies and control-flow dependencies (the labels of the
branch conditions under which it was computed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Iterable


class Cap(IntEnum):
    SYSTEM = 0
    USER = 1
    UNTRUSTED = 2


def join(caps: Iterable[Cap]) -> Cap:
    """Most restrictive label of a set of labels (lattice join)."""
    out = Cap.SYSTEM
    for c in caps:
        out = max(out, c)
    return Cap(out)


class ParseError(ValueError):
    """Raised when data does not match its declared type (Slide 37: the parser rejects it)."""


@dataclass(frozen=True)
class TypedValue:
    data: Any
    type: str
    cap: Cap
    # Score attached by the majority vote (agreement fraction); None = not voted.
    agreement: float | None = field(default=None, compare=False)


def derive(fn: Callable, *inputs: TypedValue, control: Iterable[TypedValue] = (), type_: str = "derived") -> TypedValue:
    """Compute fn(inputs) and label it with join(data caps ∪ control caps) (Slide 37)."""
    data = fn(*(v.data for v in inputs))
    caps = [v.cap for v in inputs] + [c.cap for c in control]
    agreements = [v.agreement for v in (*inputs, *control) if v.agreement is not None]
    return TypedValue(data, type_, join(caps), min(agreements) if agreements else None)
