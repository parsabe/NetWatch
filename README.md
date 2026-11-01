# NetWatch: a cybernetic guardrail architecture for multimodal autonomous systems

Master's thesis project. NetWatch protects autonomous AI agents at runtime against attacks that static safety
filters and prompt engineering miss. The work is split into three research questions, and each one is developed on
its own branch.

## Research questions and branches

| Branch | What it holds | Status |
|---|---|---|
| `master` | This README, the licenses (`LICENSE`, `THIRD_PARTY_LICENSES.md`, `LICENSES/`) and the shared material every branch starts from | — |
| [`RQ1-Prompt-Injection`](../../tree/RQ1-Prompt-Injection) | **RQ1 · Prompt injection: the Horizon architecture.** How can cryptographic randomisation, distributionally robust optimisation and deterministic causal mediation be combined into one guardrail that puts a provable upper bound on indirect prompt-injection risk without hurting model utility? | Implemented, under evaluation (`3 - implementation/Prompt_injection/`) |
| [`RQ2-VectorDB-Flame`](../../tree/RQ2-VectorDB-Flame) | **RQ2 · VectorDB Flame: secure vector RAG and retrieval integrity.** How can RAG vector pipelines be shielded against corpus poisoning, indirect instruction delivery and data exfiltration (Ed25519 chunk attestation, zero-trust ABAC, outlier detection in the embedding space, cryptographic honeytokens)? | Not started |
| [`RQ3-BlackWall`](../../tree/RQ3-BlackWall) | **RQ3 · BlackWall: autonomous oversight and anti-rogue containment.** How can a cognitive supervisor predict, detect and mathematically constrain rogue agent trajectories (goal drift, deceptive alignment, harmful actions) with latent-space probing, control barrier functions and hardware-rooted guarantees? | Not started |
| [`DOCUMENTATION`](../../tree/DOCUMENTATION) | The thesis text (LaTeX, `4 - Documentation/`) | Writing |

The branches are **never merged** into each other or into `master`. Each one starts from `master` and adds its own
work. `git.sh` handles all five:

```bash
./git.sh status            # all five branches: last commit, what is not pushed yet
./git.sh open RQ2          # open a branch in its own folder (~/NetWatch/<branch>) and work there
./git.sh "what I changed"  # commit everything in this folder on its branch and push that branch
./git.sh push-all          # push all five branches
```

To clone one branch elsewhere: `git clone -b RQ1-Prompt-Injection https://github.com/parsabe/NetWatch.git`.

## Repository layout

| Path | Contents |
|---|---|
| `3 - implementation/` | Code. On `RQ1-Prompt-Injection`: `Prompt_injection/` (Horizon: Stage 1 keyed spotlighting, Stage 2 CaMeL-based guardrail, AgentDojo / PyRIT / garak evaluation, PBS cluster scripts) |
| `4 - Documentation/` | The thesis in LaTeX (`main.tex`; build with `./compile_latex.sh`); edited on `DOCUMENTATION` |
| `Ref/` | Reference papers, grouped by research topic (third-party copyright, see below) |
| `Meetings/` | Meeting notes |
| `propose.md`, `direction.md` | Proposal and implementation direction notes |

## RQ1 in short (branch `RQ1-Prompt-Injection`)

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
