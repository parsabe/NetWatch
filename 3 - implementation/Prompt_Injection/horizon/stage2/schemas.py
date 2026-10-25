"""Typed Q-LLM output schemas: parsing, canonicalization, and size |S| (Slides 37, 41).

* parse(): reject anything that does not match the declared type (Slide 37).
  Two parser modes (a component of θ, Slide 40):
    strict — every string must match STRICT_CHARS and be ≤ strict_max_chars
    loose  — every string must be printable and ≤ loose_max_chars
* log2_size(): log2 |S| of the schema's output space under the parser mode,
  the per-output leakage cap of the QIF argument (Slide 41, Smith 2009).
* canonical(): normalized form used by the majority vote (Slide 35).
"""
from __future__ import annotations

import datetime as dt
import enum
import json
import math
import re
import string
import types
import typing

from pydantic import BaseModel, ValidationError

from horizon.stage2.values import ParseError

STRICT_CHARS = string.ascii_letters + string.digits + " .,@_+-:/"
LOOSE_CHARS = "".join(c for c in string.printable if c not in "\t\n\r\x0b\x0c")
_STRICT_RE = re.compile(f"[{re.escape(STRICT_CHARS)}]*")


def _log2_sum_powers(log2_base: float, max_n: int) -> float:
    """log2 Σ_{l=0}^{max_n} B^l, computed in log-space."""
    terms = [l * log2_base for l in range(max_n + 1)]
    top = max(terms)
    return top + math.log2(sum(2.0 ** (t - top) for t in terms))


def _field_bits(tp, mode: str, cfg: dict) -> float:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        bits = [_field_bits(a, mode, cfg) for a in non_none]
        extra = 1 if len(non_none) < len(args) else 0
        top = max(bits)
        return top + math.log2(sum(2.0 ** (b - top) for b in bits) + extra * 2.0 ** (-top))
    if origin is typing.Literal:
        return math.log2(len(args))
    if origin in (list, tuple, set, frozenset):
        inner = _field_bits(args[0], mode, cfg) if args else _field_bits(str, mode, cfg)
        return _log2_sum_powers(inner, cfg["max_list_len"])
    if isinstance(tp, type):
        if issubclass(tp, BaseModel):
            return schema_log2_size(tp, mode, cfg)
        if issubclass(tp, bool):
            return 1.0
        if issubclass(tp, enum.Enum):
            return math.log2(len(tp))
        if issubclass(tp, int):
            return float(cfg["int_bits"])
        if issubclass(tp, float):
            return float(cfg["float_bits"])
        if issubclass(tp, (dt.datetime, dt.date, dt.time, dt.timedelta)):
            return float(cfg["datetime_bits"])
    # str, EmailStr and anything string-like
    if mode == "strict":
        return _log2_sum_powers(math.log2(len(STRICT_CHARS)), cfg["strict_max_chars"])
    return _log2_sum_powers(math.log2(len(LOOSE_CHARS)), cfg["loose_max_chars"])


def schema_log2_size(model: type[BaseModel], mode: str, cfg: dict) -> float:
    """log2 |S| of a schema = Σ over fields (Slide 41: bits ≤ log2|S|)."""
    return sum(_field_bits(f.annotation, mode, cfg) for f in model.model_fields.values())


def _check_strings(obj, mode: str, cfg: dict) -> None:
    if isinstance(obj, str):
        if mode == "strict":
            if len(obj) > cfg["strict_max_chars"] or not _STRICT_RE.fullmatch(obj):
                raise ParseError(f"string rejected by strict parser: {obj[:40]!r}")
        else:
            if len(obj) > cfg["loose_max_chars"] or any(c not in LOOSE_CHARS for c in obj):
                raise ParseError(f"string rejected by loose parser: {obj[:40]!r}")
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_strings(v, mode, cfg)
    elif isinstance(obj, (list, tuple)):
        if len(obj) > cfg["max_list_len"]:
            raise ParseError("list longer than max_list_len")
        for v in obj:
            _check_strings(v, mode, cfg)


def extract_json(raw: str) -> str:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ParseError("no JSON object in output")
    return raw[start:end + 1]


def parse(raw: str, model: type[BaseModel], mode: str, cfg: dict) -> BaseModel:
    """Slide 37: the parser accepts only outputs matching the declared type."""
    try:
        obj = model.model_validate_json(extract_json(raw))
    except (ValidationError, ValueError) as e:
        raise ParseError(str(e)[:200]) from None
    _check_strings(obj.model_dump(mode="json"), mode, cfg)
    return obj


def _norm(obj):
    if isinstance(obj, str):
        return " ".join(obj.split()).casefold()
    if isinstance(obj, dict):
        return {k: _norm(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(v) for v in obj]
    return obj


def canonical(obj: BaseModel | str) -> str:
    """Canonical form for voting: whitespace-collapsed, case-folded, key-sorted JSON."""
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    return json.dumps(_norm(data), sort_keys=True)
