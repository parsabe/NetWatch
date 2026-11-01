# NetWatch: a cybernetic guardrail architecture for multimodal autonomous systems

Master's thesis project. NetWatch protects autonomous AI agents at runtime against attacks that static safety
filters and prompt engineering miss. The work is split into three research questions, and each one is developed on
its own branch.

## Research questions and branches

| Branch | Research question | Status |
|---|---|---|
| [`RQ1`](../../tree/RQ1) | **Prompt injection and adversarial defence: the Horizon architecture.** How can cryptographic randomisation, distributionally robust optimisation and deterministic causal mediation be combined into one guardrail that puts a provable upper bound on indirect prompt-injection risk without hurting model utility? | Implemented and under evaluation (`3 - implementation/Prompt_injection/`) |
| [`RQ2`](../../tree/RQ2) | **GraphRAG topological defence.** How can graph-theoretic anomaly detection in an asynchronous ingestion middleware isolate adversarial relation injections before they corrupt multi-hop reasoning? | Not started |
| [`RQ3`](../../tree/RQ3) | **Vision-language sandboxing.** To what extent can frequency-domain analysis (Fourier transforms) in an isolated vision-proxy gateway neutralise steganographic prompt injections in vision-language models without adding latency? | Not started |

The RQ branches are **never merged** into each other or into `master`. `master` holds what every branch shares: this
README, the licenses, the thesis text, the references and the meeting notes. Each RQ branch starts from `master` and
adds its own implementation and results.

```bash
git clone https://github.com/parsabe/NetWatch.git
cd NetWatch
git switch RQ1          # or RQ2 / RQ3
```

## Repository layout

| Path | Contents |
|---|---|
| `3 - implementation/` | Code. On `RQ1`: `Prompt_injection/` (Horizon: Stage 1 keyed spotlighting, Stage 2 CaMeL-based guardrail, AgentDojo / PyRIT / garak evaluation, PBS cluster scripts) |
| `4 - Documentation/` | The thesis in LaTeX (`main.tex`; build with `./compile_latex.sh`) |
| `Ref/` | Reference papers, grouped by research topic (third-party copyright, see below) |
| `Meetings/` | Meeting notes |
| `propose.md`, `direction.md` | Proposal and implementation direction notes |

## RQ1 in short (branch `RQ1`)

Horizon has two stages. **Stage 1** marks untrusted text with a randomly chosen, secretly keyed transformation (π*,
fitted as a minimax game) so injected instructions are recognisable. **Stage 2** runs the agent through a CaMeL-style
interpreter: a privileged planner never sees untrusted text, quarantined readers vote on extracted values, and a
Z3-checked policy with a conformally calibrated confirmation threshold λ̂ gates every tool call. The evaluation runs
on a PBS GPU cluster with two victim models (Qwen3-8B and Gemma 3 12B). See
`3 - implementation/Prompt_injection/README.md` and `3 - implementation/Prompt_injection/cluster/README.md`.

## Licenses

- **Own code and text:** MIT License, see [`LICENSE`](LICENSE).
- **Third-party software, models and datasets** keep their own licenses. [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)
  lists each one, and [`LICENSES/`](LICENSES) holds the full license texts. Datasets and model weights are downloaded
  by the setup scripts and are not stored in this repository.
- **`Ref/`:** the papers belong to their authors and publishers and are **not** covered by this repository's license.
