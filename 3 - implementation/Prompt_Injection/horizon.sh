#!/usr/bin/env bash
# Interactive Horizon shell: talk to the protected AI and watch every stage live.
#   ./horizon.sh                                  # interactive
#   ./horizon.sh --set flags.m=7                  # any params.yaml override
#   ./horizon.sh -c "load all" -c "Summarize this document"   # scripted commands, then exit
# Starts the local model server first if it is not running.
set -uo pipefail
cd "$(dirname "$0")"
UV="$HOME/.local/bin/uv"
curl -s -m 3 localhost:11434/api/version >/dev/null || ./scripts/serve_local.sh
export MLFLOW_DISABLE_AGENT_HINT=1
exec "$UV" run --no-sync python scripts/horizon_shell.py "$@"
