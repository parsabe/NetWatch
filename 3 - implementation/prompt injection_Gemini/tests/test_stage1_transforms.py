"""Unit tests for Stage 1 transforms, inverses, and keys.

Cites: Slide 11, Slide 14, Slide 23; §9 Ground Rules.
"""
import pytest
from src.stage1.keys import generate_key, get_keyspace_log_size, get_keyspace_size
from src.stage1.transforms import TRANSFORMS, build_spotlighted_prompt


def test_transforms_determinism_given_key():
    """Test that each transform is deterministic given key kappa, and invertible where applicable."""
    doc = "Conference meeting at 10am in room 402 with Alice and Bob."

    for name, spec in TRANSFORMS.items():
        if spec["requires_key"]:
            key1 = generate_key(name)
            # Re-running with same key produces identical output
            t1, note1 = spec["transform"](doc, key1)
            t2, note2 = spec["transform"](doc, key1)
            assert t1 == t2
            assert note1 == note2

            # Invertibility check
            inv = spec["inverse"](t1, key1)
            assert inv == doc
        else:
            t1, note1 = spec["transform"](doc, None)
            t2, note2 = spec["transform"](doc, None)
            assert t1 == t2
            assert note1 == note2
            inv = spec["inverse"](t1, None)
            assert inv == doc


def test_transforms_different_across_fresh_keys():
    """Test that keyed transforms produce distinct outputs across fresh keys drawn from secrets."""
    doc = "Confidential financial quarterly earnings report."
    for name in ("caesar", "datamark_dyn", "substitution"):
        spec = TRANSFORMS[name]
        outputs = set()
        for _ in range(5):
            k = generate_key(name)
            t, _ = spec["transform"](doc, k)
            outputs.add(t)
        # Should produce multiple distinct outputs across different keys
        assert len(outputs) > 1


def test_build_spotlighted_prompt():
    """Test that build_spotlighted_prompt formats S_kappa || T || tau_kappa(D)."""
    task = "Summarize the text."
    doc = "Hello world."
    sys_p, usr_p, key = build_spotlighted_prompt(task, doc, "delimit")
    assert "<<" in usr_p and ">>" in usr_p
    assert "Task:\nSummarize the text." in usr_p
    assert "untrusted" in sys_p.lower()
