#!/usr/bin/env bash
# A SECOND campaign with other models, next to the main one, in its own copy of the project.
#
#   ./cluster/launch_variant.sh gemma3-12b            # uses config/variants/gemma3-12b.yaml
#   NO_SMOKE=1 ./cluster/launch_variant.sh gemma3-12b # same options as cluster/launch_bounded.sh
#
# The copy lives in ~/0_runs/<name>: its own results/, report/, logs, MLflow DB and smoke folder, so the two runs
# never touch each other's files. It shares (read-only) the Python environment (.venv), the datasets (data/raw), the
# Ollama models (~/.ollama) and the Hugging Face cache. Its settings are config/variants/<name>.yaml, copied to
# config/local.yaml there (merged over params.yaml). Telegram messages are tagged with the run's model.
# Running it again re-syncs the code and continues the same run (results are kept); FRESH=1 starts over.
# At the end: .venv/bin/python scripts/compare_runs.py . ~/0_runs/<name>
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:?usage: $0 <variant name>   (config/variants/<name>.yaml)}"
SRC="$PWD"
VAR="config/variants/$NAME.yaml"
[ -f "$VAR" ] || { echo "no $VAR"; exit 1; }
DST="${RUNS_DIR:-$HOME/0_runs}/$NAME"
mkdir -p "$DST"
# FRESH=1: start the run from zero. The previous results, reports, logs and LLM cache move to
# $DST/archive/<time>/ (nothing is deleted), so a finished run is not just "resumed" with nothing left to do.
if [ "${FRESH:-0}" = 1 ]; then
  old=(); for x in results report horizon_live.log horizon.log horizon_smoke_live.log; do [ -e "$DST/$x" ] && old+=("$x"); done
  if [ ${#old[@]} -gt 0 ]; then
    arc="$DST/archive/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$arc"
    for x in "${old[@]}"; do mv "$DST/$x" "$arc/"; done
    echo "FRESH: previous run moved to $arc"
  fi
fi
# code + config only; results, reports, logs and per-run state stay where they are
rsync -a --delete --exclude .git --exclude .venv --exclude results --exclude report --exclude mlartifacts --exclude 'horizon*.log' \
      --exclude data/raw --exclude config/local.yaml --exclude config/repairs.yaml --exclude .pytest_cache \
      --exclude __pycache__ --exclude archive "$SRC/" "$DST/"
ln -sfn "$SRC/.venv" "$DST/.venv"
ln -sfn "$SRC/data/raw" "$DST/data/raw"
cp "$VAR" "$DST/config/local.yaml"
[ -f "$DST/config/repairs.yaml" ] || printf '# written by eval/campaign.py (delete to undo)\n{}\n' > "$DST/config/repairs.yaml"
echo "run folder: $DST  (settings: $VAR → config/local.yaml)"
cd "$DST"
JOBNAME="${JOBNAME:-${NAME:0:9}}" exec ./cluster/launch_bounded.sh
