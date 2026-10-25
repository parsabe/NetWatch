"""Grep check test: Verify no numeric literal from the slides exists outside tests/.

Cites: Ground Rules §1.3, §9.
"NOTHING numeric is hard-coded. All values on my slides are illustrative; they
appear in code ONLY inside the §9 unit tests."
"- No numeric literal from the slides exists outside tests/ (grep check)"
"""
import re
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"

SLIDE_NUMERIC_LITERALS = [
    r"\b0\.029\b",
    r"\b0\.104\b",
    r"\b0\.147\b",
    r"\b0\.294\b",
    r"\b0\.110\b",
    r"2\.5e-24",
]


def test_no_slide_numeric_literals_in_src():
    """Ensure none of the illustrative numeric slide literals appear in src/."""
    violations = []
    py_files = list(SRC_DIR.rglob("*.py"))
    assert len(py_files) > 0, "Source files must exist in src/."

    for file_path in py_files:
        content = file_path.read_text(encoding="utf-8")
        for pattern in SLIDE_NUMERIC_LITERALS:
            matches = re.findall(pattern, content)
            if matches:
                violations.append((file_path.relative_to(BASE_DIR), pattern, matches))

    assert not violations, f"Found hardcoded slide numeric literals in src/: {violations}"
