"""Utility u(M(x_κ(∅)), y*) ∈ [0,1] (Slide 12). Author decision: SQuAD-style token F1."""
from __future__ import annotations

import re
import string
from collections import Counter

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)


def _normalize(s: str) -> list[str]:
    s = s.lower().translate(_PUNCT)
    return _ARTICLES.sub(" ", s).split()


def token_f1(prediction: str, reference: str) -> float:
    """SQuAD token-level F1 between the model answer and the reference answer y*."""
    p, r = _normalize(prediction), _normalize(reference)
    if not p and not r:
        return 1.0
    if not p or not r:
        return 0.0
    common = sum((Counter(p) & Counter(r)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(r)
    return 2 * precision * recall / (precision + recall)


METRICS = {"token_f1": token_f1}


def utility(prediction: str, reference: str, metric: str) -> float:
    """U_τ is the mean of this over clean documents (Slide 12)."""
    return METRICS[metric](prediction, reference)
