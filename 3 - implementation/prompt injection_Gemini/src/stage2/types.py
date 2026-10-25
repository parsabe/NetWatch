"""Typed values and capability propagation for Stage 2.

Cites: Slide 38 (Step 6: typed value + capability v = (data, type, cap)).
Ground Rules:
- Capabilities: USER, SYSTEM, UNTRUSTED.
- Most restrictive capability propagation across both data and control-flow dependencies.
- Parsers reject anything not matching the declared schema.
"""
import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar, Union
from pydantic import BaseModel, ValidationError

T = TypeVar("T")


class Capability(str, Enum):
    """Capability labels for information flow control.

    Ordering: UNTRUSTED is the most restrictive (tainted).
    Cites: Slide 38.
    """
    USER = "USER"
    SYSTEM = "SYSTEM"
    UNTRUSTED = "UNTRUSTED"

    @classmethod
    def combine(cls, *caps: Union["Capability", str]) -> "Capability":
        """Compute the least upper bound (most restrictive label).

        If any dependency is UNTRUSTED, result is UNTRUSTED.
        Otherwise USER/SYSTEM is trusted.
        Cites: Slide 38, Slide 39.
        """
        for c in caps:
            val = c.value if isinstance(c, Capability) else str(c)
            if val == cls.UNTRUSTED.value:
                return cls.UNTRUSTED
        return cls.USER


class TypedValue(Generic[T]):
    """Boxed value containing data, declared type, and capability token.

    v = (data, type, cap).
    Cites: Slide 38.
    """
    def __init__(self, data: T, value_type: str, cap: Capability = Capability.UNTRUSTED):
        self.data = data
        self.value_type = value_type
        self.cap = cap if isinstance(cap, Capability) else Capability(cap)

    def derive(self, new_data: Any, new_type: str, *additional_dependencies: Union[Capability, "TypedValue"]) -> "TypedValue":
        """Derive a new value inheriting the most restrictive capability of all dependencies.

        Cites: Slide 38.
        """
        dep_caps = [self.cap]
        for dep in additional_dependencies:
            if isinstance(dep, TypedValue):
                dep_caps.append(dep.cap)
            elif isinstance(dep, (Capability, str)):
                dep_caps.append(Capability(dep))
        inherited_cap = Capability.combine(*dep_caps)
        return TypedValue(data=new_data, value_type=new_type, cap=inherited_cap)

    def is_untrusted(self) -> bool:
        return self.cap == Capability.UNTRUSTED

    def __repr__(self) -> str:
        return f"TypedValue(data={self.data!r}, type={self.value_type!r}, cap={self.cap.value})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, TypedValue):
            return (self.data == other.data and
                    self.value_type == other.value_type and
                    self.cap == other.cap)
        return False


class SchemaParser:
    """Type parsers that strictly validate and sanitize outputs from the Q-LLM.

    Rejects injections, code, or non-matching structures.
    Cites: Slide 38.
    """
    @staticmethod
    def parse_date(raw: str) -> str:
        """Parse strict ISO date YYYY-MM-DD (rejects any surrounding text/injections)."""
        raw_clean = raw.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_clean):
            raise ValueError(f"Invalid date format: {raw!r}")
        # Verify calendar validity
        datetime.strptime(raw_clean, "%Y-%m-%d")
        return raw_clean

    @staticmethod
    def parse_email(raw: str) -> str:
        """Parse strict email address without any injection characters."""
        raw_clean = raw.strip()
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw_clean):
            raise ValueError(f"Invalid email format: {raw!r}")
        return raw_clean

    @staticmethod
    def parse_int(raw: str) -> int:
        """Parse strict integer without shell characters."""
        raw_clean = raw.strip()
        match = re.search(r"^-?\d+$", raw_clean)
        if not match:
            raise ValueError(f"Invalid integer format: {raw!r}")
        return int(match.group(0))

    @staticmethod
    def parse_bool(raw: str) -> bool:
        """Parse strict boolean value."""
        norm = raw.strip().lower()
        if norm in ("true", "1", "yes"):
            return True
        if norm in ("false", "0", "no"):
            return False
        raise ValueError(f"Invalid boolean format: {raw!r}")

    @staticmethod
    def parse_string(raw: str, max_len: int = 500) -> str:
        """Parse bounded string without newlines if single-line expected."""
        s = raw.strip()
        if len(s) > max_len:
            raise ValueError(f"String exceeds maximum allowed length ({len(s)} > {max_len})")
        return s


PARSERS: Dict[str, Callable[[str], Any]] = {
    "date": SchemaParser.parse_date,
    "email": SchemaParser.parse_email,
    "int": SchemaParser.parse_int,
    "bool": SchemaParser.parse_bool,
    "string": SchemaParser.parse_string,
}
