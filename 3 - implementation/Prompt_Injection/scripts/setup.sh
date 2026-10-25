#!/usr/bin/env bash
# One-time (idempotent) setup of everything Horizon needs. Safe to re-run: every step is skipped when done.
#   ./scripts/setup.sh [--redteam] [--force-data]
# Installs: uv, Python 3.11, pinned deps (uv.lock), CaMeL (pinned commit), Ollama (user dir, no sudo),
# the models named in config/params.yaml, all non-gated datasets, the splits, and a Jupyter kernel.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
REDTEAM=0; FORCE_DATA=0
for a in "$@"; do case "$a" in --redteam) REDTEAM=1;; --force-data) FORCE_DATA=1;; esac; done

say() { printf '\n\033[1;34m[setup]\033[0m %s\n' "$*"; }
UV="$HOME/.local/bin/uv"
OLLAMA_VERSION="v0.40.11"
CAMEL_COMMIT="f083b6b396399d3b3c7f2ddaf613a5945eaf32d8"

say "uv"
if [ ! -x "$UV" ]; then curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path; fi
"$UV" --version

say "CaMeL reference implementation (pinned, unmodified)"
if [ ! -d third_party/camel-prompt-injection ]; then
  mkdir -p third_party
  git clone -q https://github.com/google-research/camel-prompt-injection.git third_party/camel-prompt-injection
fi
git -C third_party/camel-prompt-injection checkout -q "$CAMEL_COMMIT"

say "Python 3.11 + pinned dependencies"
"$UV" python install 3.11
EXTRAS=(--extra direct); [ "$REDTEAM" = 1 ] && EXTRAS+=(--extra redteam)
"$UV" sync --frozen "${EXTRAS[@]}"
PY() { "$UV" run --no-sync python "$@"; }
cfg() { PY -c "from horizon.config import load_config; c=load_config(); print($1)"; }

say "Ollama (user install, GPU)"
OLLAMA="$HOME/.local/ollama/bin/ollama"
if [ ! -x "$OLLAMA" ]; then
  mkdir -p "$HOME/.local/ollama"
  curl -sL "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-linux-amd64.tar.zst" \
    | tar --zstd -x -C "$HOME/.local/ollama"
fi
if ! curl -s localhost:11434/api/version >/dev/null; then ./scripts/serve_local.sh; else
  echo "model server already running (left untouched)"
  curl -s "localhost:$(cfg "c.models['proxy_port']")/v1/models" >/dev/null || \
    (nohup "$UV" run --no-sync python scripts/openai_role_proxy.py > results/logs/proxy.log 2>&1 &)
fi

say "models"
for m in $(cfg "' '.join(sorted({c.models[k].split('/',1)[1] for k in ['p_llm','q_llm','attacker']}))"); do
  "$OLLAMA" list | grep -q "^$m " || "$OLLAMA" pull "$m"
done
RM=$(cfg "c.report['model']"); RB=$(cfg "c.report['api_base']")
if [[ "$RM" == ollama* && "$RB" == *localhost* ]]; then
  n="${RM#*/}"; "$OLLAMA" list | grep -q "^$n " || "$OLLAMA" pull "$n"
fi

say "datasets + data card"
if [ "$FORCE_DATA" = 1 ] || [ ! -f data/data_card.json ]; then PY scripts/download_data.py; else echo "data present"; fi
[ -f data/processed/stage1_fit_attacks.jsonl ] || PY scripts/make_splits.py
[ -f data/splits/agentdojo.json ] || PY eval/agentdojo_runner.py --make-splits

say "Jupyter kernel"
PY -m ipykernel install --user --name horizon --display-name "Python (horizon)" >/dev/null && echo ok

say "checks"
command -v latexmk >/dev/null && command -v xelatex >/dev/null && echo "LaTeX: ok" || echo "LaTeX: MISSING (install texlive-xetex + latexmk to build the PDF report)"
T="$HOME/.cache/huggingface/token"
for repo in meta-llama/Llama-Prompt-Guard-2-86M meta-llama/Llama-Guard-3-1B; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $(cat "$T" 2>/dev/null)" \
         "https://huggingface.co/$repo/resolve/main/config.json")
  [ "$code" = 200 ] || [ "$code" = 302 ] && echo "$repo: accessible" || echo "$repo: GATED ($code) — accept the license on huggingface.co"
done
say "setup complete"
