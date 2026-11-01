#!/usr/bin/env bash
# ONE command on the LOGIN node: check → archive the old results → check the budget → submit smoke + 2 GPU lanes.
#
#   ./cluster/launch_bounded.sh                # tests, archive, plan, smoke job, then lanes A and B (after smoke passes)
#   NO_SMOKE=1 ./cluster/launch_bounded.sh     # skip the smoke job: lanes start right away
#   ONE_GPU=1  ./cluster/launch_bounded.sh     # one job runs every phase (no lanes)
#   SKIP_TESTS=1 ./cluster/launch_bounded.sh   # skip the offline unit tests
#
# Profile `bounded`: 2,702 certain tests, at most 3,038 (hard range 2,000–4,000). Lane A = Stage 1, CRC calibration,
# the defended ablations / XPIA, adaptive attacker. Lane B = guards, 'none' ablation / XPIA, PyRIT, garak.
# Telegram: start message with job ids here; then milestones, failures and an hourly progress overview from lane A
# (scripts/progress.py; PROGRESS_MIN=… to change, 0 = off). Any time: .venv/bin/python scripts/progress.py
#   PROFILE=quick2h WALLTIME_H=2 ONE_GPU=1 NO_SMOKE=1 NO_RESUBMIT=1 ./cluster/launch_bounded.sh   # (launch_quick2h.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${PROFILE:-bounded}"                                   # campaign profile (config: campaign.profiles)
WALLTIME_H="${WALLTIME_H:-24}"                                  # PBS walltime of the campaign job(s), hours
UV="${UV:-$HOME/.local/bin/uv}"
PY=("$UV" run --no-sync python)
say() { echo; echo "==== $*"; }
tg() { "${PY[@]}" -m horizon.notify "$1" >/dev/null 2>&1 || echo "  (Telegram message not sent)"; }

say "1. offline unit tests (no GPU, no model server)"
if [ -z "${SKIP_TESTS:-}" ]; then
  "${PY[@]}" -m pytest -q tests/test_starvation.py tests/test_reliability.py tests/test_campaign.py \
      tests/test_math_upgrades.py tests/test_stage2.py tests/test_integration_endpoints.py \
    || { echo "!! tests failed — nothing submitted"; exit 1; }
else
  echo "  skipped (SKIP_TESTS=1)"
fi

say "2. archive results of earlier campaigns with another profile than '$PROFILE'"
PREV=$("${PY[@]}" - <<'EOF'
import json, pathlib
st = [json.loads(p.read_text()) for p in pathlib.Path("results/campaign").glob("state*.json")]
print(next((s.get("profile") for s in st if s.get("profile")), ""))
EOF
)
if [ "$PREV" = "$PROFILE" ]; then
  echo "  results/ already belong to a '$PROFILE' campaign → kept (the jobs continue it)"
elif [ -n "$(ls -d results/stage1 results/stage2 results/testing results/campaign results/autotune 2>/dev/null)" ]; then
  DEST="results/archive/before_${PROFILE}_$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$DEST"
  for d in stage1 stage2 testing campaign autotune; do [ -e "results/$d" ] && mv "results/$d" "$DEST/"; done
  [ -e report/campaign ] && mv report/campaign "$DEST/report_campaign"
  echo "  moved to $DEST (profile was '${PREV:-unknown}')"
else
  echo "  nothing to archive"
fi

say "3. plan and test budget"
PLAN=$("${PY[@]}" eval/campaign.py --plan --profile "$PROFILE")
echo "$PLAN"
echo "$PLAN" | grep -q "budget OK" || { echo "!! budget check failed — nothing submitted"; exit 1; }

say "4. submit"
PAR=$("${PY[@]}" -c "import sys; from horizon.config import load_config; print(load_config().campaign['profiles'][sys.argv[1]]['parallel_requests'])" "$PROFILE")
echo "  model: $("${PY[@]}" -c "from horizon.notify import run_tag; print(run_tag())") · Ollama slots per GPU: $PAR · folder: $PWD"
JN="${JOBNAME:-horizon}"                                         # PBS job name (qstat shows it)
DEP=()
WT=(-l "walltime=$(printf '%02d' "$WALLTIME_H"):00:00")          # job walltime = WALLTIME_H (the script stops 15 min early)
VARS="PARALLEL=$PAR,PROFILE=$PROFILE,WALLTIME_H=$WALLTIME_H,PROGRESS_MIN=${PROGRESS_MIN:-60}${NO_RESUBMIT:+,NO_RESUBMIT=1}"
if [ -z "${NO_SMOKE:-}" ]; then
  SMOKE=$(qsub -N "${JN:0:9}-smoke" cluster/horizon_smoke.pbs)
  echo "  smoke job: $SMOKE (≈ 1–3 h, in a copy at ~/0_smoke_$(basename "$PWD"))"
  DEP=(-W "depend=afterok:$SMOKE")
fi
if [ -n "${ONE_GPU:-}" ]; then
  A=$(qsub "${DEP[@]}" "${WT[@]}" -N "${JN:0:15}" -v "$VARS" cluster/horizon_campaign.pbs); B=""
  echo "  campaign (one GPU, all phases): $A"
else
  A=$(qsub "${DEP[@]}" "${WT[@]}" -N "${JN:0:13}-A" -v "LANE=A,$VARS" cluster/horizon_campaign.pbs)   # no LANE = EVERY phase
  B=$(qsub "${DEP[@]}" "${WT[@]}" -N "${JN:0:13}-B" -v "LANE=B,$VARS" cluster/horizon_campaign.pbs)
  echo "  lane A: $A    lane B: $B"
fi
[ -n "${SMOKE:-}" ] && echo "  the lanes are held until the smoke job ends with exit 0; if it fails, PBS drops them"

tg "🚀 Campaign '$PROFILE' submitted from $(basename "$PWD") ($(echo "$PLAN" | grep -o "[0-9,]* certain tests, at most [0-9,]*") · walltime ${WALLTIME_H} h)
${SMOKE:+smoke: $SMOKE (lanes wait for it)
}lane A: $A${B:+
lane B: $B}
You will get: milestones, failures, and a progress overview every ${PROGRESS_MIN:-60} min."

say "watch"
echo "  qstat -u $USER                                   # job states"
echo "  tail -f horizon_live*.log                         # live logs (one GPU: horizon_live.log; lanes: _A / _B)"
echo "  .venv/bin/python scripts/progress.py             # tests done per phase"
