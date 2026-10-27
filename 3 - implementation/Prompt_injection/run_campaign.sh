#!/usr/bin/env bash
# ONE command for the big test campaign (cluster or laptop): model server → every experiment → report.
#
#   ./run_campaign.sh --prepare             # once, WITH internet (cluster: on the login node): install + download
#   ./run_campaign.sh --plan                # show phases, test counts and time estimate; runs nothing
#   ./run_campaign.sh                       # run / continue (profile from config: campaign.profile = cluster)
#   ./run_campaign.sh --profile laptop      # the smaller plan for the RTX 4060 laptop
#   ./run_campaign.sh --fresh               # move old results to results/archive/ first, start from zero
#   ./run_campaign.sh --report-only         # rewrite report/campaign/ from what exists
#
# Stopped by a time limit or Ctrl-C? Run the same command again: it continues where it stopped.
# Result: report/campaign/campaign_report.pdf (+ .md, CSV tables) · logs: results/campaign/logs/
# Exit code: 0 complete · 2 finished with problems · 3 paused (run again)
set -uo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"
export MLFLOW_DISABLE_AGENT_HINT=1

if [ "${1:-}" = "--prepare" ]; then
  shift
  ./scripts/setup.sh --redteam "$@" || exit 1
  [ -d data/raw/hackaprompt ] || "$UV" run --no-sync python scripts/download_data.py --only hackaprompt \
    --include-gated || echo "HackAPrompt not downloaded (gated: run 'hf auth login' first) — its set is skipped"
  pkill -f "[o]penai_role_proxy" 2>/dev/null; pkill -f "[o]llama serve" 2>/dev/null   # setup started it for pulls
  "$UV" run --no-sync python eval/campaign.py --plan
  echo; echo "prepared. Next: ./run_campaign.sh   (cluster: sbatch cluster/horizon_campaign.sbatch)"
  exit 0
fi

if [ ! -x "$UV" ] || [ ! -d .venv ]; then
  echo "not installed yet → run ./run_campaign.sh --prepare first (needs internet)"; exit 1
fi
"$UV" run --no-sync python -c "import pyrit, garak" 2>/dev/null || {
  echo "PyRIT / garak missing → installing the redteam extra"; "$UV" sync --extra direct --extra redteam || exit 1; }

"$UV" run --no-sync python eval/campaign.py "$@" &
PID=$!
trap 'echo "stop requested → pausing the campaign cleanly"; kill -TERM "$PID" 2>/dev/null' TERM INT
wait "$PID"; rc=$?
if kill -0 "$PID" 2>/dev/null; then wait "$PID"; rc=$?; fi     # the trap interrupted the first wait
echo
echo "campaign exit code $rc  (0 complete · 2 finished with problems · 3 paused → run again)"
[ -f report/campaign/campaign_report.md ] && echo "report: report/campaign/campaign_report.pdf (.md, CSV tables)"
exit "$rc"
