"""Telegram bot commands for watching the cluster runs from the phone.

Commands (send them to the bot; it only answers YOUR chat — the id in ~/.config/horizon/):
    /status            the best overview: PBS jobs (time used / left) + every run's tests done and phase states
    /qstat             `qstat -u $USER` with an analysis per job (running / queued, walltime used and left, exit codes
                       of the jobs that ended recently)
    /progress          every phase of every run: status and tests done / planned (scripts/progress.py per run)
    /log [qwen|gemma]  last lines of the live log of one run (default: all runs)
    /compare           Qwen vs Gemma: overview chart + the significant differences (scripts/compare_runs.py)
    /help              this list

Runs = this project folder (Qwen3-8B) and every ~/0_runs/<name>/ copy (e.g. Gemma 3 12B).
Read-only: the bot never starts, stops or changes anything, and it runs no command a message names — only the fixed
ones above.

Two ways to run it on the LOGIN node (cluster/telegram_bot.sh does this for you):
    .venv/bin/python scripts/telegram_bot.py --once            # one poll, ≤ 50 s long-poll (cron: every minute)
    .venv/bin/python scripts/telegram_bot.py                   # forever (tmux / screen)
    .venv/bin/python scripts/telegram_bot.py --set-commands    # register the command menu in Telegram (once)
    .venv/bin/python scripts/telegram_bot.py --test /status    # print the answer here, send nothing
"""
from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon import notify  # noqa: E402
from horizon.config import ROOT  # noqa: E402

OFFSET_FILE = notify.CONF_DIR / "telegram_offset"
RUNS_DIR = Path.home() / "0_runs"
QSTAT = shutil.which("qstat") or "/opt/pbs/bin/qstat"
USER = os.environ.get("USER") or getpass.getuser()
POLL_S = 50                              # long-poll; cron starts the next poll every minute

COMMANDS = [("status", "overview: PBS jobs + progress of every run"),
            ("qstat", "qstat -u $USER with time used / left per job"),
            ("progress", "every phase of every run: tests done / planned"),
            ("log", "last lines of the live logs (/log qwen, /log gemma)"),
            ("compare", "Qwen vs Gemma: chart + significant differences"),
            ("help", "list the commands")]


# ------------------------------------------------------------------------------------------------ helpers
def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "HORIZON_NO_NOTIFY": "1", "PATH": f"/opt/pbs/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"})
        return (r.stdout + (r.stderr if r.returncode else "")).strip()
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout} s: {' '.join(cmd[:3])} …)"
    except OSError as e:
        return f"(could not run {cmd[0]}: {e})"


def runs() -> list[tuple[str, Path]]:
    """(model name, folder) for the main project and every ~/0_runs/<name> copy."""
    out = [ROOT] + sorted(p for p in RUNS_DIR.glob("*") if (p / "scripts" / "progress.py").exists())
    named = []
    for d in out:
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from compare_runs import run_name
            name = run_name(d)
        except Exception:
            name = d.name
        named.append((name, d))
    return named


def python_of(d: Path) -> str:
    py = d / ".venv" / "bin" / "python"
    return str(py if py.exists() else ROOT / ".venv" / "bin" / "python")


def hms(s: str) -> int | None:
    """'HH:MM:SS' or 'HH:MM' → seconds."""
    if not s or "-" in s:
        return None
    parts = [int(x) for x in s.split(":")]
    parts += [0] * (3 - len(parts))
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def dur(sec: int | None) -> str:
    if sec is None:
        return "?"
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


# ------------------------------------------------------------------------------------------------ answers
STATE = {"R": "▶️ running", "Q": "⏳ queued", "H": "⏸ held", "F": "🏁 finished", "E": "🏁 ending", "W": "⏳ waiting",
         "B": "▶️ array running", "S": "⏸ suspended"}


def jobs(history: int = 4) -> tuple[str, list[dict]]:
    """Raw `qstat -u USER` table and parsed details of the current jobs + the last `history` finished ones."""
    table = sh([QSTAT, "-u", USER], timeout=30)
    # -w (wide): full job ids — the narrow table cuts "1419115.mmaster02" to "1419115.mmaste*", and the short id
    # alone resolves to the wrong server ("Unknown Job Id")
    wide = sh([QSTAT, "-w", "-u", USER], timeout=30)
    hist = sh([QSTAT, "-x", "-w", "-u", USER], timeout=30)
    ids = re.findall(r"^(\d+\.\S+)\s", wide, flags=re.M)
    done = [i for i in re.findall(r"^(\d+\.\S+)\s.*\sF\s", hist, flags=re.M) if i not in ids][-history:]
    details = []
    if ids or done:
        raw = sh([QSTAT, "-x", "-f", "-F", "json", *ids, *done], timeout=30)
        try:
            for jid, j in json.loads(raw).get("Jobs", {}).items():
                rl, ru = j.get("Resource_List", {}), j.get("resources_used", {})
                details.append({"id": jid.split(".")[0], "name": j.get("Job_Name", "?"), "state": j.get("job_state", "?"),
                                "req": hms(rl.get("walltime", "")), "used": hms(ru.get("walltime", "")),
                                "exit": j.get("Exit_status"), "host": str(j.get("exec_host", "")).split("/")[0],
                                "queued": j.get("qtime"), "comment": j.get("comment", ""), "current": jid in ids})
        except ValueError:
            pass
    return table, sorted(details, key=lambda d: d["id"])


EXIT = {0: "done", 2: "done with problems", 3: "paused (time limit)", 271: "killed by PBS (walltime / qdel)",
        -1: "never started (deleted by a dependency or qdel)"}


def job_lines(details: list[dict]) -> list[str]:
    cur = [d for d in details if d["current"]]
    old = [d for d in details if not d["current"]]
    out = [f"🖥 PBS jobs of {USER}: {len(cur)} in the queue (teachingq runs at most 2 at a time)"]
    for d in cur:
        line = f"{STATE.get(d['state'], d['state'])} {d['name']} ({d['id']})"
        if d["state"] == "R" and d["req"]:
            used = d["used"] or 0
            line += (f" · {dur(used)} of {dur(d['req'])} ({100 * used / d['req']:.0f}%) · "
                     f"{dur(max(0, d['req'] - used))} left" + (f" · {d['host']}" if d["host"] else ""))
        elif d["state"] in ("Q", "H", "W"):
            line += f" · asks {dur(d['req'])}" + (f" · {d['comment'][:80]}" if d["comment"] else "")
        out.append(line)
    if not cur:
        out.append("(nothing queued or running)")
    if old:
        out.append("Recently ended:")
        for d in old:
            ex = d["exit"]
            what = (f"exit {ex} = {EXIT.get(ex, 'error')} · ran {dur(d['used'])}" if ex is not None
                    else f"never ran · {d['comment'][:90] or 'deleted'}")
            out.append(f"  {d['name']} ({d['id']}): {what}")
    return out


def run_summary(name: str, d: Path) -> str:
    text = sh([python_of(d), "scripts/progress.py"], cwd=d, timeout=240)
    lines = text.splitlines()
    head = [x for x in lines[:3] if x.strip()]
    marks = {k: sum(1 for x in lines if x.startswith(k)) for k in ("✅", "⏳", "❌", "⏭", "⏸", "⬜")}
    states = " ".join(f"{k}{v}" for k, v in marks.items() if v)
    rep = d / "report" / "campaign" / "summary.json"
    fin = ""
    if rep.exists():
        try:
            s = json.loads(rep.read_text())
            fin = f"\nlast report: {s.get('status')} · {s.get('total_tests', 0):,} tests · {s.get('finished', '')[:16]}"
        except ValueError:
            pass
    return f"🔭 {name}  ({d})\n" + "\n".join(head[1:] if head and head[0].startswith("📊") else head) + \
        (f"\nphases: {states}" if states else "") + fin


def cmd_status(_arg: str) -> list[tuple[str, bool]]:
    _, det = jobs()
    parts = ["\n".join(job_lines(det))] + [run_summary(n, d) for n, d in runs()]
    return [("\n\n".join(parts) + f"\n\n🕒 {datetime.now():%Y-%m-%d %H:%M}", False)]


def cmd_qstat(_arg: str) -> list[tuple[str, bool]]:
    table, det = jobs()
    return [(table or "(qstat: no jobs)", True), ("\n".join(job_lines(det)), False)]


def cmd_progress(_arg: str) -> list[tuple[str, bool]]:
    return [(f"🔭 {n}\n" + sh([python_of(d), "scripts/progress.py"], cwd=d, timeout=240), False) for n, d in runs()]


def cmd_log(arg: str) -> list[tuple[str, bool]]:
    out = []
    for n, d in runs():
        if arg and arg.lower() not in n.lower() and arg.lower() not in d.name.lower():
            continue
        logs = sorted(d.glob("horizon_live*.log"), key=lambda p: p.stat().st_mtime)[-2:]
        for p in logs:
            tail = p.read_text(errors="replace").splitlines()[-20:]
            age = (time.time() - p.stat().st_mtime) / 60
            out.append((f"{n} · {p.name} (last write {age:.0f} min ago)\n" + "\n".join(x[:160] for x in tail), True))
    return out or [(f"no live log found{f' for {arg!r}' if arg else ''}", False)]


def cmd_compare(_arg: str) -> list[tuple[str, bool]]:
    folders = [str(d) for _, d in runs()]
    if len(folders) < 2:
        return [("only one run folder found — nothing to compare", False)]
    text = sh([python_of(ROOT), "scripts/compare_runs.py", *folders, "--no-pdf"], cwd=ROOT, timeout=400)
    out = ROOT / "report" / "compare"
    sig = []
    if (out / "attacks.csv").exists():
        import pandas as pd
        a = pd.read_csv(out / "attacks.csv")
        if "p (Fisher)" in a:
            names = [c for c in a.columns if c not in ("area", "group", "config", "Δ ASR", "p (Fisher)")]
            s = a[a["p (Fisher)"].astype(str).str.endswith("*")]
            sig = [f"• {r['group']} · {r['config']}: {r[names[0]]} → {r[names[-1]]}" for _, r in s.head(12).iterrows()]
            head = f"⚖️ {' vs '.join(names)}: {len(a)} groups, {len(s)} significant differences (α = 0.05)"
            return [("__photo__" + str(out / "charts" / "00_overview.png"), False),
                    ("\n".join([head, *sig]) + f"\nfull report: {out}/compare_runs.md", False)]
    return [(text[-3000:] or "compare failed", True)]


def cmd_help(_arg: str) -> list[tuple[str, bool]]:
    return [("Horizon cluster bot\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS), False)]


HANDLERS = {"status": cmd_status, "qstat": cmd_qstat, "progress": cmd_progress, "log": cmd_log,
            "compare": cmd_compare, "help": cmd_help, "start": cmd_help}


# ------------------------------------------------------------------------------------------------ Telegram I/O
def send_text(token: str, cid: str, text: str, pre: bool) -> None:
    for part in notify.chunks(text, 3500):
        body = f"<pre>{html.escape(part)}</pre>" if pre else html.escape(part)
        try:
            notify._call(token, "sendMessage", {"chat_id": cid, "text": body, "parse_mode": "HTML",
                                                "disable_web_page_preview": "true"})
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)


def send_photo(token: str, cid: str, path: Path, caption: str = "") -> None:
    if not path.exists():
        return
    b = uuid.uuid4().hex
    body = b"".join([
        f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{cid}\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{path.name}\"\r\n"
        "Content-Type: image/png\r\n\r\n".encode(), path.read_bytes(), f"\r\n--{b}--\r\n".encode()])
    req = urllib.request.Request(notify.API.format(token=token, method="sendPhoto"), data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        print(f"photo failed: {e}", file=sys.stderr)


def answer(text: str) -> list[tuple[str, bool]]:
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text.strip())
    if not m:
        return cmd_help("")
    h = HANDLERS.get(m.group(1).lower())
    if h is None:
        return [(f"unknown command /{m.group(1)}", False)] + cmd_help("")
    try:
        return h(m.group(2).strip())
    except Exception as e:                                  # a broken command must not kill the bot
        return [(f"/{m.group(1)} failed: {type(e).__name__}: {e}", False)]


def poll(token: str, cid: str, wait: int) -> int:
    """One getUpdates round; answers commands from the owner's chat only. Returns the number answered."""
    off = int(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else 0
    try:
        with urllib.request.urlopen(notify.API.format(token=token, method="getUpdates")
                                    + f"?timeout={wait}&offset={off}&allowed_updates=%5B%22message%22%5D",
                                    timeout=wait + 15) as r:
            res = json.loads(r.read()).get("result", [])
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} getUpdates failed: {e}", file=sys.stderr)
        return 0
    n = 0
    for u in res:
        OFFSET_FILE.write_text(str(u["update_id"] + 1))      # confirm first: a crashing command is not repeated
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(cid):  # only the owner's chat
            continue
        text = msg.get("text", "")
        if not text.startswith("/"):
            continue
        print(f"{datetime.now():%H:%M:%S} {text}", flush=True)
        notify._call(token, "sendChatAction", {"chat_id": cid, "action": "typing"})
        for body, pre in answer(text):
            if body.startswith("__photo__"):
                send_photo(token, cid, Path(body[len("__photo__"):]), "without vs. with Horizon")
            else:
                send_text(token, cid, body, pre)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one poll (for cron), then exit")
    ap.add_argument("--set-commands", action="store_true", help="register the command menu in Telegram")
    ap.add_argument("--test", metavar="COMMAND", help="print the answer to e.g. '/status' here; send nothing")
    args = ap.parse_args()
    if args.test:
        for body, pre in answer(args.test):
            print(body, end="\n\n")
        return 0
    token = notify._token()
    cid = notify._chat_id(token) if token else None
    if not token or not cid:
        print(f"Telegram not configured: need TELEGRAM_BOT_TOKEN in {notify.ENV_FILE} and a chat id "
              "(send the bot one message, then run: .venv/bin/python -m horizon.notify --check)")
        return 1
    notify.CONF_DIR.mkdir(parents=True, exist_ok=True)
    if args.set_commands:
        r = notify._call(token, "setMyCommands",
                         {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS])})
        print("command menu set" if r.get("ok") else f"setMyCommands failed: {r}")
        return 0 if r.get("ok") else 1
    if args.once:
        poll(token, cid, POLL_S)
        return 0
    while True:
        poll(token, cid, POLL_S)


if __name__ == "__main__":
    sys.exit(main())
