#!/usr/bin/env bash
# Horizon — one entry point for everything.   ./horizon.sh help
set -uo pipefail
cd "$(dirname "$0")"
UV="$HOME/.local/bin/uv"
export MLFLOW_DISABLE_AGENT_HINT=1
server() { curl -s -m 3 localhost:11434/api/version >/dev/null || ./scripts/serve_local.sh; }

help() { cat <<'TXT'
Horizon — defense against prompt injection (indirect: Stage 1 + Stage 2 · direct/jailbreak: guard layer)

  ./horizon.sh                    interactive menu: attack a document, attack an agent, try a jailbreak,
                                  self-test, results, settings, help (start here)
  ./horizon.sh help               this page
  ./horizon.sh status             model server, guards, Stage 1 mix π*, threshold λ̂, settings

  ./horizon.sh setup              install everything (uv, Python, packages, CaMeL, Ollama, models, data) — safe to re-run
  ./horizon.sh serve              start the local model server (Ollama + proxy)
  ./horizon.sh test               unit tests (the maths and the rules)

  ./horizon.sh run quick          tests + figures + smoke test + notebook + PDF report          (~30 min)
  ./horizon.sh run pilot          + a small real Stage 1 experiment and a small agent experiment (hours)
  ./horizon.sh run full           + every experiment at full size                              (days)
  ./horizon.sh report             rewrite report/report.pdf from the results that exist (uses the report AI)
  ./horizon.sh figures            redraw all figures (figures/) and the pipeline diagrams (docs/)
  ./horizon.sh notebook           rebuild and execute Horizon.ipynb

  ./horizon.sh commands           start directly in command mode (advanced; `help` inside lists commands)

Options for the menu / commands:   --set key=value   (any value in config/params.yaml, e.g. --set flags.m=7)
Options for run / report:          --report-model M --report-api-base URL --vision --skip-setup --set key=value
Logs: results/shell/ (menu sessions) · report/run_all.log (run/report) · results/mlflow.db (experiments)
TXT
}

cmd="${1:-menu}"; [ $# -gt 0 ] && shift
case "$cmd" in
  menu|demo)      server; exec "$UV" run --no-sync python scripts/horizon_shell.py "$@" ;;
  commands)       server; exec "$UV" run --no-sync python scripts/horizon_shell.py --commands "$@" ;;
  status)         exec "$UV" run --no-sync python scripts/horizon_shell.py --status "$@" ;;
  help|-h|--help) help ;;
  setup)          exec ./scripts/setup.sh "$@" ;;
  serve)          exec ./scripts/serve_local.sh ;;
  test)           exec "$UV" run --no-sync pytest -q "$@" ;;
  run)            lvl="${1:-quick}"; [ $# -gt 0 ] && shift; exec ./run_all.sh --level "$lvl" "$@" ;;
  report)         exec ./run_all.sh --report-only "$@" ;;
  figures)        "$UV" run --no-sync python scripts/make_figures.py --theory "$@" && \
                  "$UV" run --no-sync python scripts/make_pipeline_diagram.py ;;
  notebook)       exec "$UV" run --no-sync python scripts/build_notebook.py --execute ;;
  --set|-c)       server; exec "$UV" run --no-sync python scripts/horizon_shell.py "$cmd" "$@" ;;
  *)              echo "unknown command '$cmd'"; echo; help; exit 2 ;;
esac
