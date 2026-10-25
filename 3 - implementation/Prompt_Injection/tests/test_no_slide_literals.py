"""§9: no numeric literal from the slides exists outside tests/ (grep check).

Scans every Python file in horizon/, scripts/ and eval/ with the tokenizer, so
literals inside strings/comments are ignored but every numeric token is caught.
Parameter values live in config/params.yaml (the designated place, §7).
"""
import io
import tokenize
from pathlib import Path

from horizon.config import ROOT

# Illustrative numbers on the slides (Slides 13, 14, 16, 19, 22, 23, 36, 39).
SLIDE_LITERALS = {0.029, 0.104, 0.147, 0.15, 0.35, 0.294, 0.11, 2.5e-24, 0.05, 0.01, 0.1, 0.02,
                  20, 26, 60, 100, 500, 1000, 10000}
DIRS = ["horizon", "scripts", "eval"]


def _numbers(path: Path):
    toks = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    for t in toks:
        if t.type == tokenize.NUMBER:
            try:
                yield t.start[0], float(t.string.replace("_", "")), t.string
            except ValueError:
                pass


def test_no_slide_literals_outside_tests():
    hits = []
    for d in DIRS:
        for p in (ROOT / d).rglob("*.py"):
            for line, val, s in _numbers(p):
                if any(abs(val - lit) <= 1e-9 * abs(lit) for lit in SLIDE_LITERALS):
                    hits.append(f"{p.relative_to(ROOT)}:{line}: {s}")
    assert not hits, "slide literals outside tests/:\n" + "\n".join(hits)
