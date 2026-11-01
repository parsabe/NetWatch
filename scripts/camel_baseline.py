"""Build step 2: run the CaMeL reference implementation UNMODIFIED on a few AgentDojo tasks.

CaMeL only speaks to OpenAI/Anthropic/Google clients; we point its OpenAI client at
the local Ollama server through OPENAI_BASE_URL (no code change). The model needs
a colon-free alias because CaMeL parses "openai:<name>".

    uv run python scripts/camel_baseline.py --suite slack --tasks user_task_0 user_task_1 [--attack]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402

CAMEL = ROOT / "third_party" / "camel-prompt-injection"


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--suite", default="slack")
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--attack", action="store_true")
    ap.add_argument("--alias", default="local-qwen3-8b")   # "local" ⇒ AgentDojo addresses it as "Local model"
    args = ap.parse_args()
    cfg = config_from_args(args)
    ollama_model = cfg.models["p_llm"].split("/", 1)[1]
    ollama = os.environ.get("OLLAMA_BIN", str(Path.home() / ".local/ollama/bin/ollama"))
    subprocess.run([ollama, "cp", ollama_model, args.alias], check=True)
    out = ROOT / "results" / "camel_baseline"
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OPENAI_BASE_URL": f"http://127.0.0.1:{cfg.models['proxy_port']}/v1", "OPENAI_API_KEY": "local"}
    cmd = [sys.executable, str(CAMEL / "main.py"), f"openai:{args.alias}", "--suites", args.suite]
    for t in args.tasks:                      # cyclopts: repeat the flag per list item
        cmd += ["--user-tasks", t]
    if args.attack:
        cmd.append("--run-attack")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=out, env=env, capture_output=True, text=True)
    (out / f"{args.suite}_{'attack' if args.attack else 'benign'}.log").write_text(proc.stdout + proc.stderr)
    lines = [l for l in proc.stdout.splitlines() if re.search(r"utility|security", l)]
    print("\n".join(lines) or proc.stderr[-3000:])
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
