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
# Running it again re-syncs the code and continues the same run (results are kept).
# At the end: .venv/bin/python scripts/compare_runs.py . ~/0_runs/<name>
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:?usage: $0 <variant name>   (config/variants/<name>.yaml)}"
SRC="$PWD"
VAR="config/variants/$NAME.yaml"
[ -f "$VAR" ] || { echo "no $VAR"; exit 1; }
DST="${RUNS_DIR:-$HOME/0_runs}/$NAME"
mkdir -p "$DST"
# code + config only; results, reports, logs and per-run state stay where they are
rsync -a --delete --exclude .venv --exclude results --exclude report --exclude mlartifacts --exclude 'horizon*.log' \
      --exclude data/raw --exclude config/local.yaml --exclude config/repairs.yaml --exclude .pytest_cache \
      --exclude __pycache__ "$SRC/" "$DST/"
ln -sfn "$SRC/.venv" "$DST/.venv"
ln -sfn "$SRC/data/raw" "$DST/data/raw"
cp "$VAR" "$DST/config/local.yaml"
[ -f "$DST/config/repairs.yaml" ] || printf '# written by eval/campaign.py (delete to undo)\n{}\n' > "$DST/config/repairs.yaml"
echo "run folder: $DST  (settings: $VAR → config/local.yaml)"
cd "$DST"
JOBNAME="${JOBNAME:-${NAME:0:9}}" exec ./cluster/launch_bounded.sh
