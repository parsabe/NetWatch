#!/usr/bin/env bash
# ONE command for everything: setup → model server → tests → experiments → figures → notebook → model-written
# LaTeX/PDF report in report/.
#
#   ./run_all.sh                                   # quick: tests, theory figures, smoke test, report (~30 min)
#   ./run_all.sh --level pilot                     # + Stage 1 pilot and a paired AgentDojo mini-run (hours)
#   ./run_all.sh --level full                      # + all §11 experiments at full size (days on one GPU)
#   ./run_all.sh --report-only                     # just (re)write the report from what exists
#
# Report model (analyses everything and writes the report). Default: your Qwen3.6-35B-A3B served by colibri
# (openai/qwen3.6-colibri @ http://127.0.0.1:8000/v1; started automatically if it is not running).
#   --report-model ollama_chat/qwen3:8b --report-api-base http://localhost:11434     (any other model)
#   --vision                                       # the model also looks at every figure image
# Other: --skip-setup  --redteam (install PyRIT/garak)  --set key=value (any params.yaml override, repeatable)
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
UV="$HOME/.local/bin/uv"
LEVEL=quick; R_MODEL=""; R_BASE=""; SKIP_SETUP=0; REPORT_ONLY=0; REDTEAM=""; REPORT_ARGS=(); SETS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --level) LEVEL="$2"; shift;;
    --report-model) REPORT_ARGS+=(--report-model "$2"); R_MODEL="$2"; shift;;
    --report-api-base) REPORT_ARGS+=(--report-api-base "$2"); R_BASE="$2"; shift;;
    --vision) REPORT_ARGS+=(--vision);;
    --skip-setup) SKIP_SETUP=1;;
    --report-only) REPORT_ONLY=1;;
    --redteam) REDTEAM=--redteam;;
    --set) SETS+=(--set "$2"); shift;;
    -h|--help) sed -n 2,14p "$0"; exit 0;;
    *) echo "unknown option $1"; exit 2;;
  esac; shift
done
case "$LEVEL" in quick|pilot|full) ;; *) echo "--level must be quick|pilot|full"; exit 2;; esac

mkdir -p report results/logs
LOG="report/run_all.log"
exec > >(tee -a "$LOG") 2>&1
export MLFLOW_DISABLE_AGENT_HINT=1
STEP=0
step() { STEP=$((STEP+1)); printf '\n\033[1;32m==== [%d] %s ====\033[0m  (%s)\n' "$STEP" "$1" "$(date +%H:%M:%S)"
         [ $# -ge 2 ] && printf '     what: %s\n' "$2"; [ $# -ge 3 ] && printf '\033[35m     who:  %s\033[0m\n' "$3"; return 0; }
PY() { "$UV" run --no-sync python "$@"; }
FAILED=()
try() { "$@" || { echo "!! step failed: $*"; FAILED+=("$*"); }; }   # keep going; the report records what exists
gated_ok() { PY - <<'EOF'
from horizon.config import load_config
from horizon.direct.guards import GatedModelError, PromptGuardInput
try:
    PromptGuardInput(load_config().direct).check("hello"); print("yes")
except GatedModelError:
    print("no")
EOF
}
ensure_report_model() {
  # The report model must answer before the report step; the default (colibri) is started if it is down.
  local cfgline model base cdir mdir mid
  cfgline=$(PY -c "from horizon.config import load_config; r=load_config().report; print(r['model'], r['api_base'], r['colibri_dir'], r['colibri_model_dir'], r['colibri_model_id'])")
  read -r model base cdir mdir mid <<< "$cfgline"
  model="${R_MODEL:-$model}"; base="${R_BASE:-$base}"
  cdir="${cdir/#\~/$HOME}"; mdir="${mdir/#\~/$HOME}"
  if [[ "$model" == ollama* ]]; then
    curl -s -m 5 "$base/api/version" >/dev/null && { echo "report model $model @ $base: up"; return 0; }
  else
    curl -s -m 5 "$base/models" >/dev/null && { echo "report model $model @ $base: up"; return 0; }
  fi
  if [[ "$model" == "openai/$mid" && "$base" == *127.0.0.1:8000* && -x "$cdir/c/coli" ]]; then
    echo "starting colibri: $mdir as $mid on 127.0.0.1:8000 (log: results/logs/colibri.log)"
    (cd "$cdir/c" && nohup ./coli serve --model "$mdir" --host 127.0.0.1 --port 8000 --model-id "$mid" \
       > "$ROOT/results/logs/colibri.log" 2>&1 &)
    for _ in $(seq 1 360); do curl -s -m 5 "$base/models" >/dev/null && { echo "colibri up"; return 0; }; sleep 5; done
  fi
  echo "!! report model $model @ $base is not reachable"; return 1
}
echo "run_all: level=$LEVEL  $(date)"

if [ "$REPORT_ONLY" = 0 ]; then
  if [ "$SKIP_SETUP" = 0 ]; then step "install uv, Python 3.11, pinned packages, CaMeL, Ollama, Qwen3-8B, data" "scripts/setup.sh"; ./scripts/setup.sh $REDTEAM || exit 1; fi
  step "start the local LLM server used by every AI step" "Ollama (GPU) + role proxy (scripts/openai_role_proxy.py)"
  curl -s localhost:11434/api/version >/dev/null || ./scripts/serve_local.sh

  step "check every formula against the slide examples (no AI)" "pytest · tests/";               try "$UV" run --no-sync pytest -q
  step "draw the figures from params.yaml inputs (SIMULATED, labelled)" "matplotlib · scripts/make_figures.py";           try PY scripts/make_figures.py --theory "${SETS[@]}"
  step "does each layer fire once? quarantine+vote, policy, Z3, canary, guards" "Qwen3-8B · Z3 · scripts/smoke_test.py";       try PY scripts/smoke_test.py "${SETS[@]}"

  GATED=$(gated_ok | tail -1)
  NOGUARD=(); [ "$GATED" = yes ] || NOGUARD=(--set flags.use_firewall=false --set flags.use_output_guard=false)
  [ "$GATED" = yes ] || echo "Meta guard models are gated → direct-layer flags switched off for this run"

  if [ "$LEVEL" = pilot ]; then
    step "small paired run: every transform τ on the same attacks + clean docs, then CP / DRO / π*" "Qwen3-8B (target) · scipy · cvxpy/Clarabel · eval/stage1_runner.py"
    try PY eval/stage1_runner.py --split fit --pilot --static --clean --adaptive --analyze "${SETS[@]}"
    step "same tasks + attacks with and without Horizon" "CaMeL + Qwen3-8B (P-LLM, Q-LLM) · AgentDojo · eval/agentdojo_runner.py"
    for ab in none stage1+2; do
      try PY eval/agentdojo_runner.py --split report --ablation "$ab" --limit-user 3 --limit-injection 2 \
            --attacks important_instructions --set flags.use_conformal=false "${SETS[@]}"
    done
    step "figures from the pilot results" "matplotlib · scripts/make_figures.py";  try PY scripts/make_figures.py --stage1-run fit_pilot --out figures/pilot "${SETS[@]}"
  fi

  if [ "$LEVEL" = full ]; then
    step "all transforms on the fit split + adaptive PAIR attacker → τ*, π* (Sion V1)" "Qwen3-8B (target + attacker) · scipy · cvxpy/Clarabel"
    try PY eval/stage1_runner.py --split fit --static --clean --adaptive --analyze "${SETS[@]}"
    step "evaluate the choices on unseen data (never report on the choosing split)" "Qwen3-8B · eval/stage1_runner.py"
    try PY eval/stage1_runner.py --split report --static --clean --adaptive --analyze "${SETS[@]}"
    step "pick the agreement threshold λ̂ on the calibration split (CRC)" "Qwen3-8B readers · horizon/stage2/conformal.py"
    try PY eval/agentdojo_runner.py --calibrate --ablation stage1+2 "${SETS[@]}"
    step "none · stage1 · stage2 · stage1+2 · full on the same tasks/attacks → G, C, F, Sion LP" "CaMeL + Qwen3-8B · AgentDojo · scipy linprog"
    for ab in none stage1 stage2 stage1+2 full; do
      try PY eval/agentdojo_runner.py --split report --ablation "$ab" "${NOGUARD[@]}" "${SETS[@]}"
    done
    step "attacker LLM rewrites injections against the full pipeline" "Qwen3-8B (attacker) · AgentDojo"
    try PY eval/agentdojo_runner.py --split report --ablation full --adaptive "${NOGUARD[@]}" "${SETS[@]}"
    if [ "$GATED" = yes ]; then step "jailbreak sets (incl. HackAPrompt) vs benign sets through the guards" "Prompt Guard 2 + Llama Guard · eval/jailbreak_runner.py"; try PY eval/jailbreak_runner.py --detect "${SETS[@]}"; fi
    step "figures from the report-split results" "matplotlib · scripts/make_figures.py";  try PY scripts/make_figures.py --stage1-run report --out figures/results "${SETS[@]}"
  fi

  step "rebuild and run Horizon.ipynb" "nbclient · scripts/build_notebook.py"
  try PY scripts/build_notebook.py --execute
fi

step "the report model reads every figure/CSV/result, writes LaTeX; numbers are checked" "colibri (Qwen3.6-35B-A3B) · xelatex · scripts/make_report.py"
try ensure_report_model
try PY scripts/make_report.py "${REPORT_ARGS[@]}" "${SETS[@]}"

echo
if [ ${#FAILED[@]} -gt 0 ]; then printf '\033[1;31mSteps that failed (see %s):\033[0m\n' "$LOG"; printf '  - %s\n' "${FAILED[@]}"; fi
[ -f report/report.pdf ] && echo "REPORT: $ROOT/report/report.pdf"
