"""Campaign (§11): every Horizon experiment in one resumable run, then a process report.

    uv run python eval/campaign.py --plan                 # what will run: phases, test counts, time estimate
    uv run python eval/campaign.py                        # run / continue the campaign, then write the report
    uv run python eval/campaign.py --profile laptop       # the smaller plan (config: campaign.profiles)
    uv run python eval/campaign.py --report-only          # rebuild report/campaign/ from what exists
    uv run python eval/campaign.py --fresh                # archive old results/ first, start from zero

A *test* is one evaluated case: one attack (or one clean task) through one configuration — one Stage 1 document
answer, one AgentDojo episode, one XPIA plant→process, one guard check, one PyRIT attack, one garak attempt.

Phases run in dependency order (Stage 1 fit → π*, calibration → λ̂, then everything that needs them), up to
`workers` at a time; each phase is one existing runner, logged to results/campaign/logs/<phase>.log. Every runner
is resumable, so a killed job (cluster time limit) loses at most the case in flight: run the same command again.
When all phases are done and fewer than campaign.min_tests tests exist, extra XPIA rounds (same attacks, fresh keys
κ) run until the target is reached. The report (report/campaign/) says what ran, in which order, how long it took,
which phases failed and why, how many tests ended with the attack getting through vs. blocked, with
Clopper–Pearson intervals and example cases.

Exit code: 0 complete · 2 finished with failed phases or below min_tests · 3 paused (SIGTERM / SIGINT).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from horizon.config import ROOT, load_config  # noqa: E402
from horizon.llm import server_alive  # noqa: E402
from horizon.stage1 import stats  # noqa: E402

RES = ROOT / "results"
CAMP = RES / "campaign"
LOGS = CAMP / "logs"
STATE = CAMP / "state.json"
LEDGER = CAMP / "ledger.jsonl"
OUTDIR = ROOT / "report" / "campaign"
REPAIRS = ROOT / "config" / "repairs.yaml"        # per-phase --set values that made a failed phase pass
UV = os.environ.get("UV", str(Path.home() / ".local" / "bin" / "uv"))
PY = [UV, "run", "--no-sync", "python"]
HOUR = int(timedelta(hours=1).total_seconds())
MINUTE = int(timedelta(minutes=1).total_seconds())
PCT = 10 ** 2
POLL_S = 5
TAIL_LINES = 12
GUARDED_PORT = 11436
ABLATIONS = ["none", "stage1", "stage2", "stage1+2", "full"]
DOC_XPIA, AGENT_XPIA = ["none", "stage1"], ["stage2", "stage1+2", "full"]
EXIT_OK, EXIT_INCOMPLETE, EXIT_PAUSED = 0, 2, 3


# =============================================================================== plan
@dataclass
class Phase:
    name: str
    title: str                  # plain words
    what: str                   # what the tests measure
    argv: list[str]
    needs: list[str]
    kind: str                   # document | agent | guard | pyrit | garak (time estimate)
    planned: int | None         # planned tests (None: known only after the run)
    work: int | None            # model calls / episodes behind them (time estimate)
    guarded: bool = False       # needs scripts/guarded_server.py
    exclusive: bool = False     # runs alone (other model / GPU-heavy search): nothing else starts meanwhile


def _arg(args: list[str], flag: str, default=None):
    """Value after a flag in a runner argument list (all values after it for --attacks / --xpia-converters)."""
    if flag not in args:
        return default
    i = args.index(flag) + 1
    vals = []
    while i < len(args) and not args[i].startswith("--"):
        vals.append(args[i])
        i += 1
    return vals if flag in ("--attacks", "--xpia-converters") else vals[0]


def _lines(p: Path) -> int:
    return sum(1 for _ in p.open()) if p.exists() else 0


def profile_of(cfg, name: str) -> dict:
    prof = cfg.campaign["profiles"][name]
    return {"workers": prof["workers"], "parallel_requests": prof["parallel_requests"], "set": list(prof["set"]),
            "args": dict(prof.get("args") or {}), "min_tests": prof.get("min_tests", cfg.campaign["min_tests"]),
            "skip": list(prof.get("skip") or [])}


def build_plan(cfg, prof: dict, extra_set: list[str]) -> list[Phase]:
    s1, s2, rc = cfg.stage1, cfg.stage2, cfg.redteam
    sets = [x for kv in [*prof["set"], f"models.parallel_requests={prof['parallel_requests']}", *extra_set]
            for x in ("--set", kv)]
    A = prof["args"]
    V = len(s1["variants"])
    plan: list[Phase] = []

    # ---- Stage 1 (documents) -----------------------------------------------------------------------------
    sa = A.get("stage1", [])
    at = getattr(cfg, "autotune", None) or {}
    tune = at.get("enabled", False) and "autotune" not in prof.get("skip", [])
    if tune:
        n_c = max(1, len(at.get("candidates", {})) * len(at.get("sandwich", [False])))
        plan.append(Phase("autotune", "Autotune · search for stronger Stage 1 forms and tighter bounds",
                          "successive halving over candidate defences on the fit split (+ estimator simulation, "
                          "+ model comparison); adopts only clear wins into config/autotuned.yaml",
                          [*PY, "eval/autotune.py", *sets], [], "document", None,
                          at.get("n_max", 0) * n_c + at.get("model_n", 0) * len(at.get("models", [])),
                          exclusive=True))
    for split, needs in (("fit", ["autotune"] if tune else []), ("report", ["stage1_fit"])):
        n_att = _lines(ROOT / "data" / "processed" / f"stage1_{split}_attacks.jsonl")
        n_cl = _lines(ROOT / "data" / "processed" / f"stage1_{split}_clean.jsonl")
        tg = min(s1["n_adaptive_targets"], n_att)
        if "--pilot" in sa:                                  # stage1_runner --pilot sizes
            n_att, n_cl, tg = min(s1["pilot_n"], n_att), min(s1["pilot_clean"], n_cl), s1["pilot_adaptive_targets"]
        planned = n_att * V + n_cl * V + tg * V * s1["n_adaptive_eval_keys"]
        work = n_att * V + n_cl * V + tg * V * (2 * s1["attack_budget"] + s1["n_adaptive_eval_keys"])
        plan.append(Phase(f"stage1_{split}", f"Stage 1 on the {split} split",
                          "each attack document through every marking τ (+ clean documents for utility, + the "
                          "adaptive attacker)" + (" → chooses π*" if split == "fit" else " → evaluated at the fit π*"),
                          [*PY, "eval/stage1_runner.py", "--split", split, "--static", "--clean", "--adaptive",
                           "--analyze", *sa, *sets], needs, "document", planned, work))

    alt = cfg.models.get("alt_victim")
    if alt and "stage1_altmodel" not in prof.get("skip", []):
        n_att = _lines(ROOT / "data" / "processed" / "stage1_report_attacks.jsonl")
        n_cl = _lines(ROOT / "data" / "processed" / "stage1_report_clean.jsonl")
        if "--pilot" in sa:
            n_att, n_cl = min(s1["pilot_n"], n_att), min(s1["pilot_clean"], n_cl)
        tag = alt.split("/", 1)[1].split(":")[0]
        plan.append(Phase("stage1_altmodel", f"Stage 1 on a second victim model ({alt.split('/', 1)[1]})",
                          "report split, every τ, the same attacks: do the Stage 1 results carry over to another "
                          "model family? (π* from the main model is evaluated as-is)",
                          [*PY, "eval/stage1_runner.py", "--split", "report", "--static", "--clean", "--analyze",
                           "--tag", tag, *sa, *sets, "--set", f"models.q_llm={alt}",
                           "--set", "models.disable_thinking=false"],
                          ["stage1_fit"], "document", (n_att + n_cl) * V, (n_att + n_cl) * V, exclusive=True))

    # ---- Stage 2 (AgentDojo agent) -----------------------------------------------------------------------
    splits = json.loads((ROOT / "data" / "splits" / "agentdojo.json").read_text())["suites"]

    def episodes(split: str, args: list[str], adaptive: bool = False) -> tuple[int, int]:
        lu, li = _arg(args, "--limit-user"), _arg(args, "--limit-injection")
        n_att = len(_arg(args, "--attacks") or s2["attacks"])
        tests = work = 0
        for sname in s2["suites"]:
            u = len(splits[sname][split][: int(lu) if lu else None])
            i = len(splits[sname]["injection_tasks"][: int(li) if li else None])
            if adaptive:
                tests += u * i
                work += u * i * s2.get("adaptive_budget", s1["attack_budget"])
            else:
                tests += u * (1 + n_att * i)
                work += u * (1 + n_att * i)
        return tests, work

    t, w = episodes("calibration", A.get("calibrate", []))
    plan.append(Phase("calibrate", "Calibrate λ̂ (conformal risk control)",
                      "agent episodes on the calibration split; every CONFIRM records the readers' agreement → λ̂",
                      [*PY, "eval/agentdojo_runner.py", "--calibrate", "--ablation", "stage1+2",
                       *A.get("calibrate", []), *sets], ["stage1_fit"], "agent", t, w))
    for ab in ABLATIONS:
        needs = {"none": [], "stage1": ["stage1_fit"], "stage2": ["calibrate"]}.get(ab, ["stage1_fit", "calibrate"])
        t, w = episodes("report", A.get("ablation", []))
        plan.append(Phase(f"ablation_{ab}", f"AgentDojo · defence '{ab}'",
                          "benign tasks (utility) and AgentDojo's injection attacks against the agent",
                          [*PY, "eval/agentdojo_runner.py", "--split", "report", "--ablation", ab, "--separate-out",
                           *A.get("ablation", []), *sets], needs,
                          "agent_plain" if ab in ("none", "stage1") else "agent", t, w))
    t, w = episodes("report", A.get("agent_adaptive", []), adaptive=True)
    plan.append(Phase("agent_adaptive", "AgentDojo · adaptive attacker vs. full Horizon",
                      "an attacker LLM rewrites the injection up to attack_budget times per target",
                      [*PY, "eval/agentdojo_runner.py", "--split", "report", "--ablation", "full", "--adaptive",
                       "--separate-out", *A.get("agent_adaptive", []), *sets],
                      ["stage1_fit", "calibrate"], "agent", t, w))

    # ---- red-teaming: indirect (PyRIT XPIA) ----------------------------------------------------------------
    xa = A.get("xpia", [])
    n_x = _lines(ROOT / "data" / "processed" / f"stage1_{rc['xpia_split']}_attacks.jsonl")
    n_x = min(int(_arg(xa, "--limit", n_x)), n_x)
    conv = _arg(xa, "--xpia-converters") or rc["xpia_converters"]
    rest = [a for a in xa if a not in ("--xpia-converters", *conv)]
    for c in rc["xpia_configs"]:
        needs = [] if c == "none" else ["stage1_fit"] if c == "stage1" else ["stage1_fit", "calibrate"]
        plan.append(Phase(f"xpia_{c}", f"PyRIT XPIA · '{c}'",
                          "PyRIT plants each attack (optionally disguised) in a document; Horizon processes it",
                          [*PY, "eval/jailbreak_runner.py", "--xpia", c, "--xpia-converters", *conv,
                           "--xpia-items", f"xpia_items__{c}.csv", *rest, *sets],
                          needs, "document" if c in DOC_XPIA else "agent", n_x * len(conv), n_x * len(conv)))

    # ---- red-teaming: direct ----------------------------------------------------------------------------------
    da = A.get("detect", [])
    try:
        from jailbreak_runner import load_sets
        lim = _arg(da, "--limit")
        n_det = sum(len(p) for _, p in load_sets(cfg.run["seed"], int(lim) if lim else None).values()) * 2
    except Exception:                                    # data not downloaded yet
        n_det = None
    plan.append(Phase("detect", "Guards · detection and false alarms",
                      "every attack / benign prompt through the input guard (DeBERTa) and the output guard "
                      "(Qwen3Guard)", [*PY, "eval/jailbreak_runner.py", "--detect", *da, *sets], [], "guard",
                      n_det, n_det))
    pa = A.get("pyrit", [])
    lim = _arg(pa, "--limit")
    n_obj = 2 * int(lim) if lim else None
    plan.append(Phase("pyrit", "PyRIT · PAIR, TAP, Crescendo jailbreaks",
                      "multi-turn attacker LLM against the guarded endpoint",
                      [*PY, "eval/jailbreak_runner.py", "--pyrit", *pa, *sets], [], "pyrit",
                      n_obj * 3 if n_obj else None, n_obj * 3 if n_obj else None, guarded=True))
    plan.append(Phase("garak", "garak · direct + indirect probes",
                      "dan, promptinject, encoding · latentinjection, web_injection on the bare model and the "
                      "guarded endpoint", [*PY, "eval/jailbreak_runner.py", "--garak", *A.get("garak", []), *sets],
                      [], "garak", None, None, guarded=True))
    return plan


ORDER = ["autotune", "stage1_fit", "detect", "ablation_none", "xpia_none", "calibrate", "stage1_report", "ablation_stage1",
         "xpia_stage1", "ablation_stage2", "ablation_stage1+2", "ablation_full", "xpia_stage2", "xpia_stage1+2",
         "xpia_full", "pyrit", "garak", "agent_adaptive", "stage1_altmodel"]


def estimate_hours(p: Phase, cfg, prof: dict) -> float | None:
    sec = cfg.campaign["seconds_per_test"].get(p.kind)
    if sec is None or p.work is None:
        return None
    par = prof["parallel_requests"] if p.kind == "document" else 1     # Stage 1 / XPIA-doc threads use the slots
    return p.work * sec / par / HOUR


def print_plan(plan: list[Phase], cfg, prof: dict, name: str) -> None:
    rows = []
    for p in sorted(plan, key=lambda x: ORDER.index(x.name)):
        h = estimate_hours(p, cfg, prof)
        rows.append({"phase": p.name, "needs": ",".join(p.needs) or "-", "tests": p.planned if p.planned else "?",
                     "est_hours": f"{h:.1f}" if h is not None else "?", "what": p.title})
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    tot = sum(p.planned or 0 for p in plan)
    hrs = [estimate_hours(p, cfg, prof) for p in plan]
    print(f"\nprofile '{name}': ≥ {tot:,} planned tests (garak counted after the run) · target "
          f"{cfg.campaign['min_tests']:,}")
    h = sum(x for x in hrs if x)
    print(f"est_hours = per-test time measured on the laptop (campaign.seconds_per_test) × tests ÷ "
          f"{prof['parallel_requests']} parallel slots for document phases; a cluster GPU is faster per test.")
    print(f"one phase after another: ≈ {h / 24:.1f} days ({h:.0f} h; garak not estimated)"
          + (f" · {prof['workers']} phases run side by side, so the wall-clock time is shorter"
             if prof["workers"] > 1 else ""))


# =============================================================================== state, ledger
LANE = {"name": None}            # set by --lane: A / B run their phase sets on two GPUs, each with its own state


def state_path(lane: str | None = None) -> Path:
    lane = LANE["name"] if lane is None else lane
    return CAMP / (f"state-{lane}.json" if lane else "state.json")


def load_state() -> dict:
    p = state_path()
    if not p.exists() and LANE["name"] and STATE.exists():
        return json.loads(STATE.read_text())         # first lane run: continue from the single-job state
    return json.loads(p.read_text()) if p.exists() else {"phases": {}, "started": None, "topup_rounds": 0}


def save_state(st: dict) -> None:
    CAMP.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(st, indent=1))


def merged_state(st: dict, cfg) -> dict:
    """One view for the report: every phase from the lane that owns it (else the latest status anywhere)."""
    lanes = cfg.campaign.get("lanes") or {}
    out = {**st, "phases": {}}
    files = {p.stem: json.loads(p.read_text()) for p in CAMP.glob("state*.json")}
    files[state_path().stem] = st
    for stem, d in sorted(files.items()):
        for n, ph in d.get("phases", {}).items():
            owner = next((f"state-{ln}" for ln, ps in lanes.items() if n in ps), None)
            if n not in out["phases"] or stem == owner or ph.get("status") == "ok":
                out["phases"][n] = ph
    return out


def dep_status(st: dict, d: str, mine: set[str], lanes: dict | None = None) -> str | None:
    """Status of a dependency; one owned by another lane is read from that lane's state file only."""
    if d in mine or not LANE["name"]:
        return st["phases"].get(d, {}).get("status")
    owner = next((ln for ln, ps in (lanes or {}).items() if d in ps), None)
    p = state_path(owner) if owner else STATE
    if not p.exists():
        return "pending"
    return json.loads(p.read_text()).get("phases", {}).get(d, {}).get("status") or "pending"


def ledger(event: str, **kw) -> None:
    CAMP.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "event": event, **kw}) + "\n")
    print(f"[{datetime.now():%H:%M:%S}] {event} " + " ".join(f"{k}={v}" for k, v in kw.items()), flush=True)


def archive_results() -> Path | None:
    dirs = [RES / d for d in ("stage1", "stage2", "testing", "campaign") if (RES / d).exists()]
    if not dirs:
        return None
    dest = RES / "archive" / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True)
    for d in dirs:
        shutil.move(str(d), dest / d.name)
    return dest


# =============================================================================== servers
def ensure_model_server(cfg, prof: dict) -> None:
    if server_alive(cfg.models):
        return
    ledger("start_server", what="Ollama + role proxy", parallel=prof["parallel_requests"])
    env = dict(os.environ, HORIZON_PARALLEL=str(prof["parallel_requests"]))
    subprocess.run(["./scripts/serve_local.sh"], cwd=ROOT, env=env, check=True)


def start_guarded(prof: dict, extra_set: list[str]) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    sets = [x for kv in [*prof["set"], *extra_set] for x in ("--set", kv)]
    log = (LOGS / "guarded_server.log").open("a")
    proc = subprocess.Popen([*PY, "scripts/guarded_server.py", "--port", str(GUARDED_PORT), *sets], cwd=ROOT,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    import urllib.request
    for _ in range(HOUR // POLL_S):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{GUARDED_PORT}", timeout=POLL_S)
        except OSError as e:
            if "refused" not in str(e).lower():
                break                                            # any HTTP answer means the server is up
        except Exception:
            break
        time.sleep(POLL_S)
    ledger("start_server", what="guarded endpoint", port=GUARDED_PORT)
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=MINUTE)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass


# =============================================================================== notifications
GROUPS = {"direct layer (guards · PyRIT · garak)": ["detect", "pyrit", "garak"],
          "AgentDojo ablations": [f"ablation_{a}" for a in ABLATIONS],
          "PyRIT XPIA (all defence setups)": [f"xpia_{c}" for c in ABLATIONS]}
SINGLE = {"autotune": "🔧 Autotune finished", "stage1_fit": "🧩 Stage 1 fit done → π* chosen",
          "calibrate": "🎚 Calibration done → λ̂", "stage1_report": "📄 Stage 1 report split done",
          "agent_adaptive": "🗡 Adaptive attacker vs full Horizon done",
          "stage1_altmodel": "🔁 Second victim model done"}


def notify(text: str) -> None:
    try:
        from horizon.notify import send
        send(text)
    except Exception:
        pass


def _asr_lines(phases: list[str], cfg) -> list[str]:
    try:
        df, _ = tally(cfg)
    except Exception:
        return []
    df = df[df["phase"].isin(phases) & (df["attacks"] > 0)] if len(df) else df
    from horizon.messages import SETUPS
    return [f"• {r['group']} · {SETUPS.get(r['config'], r['config'])}: {int(r['got_through'])} of "
            f"{int(r['attacks'])} attacks got through ({r['asr']:.1%}, likely between {r['asr_cp_lo']:.1%} and "
            f"{r['asr_cp_hi']:.1%})" for _, r in df.iterrows()][:24]


def milestone_text(n: str, cfg) -> str:
    from horizon import messages
    return messages.single(cfg, n, _asr_lines([n], cfg))


def notify_progress(st: dict, n: str, cfg) -> None:
    """Milestones only: important single phases, whole groups, and failures after the last retry."""
    sent = st.setdefault("notified", [])
    ph = st["phases"].get(n, {})
    from horizon import messages
    if ph.get("status") == "failed":
        notify(messages.failure(n, ph))
        return
    if ph.get("status") == "ok" and n in SINGLE and n not in sent:
        text = milestone_text(n, cfg)
        if text:
            notify(text)
        sent.append(n)
    for g, members in GROUPS.items():
        stats_ = [st["phases"].get(m, {}).get("status") for m in members]
        if g not in sent and all(x in ("ok", "failed", "skipped") for x in stats_) and "ok" in stats_:
            keys, what = messages.GROUP_INFO[g]
            notify(messages.group(cfg, g, _asr_lines(members, cfg), keys, what))
            sent.append(g)


# =============================================================================== run
STOP = {"flag": False}


def _on_signal(signum, frame) -> None:          # SLURM sends SIGTERM before the time limit
    STOP["flag"] = True


def _become_subreaper() -> None:
    """Servers restarted by serve_local.sh (nohup … &) are re-parented to this process instead of PID 1: cluster
    node health checks count orphaned user processes as rogue and take the node offline."""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)     # PR_SET_CHILD_SUBREAPER (Linux only)
    except Exception:
        pass


def tail(p: Path, n: int = TAIL_LINES) -> str:
    if not p.exists():
        return ""
    return "\n".join(p.read_text(errors="replace").splitlines()[-n:])


def load_repairs() -> dict[str, list[str]]:
    try:
        return yaml.safe_load(REPAIRS.read_text()) or {}
    except Exception:
        return {}


def keep_repair(n: str, sets: list[str]) -> None:
    """Remember what made phase n pass; later runs (lanes A and B after the smoke test) start with it."""
    known = load_repairs()
    known[n] = sets
    REPAIRS.write_text("# written by eval/campaign.py: settings that made a failed phase pass (delete to undo)\n"
                       + yaml.safe_dump(known, sort_keys=True, allow_unicode=True))


def repair_sets(cfg, n: str, retries: int, known: dict[str, list[str]]) -> tuple[list[str], bool]:
    """--set values for attempt `retries` of phase n: kept repairs, then repair_ladder steps 1..retries."""
    steps = (cfg.campaign.get("repair_ladder") or [])[:retries]
    last = {kv.split("=", 1)[0]: kv for kv in [*known.get(n, []), *[kv for s in steps for kv in s["set"]]]}
    return list(last.values()), any(s.get("alone") for s in steps)        # a later step overrides an earlier one


def run_phases(plan: list[Phase], prof: dict, st: dict, extra_set: list[str], cfg) -> None:
    by = {p.name: p for p in plan}
    lanes = cfg.campaign.get("lanes") or {}
    mine = set(lanes.get(LANE["name"], by)) if LANE["name"] else set(by)
    todo = [n for n in ORDER if n in by and n in mine and st["phases"].get(n, {}).get("status") != "ok"]
    for n in todo:                                   # a new run retries failed / skipped / paused phases
        old = st["phases"].get(n, {})
        st["phases"][n] = {"status": "pending", "seconds": old.get("seconds", 0), "retries": 0}
    running: dict[str, tuple[subprocess.Popen, float]] = {}
    known = load_repairs()
    alone: set[str] = set()                             # phases on a repair step that must run without company
    guarded = None
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        while (todo or running) and not STOP["flag"]:
            for n in list(todo):
                if len(running) >= prof["workers"] or any(by[r].exclusive or r in alone for r in running):
                    break
                p = by[n]
                fix, solo = repair_sets(cfg, n, st["phases"][n].get("retries", 0), known)
                if solo:
                    alone.add(n)
                if (p.exclusive or solo) and running:
                    continue                                     # waits until it can have the GPU alone
                stat = {d: dep_status(st, d, mine, lanes) for d in p.needs}
                if any(s in ("failed", "skipped") for s in stat.values()):
                    todo.remove(n)
                    st["phases"][n] = {"status": "skipped", "reason": "needs " + ", ".join(
                        d for d, s in stat.items() if s in ("failed", "skipped"))}
                    ledger("skip", phase=n, reason=st["phases"][n]["reason"])
                    continue
                if any(s != "ok" for s in stat.values()):
                    continue
                if p.kind != "guard":
                    ensure_model_server(cfg, prof)               # restarts Ollama if it died meanwhile
                if p.guarded and guarded is None:
                    guarded = start_guarded(prof, extra_set)
                log = (LOGS / f"{n}.log").open("a")
                argv = [*p.argv, *[x for kv in fix for x in ("--set", kv)]]
                log.write(f"\n===== {datetime.now().isoformat(timespec='seconds')}  {' '.join(argv)}\n")
                log.flush()
                proc = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True, env=dict(os.environ, MLFLOW_DISABLE_AGENT_HINT="1"))
                running[n] = (proc, time.time())
                todo.remove(n)
                st["phases"][n].update(status="running", start=datetime.now().isoformat(timespec="seconds"),
                                       repair=fix)
                save_state(st)
                ledger("start", phase=n, tests_planned=p.planned)
            for n, (proc, t0) in list(running.items()):
                rc = proc.poll()
                if rc is None:
                    continue
                del running[n]
                ph = st["phases"][n]
                ph.update(status="ok" if rc == 0 else "failed", end=datetime.now().isoformat(timespec="seconds"),
                          seconds=ph.get("seconds", 0) + time.time() - t0, returncode=rc)
                if rc != 0:
                    ph["error_tail"] = tail(LOGS / f"{n}.log")
                ledger("finish", phase=n, status=ph["status"], minutes=round((time.time() - t0) / MINUTE, 1))
                alone.discard(n)
                if rc != 0 and ph.get("retries", 0) < cfg.campaign["max_retries"] and not STOP["flag"]:
                    ph.update(status="pending", retries=ph.get("retries", 0) + 1)   # runners resume where they were
                    todo = sorted([*todo, n], key=ORDER.index)
                    ledger("retry", phase=n, attempt=ph["retries"], max=cfg.campaign["max_retries"])
                    nxt, _ = repair_sets(cfg, n, ph["retries"], known)
                    if nxt != ph.get("repair", []):
                        from horizon import messages
                        notify(messages.repair_retry(cfg, n, ph, nxt))
                elif not STOP["flag"]:
                    if rc == 0 and ph.get("repair") and ph["repair"] != known.get(n):
                        keep_repair(n, ph["repair"])
                        known[n] = ph["repair"]
                        ledger("repair_kept", phase=n, set=ph["repair"])
                        from horizon import messages
                        notify(messages.repair_kept(cfg, n, ph))
                    notify_progress(st, n, cfg)
                if n == "autotune" and rc == 0:                    # adopted forms change the planned counts
                    try:                                           # (report numbers only — never fatal)
                        fresh = {q.name: q for q in build_plan(load_config(None, [*prof["set"], *extra_set]),
                                                               prof, extra_set)}
                        for k, q in fresh.items():
                            if k in by:
                                by[k].planned, by[k].work = q.planned, q.work
                    except Exception as e:
                        ledger("plan_refresh_failed", error=str(e)[:200])
                save_state(st)
            time.sleep(POLL_S)
    finally:
        for n, (proc, t0) in running.items():                 # paused: the runner resumes next time
            stop(proc)
            ph = st["phases"][n]
            ph.update(status="paused", seconds=ph.get("seconds", 0) + time.time() - t0)
            ledger("pause", phase=n)
        stop(guarded)
        save_state(st)


# =============================================================================== merge + tally
def merge_outputs() -> None:
    """Parallel jobs wrote separate files; merge them where the figures / report / shell look."""
    s2 = RES / "stage2"
    for name in ("episodes", "calibration", "runs", "adaptive_trace"):
        parts = sorted(s2.glob(f"report-*/{name}.csv"))
        if parts:
            old = [s2 / "report" / f"{name}.csv"] if (s2 / "report" / f"{name}.csv").exists() else []
            df = pd.concat([pd.read_csv(p) for p in [*old, *parts]], ignore_index=True).drop_duplicates()
            (s2 / "report").mkdir(parents=True, exist_ok=True)
            df.to_csv(s2 / "report" / f"{name}.csv", index=False)
    t = RES / "testing"
    parts = sorted(t.glob("xpia_items__*.csv"))
    if parts:
        from jailbreak_runner import _cp_rows
        old = [t / "xpia_items.csv"] if (t / "xpia_items.csv").exists() else []
        df = pd.concat([pd.read_csv(p) for p in [*old, *parts]], ignore_index=True).drop_duplicates()
        df.to_csv(t / "xpia_items.csv", index=False)
        cfg = load_config()
        _cp_rows(df.assign(n=1), ["round", "config", "converter"] if "round" in df else ["config", "converter"],
                 "success", "n", cfg.stage1["alpha"]).to_csv(t / "xpia.csv", index=False)


def _read(p: Path) -> pd.DataFrame:
    return pd.read_csv(p) if p.exists() and p.stat().st_size else pd.DataFrame()


def _row(area, group, config, phase, attacks=0, through=0, benign=0, benign_ok=0, alarms=0, note="", alpha=None):
    r = {"area": area, "group": group, "config": config, "phase": phase, "tests": int(attacks + benign),
         "attacks": int(attacks), "got_through": int(through), "blocked": int(attacks - through),
         "benign": int(benign), "benign_ok": int(benign_ok), "false_alarms": int(alarms), "note": note}
    if attacks and alpha:
        lo, hi = stats.cp_interval(int(through), int(attacks), alpha)
        r.update(asr=through / attacks, asr_cp_lo=lo, asr_cp_hi=hi)
    return r


def tally(cfg) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """One row per (area, group, configuration): tests, attacks through / blocked, benign outcomes."""
    a = cfg.stage1["alpha"]
    rows, examples = [], {}
    for d in sorted(p for p in (RES / "stage1").glob("*") if (p / "matrix.csv").exists()):
        split = d.name                                       # fit · report (· fit_pilot · report_<model>)
        parts = split.split("_")
        ph = f"stage1_{parts[0]}" if set(parts[1:]) <= {"pilot"} else "stage1_altmodel"
        m = _read(d / "matrix.csv")
        for v, g in (m.groupby("variant") if len(m) else []):
            rows.append(_row("Stage 1", f"document attacks ({split})", v, ph, len(g),
                             g["A"].sum(), alpha=a))
        if len(m):
            examples[f"stage1_{split}"] = m
        c = _read(d / "clean.csv")
        for v, g in (c.groupby("variant") if len(c) else []):
            rows.append(_row("Stage 1", f"clean documents ({split})", v, ph, benign=len(g),
                             benign_ok=g["utility"].sum(), note=f"mean token-F1 {g['utility'].mean():.3f}"))
        e = _read(d / "adaptive_eval.csv")
        for v, g in (e.groupby("variant") if len(e) else []):
            rows.append(_row("Stage 1", f"adaptive attacker ({split})", v, ph, len(g),
                             g["A"].sum(), alpha=a))
    for split in ("calibration", "report"):
        ep = _read(RES / "stage2" / split / "episodes.csv")
        if not len(ep):
            continue
        ep["ablation"] = ep["config"].str.split("|").str[0]
        for ab, g in ep.groupby("ablation"):
            att, ben = g[g["style"] != "benign"], g[g["style"] == "benign"]
            note = f"G={int(att['G'].sum())} C={int(att['C'].sum())} F={int(att['F'].sum())}"
            phase = "calibrate" if split == "calibration" else f"ablation_{ab}"
            rows.append(_row("Stage 2", f"AgentDojo ({split})", ab, phase, len(att), att["asr"].sum(), len(ben),
                             ben["utility"].sum(), note=note, alpha=a))
        examples[f"stage2_{split}"] = ep
    ad = _read(RES / "stage2" / "report" / "adaptive_trace.csv")
    if len(ad):
        tg = ad.groupby(["config", "suite", "user_task", "injection_task"])["success"].max()
        rows.append(_row("Stage 2", "AgentDojo adaptive attacker", "full", "agent_adaptive", len(tg), tg.sum(),
                         note=f"{len(ad)} episodes", alpha=a))
    x = _read(RES / "testing" / "xpia_items.csv")
    if len(x):
        x["round"] = x["round"] if "round" in x else 0
        for (c, conv), g in x.groupby(["config", "converter"]):
            rows.append(_row("Red team · indirect", f"PyRIT XPIA ({conv})", c, f"xpia_{c}", len(g), g["success"].sum(),
                             note=f"rounds {sorted(g['round'].unique().tolist())}", alpha=a))
        examples["xpia"] = x
    di = _read(RES / "testing" / "direct_items.csv")
    if len(di):
        for (det, kind), g in di.groupby(["detector", "kind"]):
            if kind == "attack":
                rows.append(_row("Red team · direct", "guard detection", det, "detect", len(g),
                                 len(g) - g["blocked"].sum(), alpha=a, note="blocked = detected"))
            else:
                rows.append(_row("Red team · direct", "guard false alarms", det, "detect", benign=len(g),
                                 benign_ok=len(g) - g["blocked"].sum(), alarms=g["blocked"].sum()))
    py = _read(RES / "testing" / "pyrit.csv")
    if len(py):
        for k, g in py.groupby("attack"):
            rows.append(_row("Red team · direct", "PyRIT jailbreak", k, "pyrit", len(g),
                             g["outcome"].astype(str).str.contains("SUCCESS").sum(), alpha=a))
    ga = _read(RES / "testing" / "garak_items.csv")
    if len(ga):
        for (tgt, fam), g in ga.groupby(["target", "family"]):
            rows.append(_row("Red team · " + fam, "garak probes", tgt, "garak", g["n"].sum(), g["fails"].sum(),
                             alpha=a, note=", ".join(sorted({p.split('.')[0] for p in g["probe"]}))))
    return pd.DataFrame(rows), examples


def total_tests(df: pd.DataFrame) -> int:
    return int(df["tests"].sum()) if len(df) else 0


# =============================================================================== report
PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage{fontspec}
\setmainfont{DejaVu Serif}
\setmonofont{DejaVu Sans Mono}[Scale=0.8]
\usepackage[landscape,margin=1.6cm]{geometry}
\usepackage{booktabs,longtable,float,xcolor,caption,graphicx,enumitem}
\usepackage[hidelinks]{hyperref}
\captionsetup{font=small}
\title{Horizon campaign report}
\date{%s}
\begin{document}
\maketitle
"""


def compact(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Printable results table: one interval column, benign as 'ok / n'."""
    ci = f"{1 - cfg.stage1['alpha']:.0%} CI"
    out = pd.DataFrame({"area": df["area"], "group": df["group"], "config": df["config"], "tests": df["tests"],
                        "attacks": df["attacks"], "got through": df["got_through"], "blocked": df["blocked"]})
    has = df["attacks"] > 0
    out["ASR"] = [f"{v:.1%}" if h else "" for v, h in zip(df.get("asr", pd.Series(float("nan"), index=df.index)), has)]
    out[ci] = [f"{lo:.1%}–{hi:.1%}" if h else "" for lo, hi, h in
               zip(df.get("asr_cp_lo", out["tests"]), df.get("asr_cp_hi", out["tests"]), has)]
    out["benign ok"] = [f"{o:.0f} / {b}" if b else "" for o, b in zip(df["benign_ok"], df["benign"])]
    out["false alarms"] = [str(a) if b else "" for a, b in zip(df["false_alarms"], df["benign"])]
    out["note"] = df["note"]
    return out


def fmt_dur(sec: float) -> str:
    h, rem = divmod(int(sec), HOUR)
    return f"{h} h {rem // MINUTE:02d} min"


def write_report(cfg, plan: list[Phase], st: dict, status: str) -> Path:
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_report import esc, latex_table
    OUTDIR.mkdir(parents=True, exist_ok=True)
    st = merged_state(st, cfg)
    merge_outputs()
    df, ex = tally(cfg)
    total = total_tests(df)
    target = cfg.campaign["min_tests"]
    df.to_csv(OUTDIR / "results_by_group.csv", index=False)
    by = {p.name: p for p in plan}
    proc = []
    for n in ORDER:
        if n not in by:
            continue
        ph = st["phases"].get(n, {})
        got = int(df.loc[df["phase"] == n, "tests"].sum()) if len(df) else 0
        proc.append({"order": len(proc) + 1, "phase": n, "what": by[n].title, "status": ph.get("status", "not started"),
                     "start": ph.get("start", ""), "end": ph.get("end", ""),
                     "duration": fmt_dur(ph["seconds"]) if ph.get("seconds") else "",
                     "tests planned": by[n].planned if by[n].planned else "?", "tests done": got})
    pdf = pd.DataFrame(proc)
    pdf.to_csv(OUTDIR / "process.csv", index=False)
    att = df[df["attacks"] > 0] if len(df) else df
    summary = {"status": status, "profile": st.get("profile"), "started": st.get("started"),
               "finished": datetime.now().isoformat(timespec="seconds"), "total_tests": total, "min_tests": target,
               "target_met": total >= target, "attacks": int(att["attacks"].sum()) if len(att) else 0,
               "attacks_got_through": int(att["got_through"].sum()) if len(att) else 0,
               "topup_rounds": st.get("topup_rounds", 0),
               "phases": {r["phase"]: r["status"] for r in proc}}
    (OUTDIR / "summary.json").write_text(json.dumps(summary, indent=1))

    # ---------- narrative (plain words, computed from the files) ----------
    lines = [f"Status: **{status}** · profile `{st.get('profile')}` · started {st.get('started')} · "
             f"report written {summary['finished']}.",
             f"**{total:,} tests** finished (target {target:,}: {'met' if total >= target else 'NOT met'}). "
             f"Of {summary['attacks']:,} attack tests, {summary['attacks_got_through']:,} got through and "
             f"{summary['attacks'] - summary['attacks_got_through']:,} were blocked or ignored "
             "(all areas and configurations pooled — see the tables for each one)."]
    failed = [r for r in proc if r["status"] in ("failed", "skipped", "paused", "not started")]
    if failed:
        lines.append("Phases that did not finish: " + "; ".join(f"{r['phase']} ({r['status']})" for r in failed) + ".")
    if summary["topup_rounds"]:
        lines.append(f"{summary['topup_rounds']} extra XPIA round(s) ran to reach the target; a round repeats the "
                     "same attacks with fresh keys κ, so those repeats are not independent new attacks.")
    for area, g in (att.groupby("area") if len(att) else []):
        best = g.sort_values("asr").iloc[0]
        worst = g.sort_values("asr").iloc[-1]
        lines.append(f"{area}: lowest attack success {best['asr']:.1%} ({best['group']} · {best['config']}), "
                     f"highest {worst['asr']:.1%} ({worst['group']} · {worst['config']}).")

    # ---------- examples ----------
    k = cfg.campaign["examples_per_group"]
    ex_rows = []
    for key, d in ex.items():
        col = "A" if "A" in d else "asr" if "asr" in d else "success"
        idc = "attack_id"
        txt = "reply" if "reply" in d else "answer" if "answer" in d else None
        cfgc = "variant" if "variant" in d else "ablation" if "ablation" in d else "config"
        if "style" in d:
            d = d[d["style"] != "benign"]
        for outcome, sub in (("got through", d[d[col] == 1]), ("blocked", d[d[col] == 0])):
            for _, r in sub.head(k).iterrows():
                ex_rows.append({"source": key, "outcome": outcome, "config": r[cfgc], "case": r[idc],
                                "output (start)": str(r[txt])[:140].replace("\n", " ") if txt else ""})
    exd = pd.DataFrame(ex_rows)
    exd.to_csv(OUTDIR / "examples.csv", index=False)

    # ---------- markdown ----------
    show = ["area", "group", "config", "tests", "attacks", "got_through", "blocked", "asr", "asr_cp_lo", "asr_cp_hi",
            "benign", "benign_ok", "false_alarms", "note"]
    dshow = df[[c for c in show if c in df]] if len(df) else df
    md = ["# Horizon campaign report", "", *[f"- {x}" for x in lines], "", "## How it went (process)", "",
          pdf.to_markdown(index=False) if len(pdf) else "(nothing ran)", ""]
    errs = [(n, ph["error_tail"]) for n, ph in st["phases"].items() if ph.get("error_tail")]
    if errs:
        md += ["## Errors (last log lines)", ""] + [f"### {n}\n```\n{t}\n```" for n, t in errs]
    md += ["## Results by group", "", "`got_through` = the attack worked; `asr` = got_through / attacks with a "
           f"{1 - cfg.stage1['alpha']:.0%} Clopper–Pearson interval; `benign_ok` = clean task done correctly (Stage 1: "
           "sum of token-F1).", "", dshow.to_markdown(index=False, floatfmt=".3f") if len(dshow) else "(no results)",
           "", "## Example cases", "", exd.to_markdown(index=False) if len(exd) else "(none)", "",
           f"Files: `{OUTDIR.relative_to(ROOT)}/` (CSV tables) · logs `results/campaign/logs/` · "
           "timeline `results/campaign/ledger.jsonl`."]
    (OUTDIR / "campaign_report.md").write_text("\n".join(md))

    # ---------- LaTeX → PDF ----------
    def bold(s: str) -> str:
        parts = esc(s).split("**")
        return "".join(f"\\textbf{{{p}}}" if i % 2 else p for i, p in enumerate(parts)).replace("`", "")
    tex = [PREAMBLE % esc(summary["finished"]), "\\section*{Summary}\n\\begin{itemize}\n",
           *[f"\\item {bold(x)}\n" for x in lines], "\\end{itemize}\n", "\\section*{How it went}\n",
           latex_table(pdf, "Phases in the order they were scheduled", longtable=True) if len(pdf) else ""]
    for n, t in errs:
        tex.append(f"\\subsection*{{Error in {esc(n)}}}\n\\begin{{small}}\\begin{{verbatim}}\n{t}\n"
                   "\\end{verbatim}\\end{small}\n")
    tex += ["\\section*{Results by group}\n", latex_table(compact(df, cfg), "Attacks that got through / were "
                                                         "blocked, and benign outcomes", longtable=True)
            if len(df) else "", "\\section*{Example cases}\n",
            latex_table(exd.loc[:, (exd != "").any()], "Examples per outcome", longtable=True)
            if len(exd) else "", "\\end{document}\n"]
    (OUTDIR / "campaign_report.tex").write_text("".join(tex))
    if shutil.which("latexmk") and shutil.which("xelatex"):
        r = subprocess.run(["latexmk", "-pdfxe", "-interaction=nonstopmode", "-halt-on-error", "campaign_report.tex"],
                           cwd=OUTDIR, capture_output=True, text=True)
        subprocess.run(["latexmk", "-c", "campaign_report.tex"], cwd=OUTDIR, capture_output=True)
        ledger("report_pdf", ok=r.returncode == 0)
    ledger("report", total_tests=total, status=status, path=str((OUTDIR / "campaign_report.md").relative_to(ROOT)))
    return OUTDIR


# =============================================================================== main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="cluster | laptop (default: campaign.profile)")
    ap.add_argument("--plan", action="store_true", help="only print the plan")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="archive results/{stage1,stage2,testing,campaign} first")
    ap.add_argument("--workers", type=int, help="override the profile's parallel phases")
    ap.add_argument("--min-tests", type=int, help="override campaign.min_tests")
    ap.add_argument("--set", action="append", default=[], help="any params.yaml value, e.g. models.q_llm=…")
    ap.add_argument("--lane", choices=["A", "B"], help="run one lane of campaign.lanes (two GPUs, two jobs)")
    args = ap.parse_args()
    LANE["name"] = args.lane
    if args.lane and args.lane != "A":                  # SQLite over NFS from two nodes is unsafe: own DB
        args.set = [f"run.tracking_uri=results/mlflow_lane{args.lane}.db", *args.set]
    cfg = load_config(None, args.set)
    st = load_state()
    name = args.profile or st.get("profile") or cfg.campaign["profile"]
    prof = profile_of(cfg, name)
    if args.workers:
        prof["workers"] = args.workers
    min_tests = args.min_tests if args.min_tests is not None else prof["min_tests"]
    cfg = load_config(None, [*prof["set"], f"models.parallel_requests={prof['parallel_requests']}", *args.set,
                             f"campaign.min_tests={min_tests}"])
    plan = build_plan(cfg, prof, args.set)
    if args.plan:
        print_plan(plan, cfg, prof, name)
        return EXIT_OK
    if args.report_only:
        write_report(cfg, plan, st, "report only")
        return EXIT_OK
    if args.fresh:
        dest = archive_results()
        st = load_state()
        if dest:
            print(f"old results moved to {dest.relative_to(ROOT)}")
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    _become_subreaper()
    st.setdefault("started", datetime.now().isoformat(timespec="seconds"))
    st["started"] = st["started"] or datetime.now().isoformat(timespec="seconds")
    st["profile"] = name
    save_state(st)
    ledger("campaign", profile=name, workers=prof["workers"], planned=sum(p.planned or 0 for p in plan),
           min_tests=cfg.campaign["min_tests"])
    from horizon import messages
    done_now = total_tests(tally(cfg)[0])
    lanes = cfg.campaign.get("lanes") or {}
    mine = [n for n in ORDER if any(p.name == n for p in plan) and (not args.lane or n in lanes.get(args.lane, []))]
    notify(messages.campaign_start(cfg, name, args.lane, os.environ.get("PBS_JOBID", "local"), done_now,
                                   sum(p.planned or 0 for p in plan), mine, bool(st.get("phases"))))
    if "methods" not in st.setdefault("notified", []):          # once per campaign: what differs from the original
        notify(messages.method_changes(cfg))
        st["notified"].append("methods")
        save_state(st)
    ensure_model_server(cfg, prof)
    run_phases(plan, prof, st, args.set, cfg)
    # ---- top-up: fresh-key XPIA rounds until min_tests ------------------------------------------------------
    merge_outputs()
    rc = cfg.campaign
    while (not STOP["flag"] and args.lane in (None, "A") and total_tests(tally(cfg)[0]) < rc["min_tests"]
           and st.get("topup_rounds", 0) < rc["max_topup_rounds"]
           and all(st["phases"].get(n, {}).get("status") == "ok" for n in ("xpia_none", "xpia_stage1"))):
        r = st.get("topup_rounds", 0) + 1
        ledger("topup", round=r, have=total_tests(tally(cfg)[0]), need=rc["min_tests"])
        argv = [*PY, "eval/jailbreak_runner.py", "--xpia", *rc["topup_configs"], "--xpia-converters",
                *rc["topup_converters"], "--xpia-round", str(r), "--xpia-items", f"xpia_items__topup{r}.csv",
                *[x for kv in [*prof["set"], *args.set] for x in ("--set", kv)]]
        with (LOGS / "topup.log").open("a") as log:
            p = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            while p.poll() is None and not STOP["flag"]:
                time.sleep(POLL_S)
            stop(p)
        if p.returncode != 0 or STOP["flag"]:
            break
        st["topup_rounds"] = r
        save_state(st)
        merge_outputs()
    total = total_tests(tally(cfg)[0])
    view = merged_state(st, cfg)
    all_ok = all(view["phases"].get(p.name, {}).get("status") == "ok" for p in plan)
    status = ("paused (time limit / Ctrl-C) — run the same command again to continue" if STOP["flag"] else
              "complete" if all_ok and total >= rc["min_tests"] else "finished with problems")
    write_report(cfg, plan, st, status)
    if STOP["flag"]:
        notify(messages.paused(total, args.lane))
    else:
        df_ = tally(cfg)[0]
        att = df_[df_["attacks"] > 0] if len(df_) else df_
        notify(messages.finished(status, total, int(att["got_through"].sum()) if len(att) else 0,
                                 int(att["attacks"].sum()) if len(att) else 0, args.lane))
    return EXIT_PAUSED if STOP["flag"] else EXIT_OK if status == "complete" else EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
