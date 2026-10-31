"""Campaign progress at a glance: every phase, its lane, its status and tests done / planned; optionally to Telegram.

    uv run --no-sync python scripts/progress.py            # print (login node, any time)
    uv run --no-sync python scripts/progress.py --send     # also send it to Telegram (the cluster job does this hourly)

Read-only: counts rows in the result files the runners append to (never parses half-written lines, never merges
anything), so it is safe to run while the campaign writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
from horizon.config import ROOT, load_config  # noqa: E402

RES = ROOT / "results"
ICON = {"ok": "✅", "running": "⏳", "failed": "❌", "skipped": "⏭", "paused": "⏸", "pending": "⬜"}


def rows(p: Path, keys: list[str] | None = None) -> int:
    """Distinct cases in a result CSV (0 if missing). Parsed as CSV — replies contain newlines, so counting lines
    over-counts — and de-duplicated on the case key, because a resumed runner may have written a case twice."""
    if not p.exists() or not p.stat().st_size:
        return 0
    try:
        import pandas as pd
        df = pd.read_csv(p, on_bad_lines="skip", engine="python")      # the row being written right now is skipped
    except Exception:
        return 0
    keys = [k for k in (keys or []) if k in df.columns]
    return int(len(df.drop_duplicates(keys)) if keys else len(df))


def stage1_dir(split: str) -> Path:
    for name in (f"{split}_pilot", split):                     # the bounded profile runs Stage 1 with --pilot
        if (RES / "stage1" / name).exists():
            return RES / "stage1" / name
    return RES / "stage1" / f"{split}_pilot"


def done(phase: str) -> int:
    t, s2 = RES / "testing", RES / "stage2"
    if phase in ("stage1_fit", "stage1_report"):
        d = stage1_dir(phase.split("_")[1])
        return (rows(d / "matrix.csv", ["attack_id", "variant"]) + rows(d / "clean.csv", ["doc_id", "variant"])
                + rows(d / "adaptive_eval.csv", ["target_id", "variant", "eval_idx"]))
    if phase == "detect":
        return rows(t / "direct_items.csv", ["detector", "set", "idx"])
    if phase == "calibrate":
        return rows(s2 / "calibration" / "episodes.csv", ["config", "attack_id"])
    if phase.startswith("ablation_"):
        return rows(s2 / f"report-{phase.split('_', 1)[1]}" / "episodes.csv", ["config", "attack_id"])
    if phase.startswith("xpia_"):
        return rows(t / f"xpia_items__{phase.split('_', 1)[1]}.csv", ["round", "config", "converter", "attack_id"])
    if phase == "pyrit":
        return rows(t / "pyrit.csv", ["attack", "objective"])
    if phase == "agent_adaptive":
        p = s2 / "report-full-adaptive" / "adaptive_trace.csv"
        if not p.exists():
            return 0
        try:
            import pandas as pd
            return int(pd.read_csv(p).groupby(["suite", "user_task", "injection_task"]).ngroups)
        except Exception:
            return 0
    if phase == "garak":                       # a test = one output; status 1 = generated (live), 2 = evaluated (dup)
        n = 0
        for rep in (t / "garak").glob("*.report.jsonl"):
            for line in rep.read_text(errors="replace").splitlines():
                if '"entry_type": "attempt"' in line and '"status": 1' in line:
                    try:
                        n += len(json.loads(line).get("outputs") or [])
                    except ValueError:                     # the line being written right now
                        pass
        return n
    return 0


def snapshot() -> tuple[str, int, int, int]:
    """(message, done, certain planned, max planned)."""
    import campaign as C
    base = load_config()
    states = {p.stem: json.loads(p.read_text()) for p in (RES / "campaign").glob("state*.json")} \
        if (RES / "campaign").exists() else {}
    profile = next((s.get("profile") for s in states.values() if s.get("profile")), base.campaign["profile"])
    prof = C.profile_of(base, profile)
    cfg = load_config(None, prof["set"])
    plan = C.build_plan(cfg, prof, [])
    view = C.merged_state(next(iter(states.values()), {"phases": {}}), cfg) if states else {"phases": {}}
    lanes = cfg.campaign.get("lanes") or {}
    lane_of = {p: ln for ln, ps in lanes.items() for p in ps}
    started = min((s.get("started") for s in states.values() if s.get("started")), default=None)
    firm, upper = C.budget(plan)
    total, lines = 0, []
    for ln in sorted({lane_of.get(p.name, "-") for p in plan}):
        lines.append(f"\nLane {ln}:" if ln != "-" else "\n(no lane):")
        for p in sorted((q for q in plan if lane_of.get(q.name, "-") == ln), key=lambda q: C.ORDER.index(q.name)):
            n = done(p.name)
            total += n
            st = view["phases"].get(p.name, {}).get("status", "pending")
            plan_s = f"{p.planned:,}" if p.planned is not None else "?"
            lines.append(f"{ICON.get(st, '•')} {p.name}: {n:,} / {plan_s}" + (" (max)" if p.planned_min == 0 else ""))
    since = ""
    if started:
        h = (datetime.now() - datetime.fromisoformat(started)).total_seconds() / 3600
        since = f" · {h:.1f} h since start"
    head = (f"📊 Progress ({profile}){since}\n"
            f"Tests done: {total:,} of {firm:,}–{upper:,} ({100 * total / max(1, firm):.0f}%) · "
            f"allowed {prof['min_tests']:,}–{prof['max_tests']:,}" if prof.get("max_tests") else
            f"📊 Progress ({profile}){since}\nTests done: {total:,} of ≥ {firm:,}")
    return head + "\n" + "\n".join(lines), total, firm, upper


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="also send to Telegram")
    args = ap.parse_args()
    text = snapshot()[0]
    print(text)
    if args.send:
        from horizon.notify import send
        return 0 if send(text) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
