#!/usr/bin/env bash
# Start local model serving: Ollama (GPU) + the developer→system role proxy.
# Context length / KV-cache settings come from config/params.yaml (models.*);
# HORIZON_PARALLEL overrides the parallel slots (eval/campaign.py sets it from the profile).
set -euo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"
OLLAMA="${OLLAMA_BIN:-$HOME/.local/ollama/bin/ollama}"
get() { "$UV" run --no-sync python -c "from horizon.config import load_config; print(load_config().models['$1'])"; }
mkdir -p results/logs
pkill -f "ollama [s]erve" 2>/dev/null || true
OLLAMA_CONTEXT_LENGTH="$(get ollama_context_length)" OLLAMA_FLASH_ATTENTION=1 \
OLLAMA_KV_CACHE_TYPE="$(get ollama_kv_cache_type)" OLLAMA_NUM_PARALLEL="${HORIZON_PARALLEL:-$(get parallel_requests)}" \
OLLAMA_HOST=127.0.0.1:11434 \
  nohup "$OLLAMA" serve > results/logs/ollama.log 2>&1 &
until curl -s localhost:11434/api/version >/dev/null; do sleep 1; done
pkill -f "openai_role_[p]roxy.py" 2>/dev/null || true
nohup "$UV" run --no-sync python scripts/openai_role_proxy.py > results/logs/proxy.log 2>&1 &
until curl -s "localhost:$(get proxy_port)/v1/models" >/dev/null; do sleep 1; done
echo "ollama + proxy up"
