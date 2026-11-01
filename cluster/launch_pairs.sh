#!/usr/bin/env bash
# ONE command on the LOGIN node: which model is the better PLANNER (P-LLM) and which the better READER (Q-LLM)?
#
#   ./cluster/launch_pairs.sh
#
# Four GPU jobs, one per pairing, each a full ≤ 2 h run with profile `pair2h` in its own folder ~/0_runs/<name>:
#   pq-qwen-gemma   P = Qwen3-8B,     Q = Gemma 3 12B
#   pq-gemma-qwen   P = Gemma 3 12B,  Q = Qwen3-8B
#   pq-qwen-qwen    P = Q = Qwen3-8B
#   pq-gemma-gemma  P = Q = Gemma 3 12B
# The attacker is mistral:7b in all four (no model attacks itself) and the guards are the same, so only P and Q
# differ. teachingq runs at most 2 of your jobs at a time: the first two start now (the mixed pairs: the riskier
# setup — two models share one 16 GB GPU — runs first), the other two wait and start when those end, so all four
# finish ≈ 4 h after the start (more if the GPUs are busy).
# Watch in Telegram (/status, /log pq-gemma, /compare) — the bot is started below.
# At the end (or any time, on what has finished):
#   .venv/bin/python scripts/compare_runs.py ~/0_runs/pq-qwen-qwen ~/0_runs/pq-gemma-gemma ~/0_runs/pq-qwen-gemma ~/0_runs/pq-gemma-qwen
set -euo pipefail
cd "$(dirname "$0")/.."
export PROFILE=pair2h WALLTIME_H=2 ONE_GPU=1 NO_SMOKE=1 NO_RESUBMIT=1 PROGRESS_MIN="${PROGRESS_MIN:-30}"
PAIRS=(pq-qwen-gemma pq-gemma-qwen pq-qwen-qwen pq-gemma-gemma)
first=1
for name in "${PAIRS[@]}"; do
  echo; echo "######## $name"
  if [ "$first" = 1 ]; then first=0; JOBNAME="$name" ./cluster/launch_variant.sh "$name"
  else SKIP_TESTS=1 JOBNAME="$name" ./cluster/launch_variant.sh "$name"; fi
done

echo; echo "######## Telegram bot (/status, /qstat, /log, /compare on your phone)"
./cluster/telegram_bot.sh start || echo "  (bot not started — run ./cluster/telegram_bot.sh start by hand)"

echo
echo "4 jobs submitted (2 run now, 2 wait). Watch: qstat -u $USER  or /status in Telegram"
echo "compare at the end:"
echo "  .venv/bin/python scripts/compare_runs.py $(printf '~/0_runs/%s ' "${PAIRS[@]}")"
