# Horizon — RQ1 implementation (indirect prompt injection)

Two-stage defense against **indirect** prompt injection, plus an off-the-shelf layer for direct injection and
jailbreaks. The math follows the slides in `Meetings/20.10/combined_presentation.pdf`; every math function
cites its slide number. **Start with `Horizon.ipynb`**: it walks through every build step with live demos and
the saved results. `REPORT.md` has the status report.

## Layout

```
config/params.yaml        every parameter (illustrative, sweepable; CLI: --set key=value)
config/policy.yaml        deterministic policy table (Slide 38), proven noninterferent by Z3 at startup
horizon/
  keys.py                 CSPRNG keys (`secrets`)
  llm.py                  LiteLLM client + on-disk cache
  stage1/                 transforms, prompts, canary A(δ,κ), utility (token-F1), stats (CP, DRO, combined
                          risk, choice rule, McNemar, bootstrap), sion (V1 SOCP), adaptive attacker, corpus
  stage2/                 values (labels), schemas (parser, |S|), quarantine × m, vote (+Hoeffding), policy,
                          z3_check, conformal (CRC), loss, leakage, sion_lp, horizon_value, camel_adapter
  direct/                 LlamaFirewall/Prompt Guard 2, Llama Guard, canary
  pipeline.py             end-to-end AgentDojo pipeline, all ablation flags
  theory.py               theory-mode inputs for figures
eval/                     stage1_runner, agentdojo_runner, jailbreak_runner (§11)
scripts/                  serve_local.sh, openai_role_proxy.py, download_data.py, make_splits.py,
                          camel_baseline.py, smoke_test.py, guarded_server.py, make_figures.py, build_notebook.py
tests/                    §9 unit tests (the only place slide values appear) + grep check
third_party/              CaMeL reference implementation (unmodified, Apache-2.0)
data/                     README.md = data card; raw/ (git-ignored), processed/, splits/
results/                  MLflow (results/mlflow.db), runs, logs (git-ignored)
figures/                  F1–F18 as PDF + PNG + CSV (theory mode); figures/pilot/ from the Stage 1 pilot
```

## One command

```bash
./run_all.sh                         # setup + tests + theory figures + smoke test + notebook + PDF report
./run_all.sh --level pilot           # + Stage 1 pilot and a paired AgentDojo mini-run
./run_all.sh --level full            # + every §11 experiment at full size
./run_all.sh --report-only --report-model ollama_chat/<name> --report-api-base http://<colibri>:11434
```

Everything ends up in `report/`: `report.pdf` / `report.tex` (written by the analysis model, with the
project tree, every figure, result tables computed from the files, and the model's analysis; numbers in the
prose are checked against the data and unmatched ones are marked red), the copied figures and CSVs,
the model's raw analyses (`analysis/*.json`), `tests.xml` and `run_all.log`. Setup alone: `./scripts/setup.sh`.

## Start here: `./horizon.sh`

```bash
./horizon.sh          # numbered menu (no commands to remember)
./horizon.sh help     # every project command: setup, test, run quick|pilot|full, report, figures, notebook, status
```

Menu: **1** attack a document (Stage 1) · **2** attack an agent (Stage 2, you approve risky actions) ·
**3** try a jailbreak (direct layer) · **4** self-test · **5** results & reports · **6** settings ·
**7** help & glossary · **8** command mode. Every demo shows numbered plain-language steps; switch
*Settings → detail* to also see the maths. Attack texts: `demo/untrusted_samples.txt` (indirect) and
`demo/jailbreak_samples.txt` (direct). Every step is logged to `results/shell/session_*.jsonl`.

## Step by step

```bash
~/.local/bin/uv sync --extra direct            # pinned (uv.lock); add --extra redteam for PyRIT/garak
./scripts/serve_local.sh                       # Ollama (qwen3:8b) + developer→system role proxy
uv run pytest -q                               # §9 unit tests
uv run python scripts/make_figures.py --theory # all theory-mode figures
uv run python scripts/smoke_test.py            # does each layer fire?
```

Data: `uv run python scripts/download_data.py` (gated sets only with `--include-gated`), then
`uv run python scripts/make_splits.py` and `uv run python eval/agentdojo_runner.py --make-splits`.

## Ablation flags (`flags:` in params.yaml)

`use_firewall, use_stage1, stage1_variant | stage1_mix, use_stage2, m, use_vote, use_typing, use_policy,
use_conformal, use_output_guard, use_canary`. Preset ablations `none | stage1 | stage2 | stage1+2 | full` in
`eval/agentdojo_runner.py`.

## Decisions recorded in code

* Per-run independent keys in the quarantine (Slide 35; Slide 34 inconsistent).
* `attack_budget` (Slide 20) and `leakage_cap_bits` (Slide 41) are distinct names.
* Utility metric: SQuAD token-F1 (author decision).
* CRC: ask user iff vote agreement s(x) ≥ λ, else DENY; λ̂ = smallest feasible λ (author decision).
* Leakage: hard per-configuration cap in Π (author decision); side-channel term is a flagged placeholder (0).
* V_Horizon: variance from per-attack losses, mean from CP bounds — *pending author confirmation* (Slide 42
  as written gives Var = 0).
* Report-split values are always evaluated at choices made on the fit split.
