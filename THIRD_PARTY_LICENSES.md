# Third-party licenses

Horizon (RQ1 of NetWatch) is MIT-licensed (see `LICENSE`). Everything below belongs to its authors and keeps its
own license. None of the model weights or datasets are stored in this repository: the setup scripts download them
(`scripts/download_data.py`, `run_campaign.sh --prepare`). Full license texts are
in `LICENSES/`: `Apache-2.0.txt`, `MIT-BIPIA.txt` and `MIT-JailbreakBench.txt` as examples of the MIT text. For any
other component, the text that ships with that component applies.

Versions are the ones locked in `uv.lock` on 1 Nov 2026.

## Software

| Component | Use | License |
|---|---|---|
| CaMeL reference implementation (google-research/camel-prompt-injection) | Stage 2 interpreter; vendored unmodified in `third_party/` (not committed) | Apache-2.0 |
| AgentDojo 0.1.35 | agent benchmark (workspace, slack, travel) | MIT |
| PyRIT 1.1.0 | PAIR / TAP / Crescendo jailbreaks, XPIA | MIT |
| garak 0.17.1 | LLM vulnerability probes | Apache-2.0 |
| Ollama | local model server | MIT |
| LiteLLM 1.106.3 | model client | MIT |
| pydantic-ai 0.2.12, pydantic 2 | CaMeL's LLM API, typed values | MIT |
| PyTorch 2.14.1 | guard models | BSD-3-Clause (see package metadata) |
| Hugging Face transformers 5.19, datasets 5.1, huggingface-hub 1.33 | guard models, data download | Apache-2.0 |
| cvxpy 1.9.3, Clarabel 0.11.1 | Stage 1 minimax / π* solver | Apache-2.0 |
| z3-solver 5.3.1 | SMT proof of the Stage 2 policy | MIT |
| MLflow 3.17, OpenTelemetry API | experiment tracking | Apache-2.0 |
| NumPy, SciPy, pandas, statsmodels | statistics | BSD-3-Clause |
| matplotlib 3.11 | figures and report charts | matplotlib License (PSF-based) |
| PyYAML | configuration | MIT |
| pytest | tests | MIT |
| nbformat, nbclient, ipykernel | notebook | BSD-3-Clause |

## Models

| Model | Role | License |
|---|---|---|
| Qwen3-8B (Alibaba Qwen) | planner, quarantined reader, victim, attacker (run 1) | Apache-2.0 |
| Gemma 3 12B (Google) | planner, quarantined reader, victim, attacker (run 2) | Gemma Terms of Use (https://ai.google.dev/gemma/terms) |
| Mistral 7B Instruct (Mistral AI) | attacker in the four planner/reader pairing runs (`config/variants/pq-*.yaml`) | Apache-2.0 |
| protectai/deberta-v3-base-prompt-injection-v2 | input guard | Apache-2.0 |
| Qwen/Qwen3Guard-Gen-0.6B | output guard (run 1) | Apache-2.0 |
| ibm-granite/granite-guardian-3.1-2b | output guard (run 2) | Apache-2.0 |

## Datasets

| Dataset | Source | License |
|---|---|---|
| BIPIA | github.com/microsoft/BIPIA | MIT |
| LLMail-Inject | huggingface.co/datasets/microsoft/llmail-inject-challenge | MIT |
| HackAPrompt | huggingface.co/datasets/hackaprompt/hackaprompt-dataset (gated) | MIT |
| Tensor Trust | huggingface.co/datasets/qxcv/tensor-trust | not stated on the dataset card, see the source repository |
| JailbreakBench JBB-Behaviors | huggingface.co/datasets/JailbreakBench/JBB-Behaviors | MIT |
| HarmBench behaviours | github.com/centerforaisafety/HarmBench | MIT |
| deepset prompt-injections | huggingface.co/datasets/deepset/prompt-injections | Apache-2.0 |
| Lakera gandalf_ignore_instructions | huggingface.co/datasets/Lakera/gandalf_ignore_instructions | MIT |
| XSTest v2 | huggingface.co/datasets/natolambert/xstest-v2-copy | CC-BY-4.0 |
| Stanford Alpaca | huggingface.co/datasets/tatsu-lab/alpaca | CC-BY-NC-4.0 (non-commercial use only) |

## Reference papers

The PDFs in `Ref/` are the work of their authors and publishers. They are kept for private study and are not
licensed under this repository's MIT license.
