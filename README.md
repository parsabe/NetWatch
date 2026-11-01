# Horizon — RQ1 implementation (indirect prompt injection)

[NetWatch](https://github.com/parsabe/NetWatch) · **RQ1 Prompt injection** · [RQ2 VectorDB Flame](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) · [RQ3 BlackWall](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) · [Documentation](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION)

This branch holds only the RQ1 folder of [NetWatch](https://github.com/parsabe/NetWatch), at the branch root:
`git clone -b RQ1-Prompt-Injection https://github.com/parsabe/NetWatch.git` gives you a runnable project.

Two-stage defense against **indirect** prompt injection, plus an off-the-shelf layer for direct injection and
jailbreaks. The math follows the slides in [`Meetings/20.10/combined_presentation.pdf`](https://github.com/parsabe/NetWatch/blob/master/Meetings/20.10/combined_presentation.pdf) (on `master`); every math function
cites its slide number. **Start with `Horizon.ipynb`**: it walks through every build step with live demos and
the saved results. [`report/campaign/campaign_report.md`](report/campaign/campaign_report.md) has the latest campaign report.

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
  direct/                 DeBERTa injection classifier (input), Qwen3Guard (output), canary
  pipeline.py             end-to-end AgentDojo pipeline, all ablation flags
  theory.py               theory-mode inputs for figures
eval/                     stage1_runner, agentdojo_runner, jailbreak_runner (§11: detect · PyRIT PAIR/TAP/Crescendo ·
                          PyRIT XPIA (indirect) · garak direct + latentinjection)
horizon/redteam/          PyRIT XPIA adapter: plant δ → process with a Horizon configuration → canary indicator
scripts/                  serve_local.sh, openai_role_proxy.py, download_data.py, make_splits.py,
                          camel_baseline.py, smoke_test.py, guarded_server.py, make_figures.py, build_notebook.py
tests/                    §9 unit tests (the only place slide values appear) + grep check
third_party/              CaMeL reference implementation (unmodified, Apache-2.0)
data/                     README.md = data card; raw/ (git-ignored), processed/, splits/
results/                  per-phase CSV/JSON of every run (versioned); MLflow DB, LLM cache, logs, archive (git-ignored)
report/                   campaign/ (report PDF + charts), compare/ (run comparison), runs/<name>/ (cluster copies)
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

## Big test campaign (cluster or laptop)

```bash
./run_campaign.sh --plan               # phases, test counts, time estimate
./run_campaign.sh --profile laptop     # or: sbatch cluster/horizon_campaign.sbatch (see cluster/README.md)
```

Runs every experiment in dependency order (resumable). The default profile `bounded` is sized to 2,000–4,000 tests
in total (`--plan` prints the sum; the campaign refuses to start outside that range), then writes
`report/campaign/campaign_report.pdf`: the process (phases, durations, failures with log tails) and, per defence
configuration, how many attacks got through vs. were blocked.

## Upgrades (autotune, stronger attacks, tighter math)

* `eval/autotune.py` (campaign phase `autotune`, before Stage 1 fit): successive-halving search over new Stage 1
  forms (longer dynamic markers, keyed delimiting, sandwich reminder) on the fit split; adopts only clear wins into
  `config/autotuned.yaml` (delete it to undo). Also picks the tightest *provably valid* estimator by simulation and
  compares victim models (report only).
* Attacks: adaptive attacker with budget 20, early stop and transfer seeds; new AgentDojo style `value_tamper`
  (data-flow attack on Stage 2); second victim model `models.alt_victim` (phase `stage1_altmodel`).
* Math (switches in params.yaml): CP mean in V1, data-driven ρ̂, Holm-adjusted McNemar, bootstrap CIs of ASR drops,
  Chernoff/exact/model-free vote bounds + run correlation, CRC with B = a and per-style λ̂, Bonferroni/Bernstein
  loss bounds, trace-based side-channel bits.
* `tests/test_integration_endpoints.py`: every OpenAI-style client hits exactly `/v1/chat/completions`.

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

## Results, reports and figures (all inside this folder)

| Folder | What is in it | In git |
|---|---|---|
| `results/campaign/`, `results/stage1/`, `results/stage2/`, `results/testing/` | per-phase CSV/JSON of the latest run in this folder: every attack, verdict, λ̂, π*, guard decision | yes |
| `results/llm_cache/`, `results/logs/`, `results/archive/`, `results/*.db`, `results/mlartifacts/` | LLM cache, raw logs, older runs, MLflow | no (large, rebuilt by every run) |
| `report/campaign/` | `campaign_report.{md,pdf,tex}`, `charts/`, `results_by_group.csv`, `process.csv`, `examples.csv` | yes |
| `report/compare/` | `compare_runs.{md,pdf}`, `scoreboard.csv`, charts: runs side by side | yes |
| `report/runs/<name>/` | the campaign report of each cluster copy in `~/0_runs/<name>` (e.g. the four planner/reader pairings) | yes |
| `figures/` | F1–F18 as PDF, PNG and CSV, plus `captions.json` | yes |
| `docs/` | pipeline diagram (`pipeline.{dot,pdf,png,svg}`) | yes |

To copy the reports of the cluster runs into `report/runs/` (so they are versioned with the code):

```bash
mkdir -p report/runs; for d in ~/0_runs/*/; do n=$(basename "$d"); [ -d "$d/report/campaign" ] && rsync -a --delete "$d/report/campaign/" "report/runs/$n/"; done
```

## References

1. E. Debenedetti, I. Shumailov, T. Fan, J. Hayes, N. Carlini, D. Fabian, C. Kern, C. Shi, A. Terzis, F. Tramèr.
   *Defeating Prompt Injections by Design* (CaMeL). 2025. [arXiv:2503.18813](https://arxiv.org/abs/2503.18813) ·
   code: [google-research/camel-prompt-injection](https://github.com/google-research/camel-prompt-injection) (Stage 2)
2. K. Hines, G. Lopez, M. Hall, F. Zarfati, Y. Zunger, E. Kıcıman. *Defending Against Indirect Prompt Injection
   Attacks With Spotlighting.* 2024. [arXiv:2403.14720](https://arxiv.org/abs/2403.14720) (Stage 1)
3. E. Debenedetti, J. Zhang, M. Balunović, L. Beurer-Kellner, M. Fischer, F. Tramèr. *AgentDojo: A Dynamic
   Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents.* NeurIPS 2024 Datasets and
   Benchmarks. [arXiv:2406.13352](https://arxiv.org/abs/2406.13352)
4. M. Nasr, N. Carlini, C. Sitawarin, et al. *The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses
   against LLM Jailbreaks and Prompt Injections.* USENIX Security 2026. [arXiv:2510.09023](https://arxiv.org/abs/2510.09023)
5. K. Greshake, S. Abdelnabi, S. Mishra, C. Endres, T. Holz, M. Fritz. *Not what you've signed up for: Compromising
   Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec 2023.
   [arXiv:2302.12173](https://arxiv.org/abs/2302.12173)
6. J. Yi, Y. Xie, B. Zhu, E. Kıcıman, G. Sun, X. Xie, F. Wu. *Benchmarking and Defending Against Indirect Prompt
   Injection Attacks on Large Language Models* (BIPIA). KDD 2025. [arXiv:2312.14197](https://arxiv.org/abs/2312.14197)
7. E. Zverev, S. Abdelnabi, S. Tabesh, M. Fritz, C. H. Lampert. *Can LLMs Separate Instructions From Data? And
   What Do We Even Mean By That?* ICLR 2025. [arXiv:2403.06833](https://arxiv.org/abs/2403.06833)
8. E. Wallace, K. Xiao, R. Leike, L. Weng, J. Heidecke, A. Beutel. *The Instruction Hierarchy: Training LLMs to
   Prioritize Privileged Instructions.* 2024. [arXiv:2404.13208](https://arxiv.org/abs/2404.13208)
9. M. Costa, B. Köpf, A. Kolluri, et al. *Securing AI Agents with Information-Flow Control* (FIDES). 2025.
   [arXiv:2505.23643](https://arxiv.org/abs/2505.23643)
10. A. N. Angelopoulos, S. Bates, A. Fisch, L. Lei, T. Schuster. *Conformal Risk Control.* ICLR 2024.
    [arXiv:2208.02814](https://arxiv.org/abs/2208.02814) (λ̂)
11. L. de Moura, N. Bjørner. *Z3: An Efficient SMT Solver.* TACAS 2008.
    [doi:10.1007/978-3-540-78800-3_24](https://doi.org/10.1007/978-3-540-78800-3_24) (policy proof)
12. G. D. Lopez Munoz et al. *PyRIT: A Framework for Security Risk Identification and Red Teaming in Generative AI
    Systems.* 2024. [arXiv:2410.02828](https://arxiv.org/abs/2410.02828)
13. L. Derczynski, E. Galinkin, J. Martin, S. Majumdar, N. Inie. *garak: A Framework for Security Probing Large
    Language Models.* 2024. [arXiv:2406.11036](https://arxiv.org/abs/2406.11036)

Datasets, models and tools with their licenses: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md). More
papers: [`Ref/`](https://github.com/parsabe/NetWatch/tree/master/Ref) on `master`.

## Citation

Horizon is RQ1 of NetWatch. Please cite the project; GitHub's "Cite this repository" button on
[`master`](https://github.com/parsabe/NetWatch) uses `CITATION.cff`, which is also in this folder.

```bibtex
@mastersthesis{netwatch2026,
  author = {parsabe},
  title  = {{NetWatch}: A Cybernetic Guardrail Architecture for Multimodal Autonomous Systems},
  school = {Master's thesis},
  year   = {2026},
  url    = {https://github.com/parsabe/NetWatch},
  note   = {RQ1 Horizon, branch RQ1-Prompt-Injection}
}
```

## License

Own code: MIT, see [`LICENSE`](LICENSE). Third-party software, models and datasets keep their own licenses, listed
in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) with full texts in [`LICENSES/`](LICENSES).
