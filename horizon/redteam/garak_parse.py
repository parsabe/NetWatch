"""Parsing garak reports into per-probe results (no model, no garak import: used by the runner AND the report).

A hit is a garak output its detector flagged as a successful attack. Two kinds of hits are not jailbreaks and are
taken out of `fails` (kept visible as `excluded_hits`): the guarded endpoint's own refusal — garak's MitigationBypass
does not know Horizon's wording, which made the 1–2 Nov reports say 40 % guarded ASR where the truth was 0 % — and
empty outputs (an upstream error or a looping generation: no content, nothing got through).
"""
from __future__ import annotations

import json
from pathlib import Path

from horizon.direct.refusal import is_own_refusal


def _hit_text(h: dict) -> str:
    out = h.get("output")
    if isinstance(out, dict):
        return str(out.get("text") or "")
    if isinstance(out, str) and out.startswith("{") and "'text':" in out:      # older garak: repr of the dict
        import ast
        try:
            return str(ast.literal_eval(out).get("text") or "")
        except (ValueError, SyntaxError):
            return out
    return str(out or "")


def parse_garak(report: Path) -> list[dict]:
    """garak 'eval' records: per probe × detector, fails = outputs where the attack succeeded.

    Hits that are NOT jailbreaks are taken out of `fails` and counted in `excluded_hits`: the guarded endpoint's own
    refusal (garak's MitigationBypass does not know Horizon's wording — the cause of the false 40 % guarded ASR on
    1–2 Nov) and empty outputs (an upstream error / a looping generation: no content, so nothing got through)."""
    excluded: dict[tuple[str, str], int] = {}
    hitlog = report.with_name(report.name.replace(".report.jsonl", ".hitlog.jsonl"))
    if hitlog.exists():
        for line in hitlog.read_text().splitlines():
            if not line.strip():
                continue
            h = json.loads(line)
            text = _hit_text(h)
            if is_own_refusal(text) or not text.strip():
                k = (h.get("probe", ""), h.get("detector", ""))
                excluded[k] = excluded.get(k, 0) + 1
    rows = []
    for line in report.read_text().splitlines():
        r = json.loads(line)
        if r.get("entry_type") == "eval":
            ex = min(excluded.get((r["probe"], r["detector"]), 0), int(r["fails"]))
            rows.append({"probe": r["probe"], "detector": r["detector"], "fails": int(r["fails"]) - ex,
                         "n": r["total_evaluated"], "excluded_hits": ex})
    return rows


def finished(report: Path) -> bool:
    return report.exists() and '"entry_type": "completion"' in report.read_text()


def reparse_dir(garak_dir: Path) -> list[dict]:
    """Every FINISHED report in a garak results folder (file name <target>_<family>_<probe>.report.jsonl)."""
    rows = []
    for rep in sorted(garak_dir.glob("*.report.jsonl")):
        if not finished(rep):
            continue
        target, family, _ = rep.name.removesuffix(".report.jsonl").split("_", 2)
        rows += [{"target": target, "family": family, **r} for r in parse_garak(rep)]
    return rows
