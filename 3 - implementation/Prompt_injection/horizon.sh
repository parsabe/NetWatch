#!/usr/bin/env bash
# Horizon — the demo front door.   ./horizon.sh help
set -uo pipefail
cd "$(dirname "$0")"
UV="$HOME/.local/bin/uv"
export MLFLOW_DISABLE_AGENT_HINT=1
server() { curl -s -m 3 localhost:11434/api/version >/dev/null || ./scripts/serve_local.sh; }
say() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

help() { cat <<'TXT'
Horizon — defense against prompt injection, as a small DEMO on this laptop
  indirect attacks (hidden in emails / web pages):  Stage 1 (secret-key marking) + Stage 2 (safe CaMeL agent)
  direct attacks (the user attacks):                input guard (DeBERTa) + output guard (Qwen3Guard) + canary
  picture of the whole pipeline:                    docs/pipeline.pdf  (green = math)

TRY IT (small, minutes)
  ./horizon.sh                    interactive menu — start here:
                                    1 attack a document (Stage 1)   2 attack an agent (Stage 2, you approve actions)
                                    3 try a jailbreak (guards)      4 self-test   5 results   6 settings   7 help
  ./horizon.sh try                quick check of every part, ≈ 5 min: unit tests · smoke test (each layer fires
                                  once) · PyRIT XPIA plants 2 attacks in documents (bare model vs Stage 1)
  ./horizon.sh status             is everything ready? model server, guards, π*, λ̂
  ./horizon.sh test               unit tests only (the maths and the rules, seconds)
  ./horizon.sh help               this page

SET UP
  ./horizon.sh setup              install everything (uv, Python, packages, CaMeL, Ollama, Qwen3-8B, guards, data)
  ./horizon.sh serve              start the local model server (Ollama + proxy); the commands above do it for you

BIGGER RUNS (not needed for the demo)
  ./horizon.sh campaign --plan    the big test campaign (≥ 5,000 tests) — see cluster/README.md
  ./horizon.sh run quick|pilot|full     tests + figures + notebook + report (quick ≈ 30 min, full = days)
  ./horizon.sh report             rewrite report/report.pdf from existing results (colibri report model)
  ./horizon.sh figures            redraw figures/ and docs/pipeline.pdf
  ./horizon.sh notebook           rebuild and run Horizon.ipynb
  ./horizon.sh commands           the menu's command mode (advanced)

Options:  --set key=value  (any value in config/params.yaml, e.g. ./horizon.sh --set flags.m=3)
Logs:     results/shell/ (menu) · results/demo/ (try) · report/ (runs)
TXT
}

try_all() {
  server || exit 1
  say "1/3  unit tests — the maths and the rules (no AI)"
  "$UV" run --no-sync pytest -q | tail -1
  say "2/3  smoke test — does each layer fire once? (readers + vote, rules, Z3, canary, guards; ≈ 2 min)"
  "$UV" run --no-sync python scripts/smoke_test.py > results/demo_smoke.log 2>&1 \
    && "$UV" run --no-sync python - <<'EOF'
import json
S = json.load(open("results/smoke/smoke.json"))
q = S.get("quarantine_attack", {})
print("  readers on an attacked email :", "FOOLED" if q.get("fooled") else "not fooled")
for k, v in S.get("policy", {}).items():
    print(f"  rule check {k:20s}: {v}")
print("  Z3 refuses a bad rule table  :", "yes" if "refused" in S.get("z3_bad_policy", "") else "NO")
print("  canary catches a leak        :", "yes" if S.get("canary", {}).get("leak_blocked") else "NO")
for g in ("input_guard", "output_guard"):
    v = S.get(g)
    if v is None:
        print(f"  {g:29s}: not checked yet")
        continue
    print(f"  {g:29s}:", v.get("status") or ("blocked the attack" if v.get("blocked") else "did NOT block"),
          f"({v.get('reason', '')})" if v.get("reason") else "")
EOF
  [ $? -eq 0 ] || echo "  smoke test failed — see results/demo_smoke.log"
  say "3/3  PyRIT XPIA — 2 attacks planted in documents, read by the bare model and by Stage 1 (≈ 1 min)"
  "$UV" run --no-sync python eval/jailbreak_runner.py --xpia none stage1 --xpia-converters none --limit 2 \
    --xpia-round "$(date +%s)" --xpia-items ../demo/xpia_items.csv > results/demo_xpia.log 2>&1 \
    && "$UV" run --no-sync python - <<'EOF'
import pandas as pd
d = pd.read_csv("results/demo/xpia_items.csv")
d = d[d["round"] == d["round"].max()]
for _, r in d.iterrows():
    print(f"  {r['config']:7s} {r['attack_id']:16s} attack {'GOT THROUGH' if r['success'] else 'was ignored'}"
          f"  ({r['detail']})")
EOF
  [ $? -eq 0 ] || echo "  XPIA failed — see results/demo_xpia.log"
  echo; echo "done. Next: ./horizon.sh  (menu)   ·   picture: docs/pipeline.pdf"
}

cmd="${1:-menu}"; [ $# -gt 0 ] && shift
case "$cmd" in
  menu|demo)      server; exec "$UV" run --no-sync python scripts/horizon_shell.py "$@" ;;
  try)            mkdir -p results/demo; try_all ;;
  commands)       server; exec "$UV" run --no-sync python scripts/horizon_shell.py --commands "$@" ;;
  status)         exec "$UV" run --no-sync python scripts/horizon_shell.py --status "$@" ;;
  help|-h|--help) help ;;
  setup)          exec ./scripts/setup.sh "$@" ;;
  serve)          exec ./scripts/serve_local.sh ;;
  test)           exec "$UV" run --no-sync pytest -q "$@" ;;
  run)            lvl="${1:-quick}"; [ $# -gt 0 ] && shift; exec ./run_all.sh --level "$lvl" "$@" ;;
  report)         exec ./run_all.sh --report-only "$@" ;;
  campaign)       exec ./run_campaign.sh "$@" ;;
  figures)        "$UV" run --no-sync python scripts/make_figures.py --theory "$@" && \
                  "$UV" run --no-sync python scripts/make_pipeline_diagram.py ;;
  notebook)       exec "$UV" run --no-sync python scripts/build_notebook.py --execute ;;
  --set|-c)       server; exec "$UV" run --no-sync python scripts/horizon_shell.py "$cmd" "$@" ;;
  *)              echo "unknown command '$cmd'"; echo; help; exit 2 ;;
esac
