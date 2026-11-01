#!/usr/bin/env bash
# ONE command on the LOGIN node: the 2-hour look at BOTH models, side by side.
#
#   ./cluster/launch_quick2h.sh
#
# Submits exactly two GPU jobs (teachingq runs at most 2 per user, so both start together):
#   1. Qwen3-8B      — this folder                    → horizon_live.log, report/campaign/
#   2. Gemma 3 12B   — ~/0_runs/gemma3-12b (re-synced) → ~/0_runs/gemma3-12b/horizon_live.log, …/report/campaign/
# Each job runs EVERY phase on one GPU with profile `quick2h` (≈ 800 tests), walltime 2 h, no smoke job, no
# follow-up part: the campaign stops / skips leaf phases after 90 min (finished tests are kept), so both jobs end
# and write their report within the 2 h. Earlier results with another profile are moved to results/archive/ first.
# At the end:  .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b
set -euo pipefail
cd "$(dirname "$0")/.."
export PROFILE=quick2h WALLTIME_H=2 ONE_GPU=1 NO_SMOKE=1 NO_RESUBMIT=1 PROGRESS_MIN="${PROGRESS_MIN:-30}"

echo "######## Qwen3-8B (this folder)"
JOBNAME=qwen-2h ./cluster/launch_bounded.sh

echo; echo "######## Gemma 3 12B (~/0_runs/gemma3-12b)"
SKIP_TESTS=1 JOBNAME=gemma-2h ./cluster/launch_variant.sh gemma3-12b

echo; echo "######## Telegram bot (/status, /qstat, /log … on your phone)"
./cluster/telegram_bot.sh start || echo "  (bot not started — run ./cluster/telegram_bot.sh start by hand)"

echo
echo "both submitted. Watch:  qstat -u $USER   or /status in Telegram"
echo "  tail -f horizon_live.log ~/0_runs/gemma3-12b/horizon_live.log"
echo "  .venv/bin/python scripts/progress.py ;  (cd ~/0_runs/gemma3-12b && .venv/bin/python scripts/progress.py)"
echo "after both ended (≤ 2 h):  .venv/bin/python scripts/compare_runs.py . ~/0_runs/gemma3-12b"
