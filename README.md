# NetWatch: a cybernetic guardrail architecture for multimodal autonomous systems

**[NetWatch](https://github.com/parsabe/NetWatch)** · [RQ1 Prompt injection](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) · [RQ2 VectorDB Flame](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) · [RQ3 BlackWall](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) · [Documentation](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION)

This is a master's thesis project. NetWatch protects autonomous AI agents at runtime against attacks that static
safety filters and prompt engineering miss. The work has three research questions (RQs). Each RQ lives in its own
folder, and each folder lives on its own branch.

## Research questions, folders and branches

| Folder | Branch | Research question | Status |
|---|---|---|---|
| `RQ1-Prompt-injection/` | [`RQ1-Prompt-Injection`](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) | **Prompt injection: the Horizon architecture.** How can cryptographic randomisation, distributionally robust optimisation and deterministic causal mediation be combined into one guardrail that puts a provable upper bound on indirect prompt-injection risk without hurting model utility? | Implemented, under evaluation |
| `RQ2-VectorDB-Flame/` | [`RQ2-VectorDB-Flame`](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) | **VectorDB Flame: secure vector RAG and retrieval integrity.** How can RAG vector pipelines be shielded against corpus poisoning, indirect instruction delivery and data exfiltration? | Not started |
| `RQ3-BlackWall/` | [`RQ3-BlackWall`](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) | **BlackWall: autonomous oversight and anti-rogue containment.** How can a supervisor predict, detect and mathematically constrain rogue agent trajectories? | Not started |
| `Documentation/` | [`DOCUMENTATION`](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION) | The thesis text in LaTeX | Writing |

`master` (this branch) holds this README, the license, the citation file, the reference papers (`Ref/`), the
meeting slides (`Meetings/`) and `git.sh`.

The branches are **never merged**. Each branch holds only its own folder, with that folder's content at the
branch root. So `git clone -b RQ1-Prompt-Injection https://github.com/parsabe/NetWatch.git` gives you only the
Horizon project, ready to run.

Each RQ folder is self-contained. It has its own code, its own `results/` (the data each run produces), its own
`report/` (tables, charts and PDF reports) and its own `figures/` (images for the thesis). Nothing is shared
through the repository root.

## Working with the five branches

Locally, the four folders are [git worktrees](https://git-scm.com/docs/git-worktree) inside the `master`
checkout. You see one folder with everything in it, but each subfolder commits to its own branch.

```bash
git clone https://github.com/parsabe/NetWatch.git && cd NetWatch
./git.sh setup              # adds RQ1-Prompt-injection/, RQ2-VectorDB-Flame/, RQ3-BlackWall/, Documentation/

./git.sh status             # all five: branch, uncommitted files, commits not pushed
./git.sh "what I changed"   # commit every folder that changed, each on its own branch, and push those branches
./git.sh RQ1 "message"      # only one folder (RQ1, RQ2, RQ3, DOC or master)
./git.sh push-all           # push all five branches
```

Plain git works too: `cd RQ1-Prompt-injection && git status` shows only RQ1's files, on branch
`RQ1-Prompt-Injection`.

## Repository layout

```
NetWatch/                      branch master
├── README.md  LICENSE  CITATION.cff  git.sh
├── Ref/                       reference papers by topic (third-party copyright)
├── Meetings/                  meeting slides and notes
├── RQ1-Prompt-injection/      branch RQ1-Prompt-Injection: Horizon code, results/, report/, figures/
├── RQ2-VectorDB-Flame/        branch RQ2-VectorDB-Flame:   results/, report/, figures/ (planned)
├── RQ3-BlackWall/             branch RQ3-BlackWall:        results/, report/, figures/ (planned)
└── Documentation/             branch DOCUMENTATION:        the thesis (main.tex, compile_latex.sh)
```

## Citation

If you use NetWatch or one of its parts, please cite it. GitHub's "Cite this repository" button uses
[`CITATION.cff`](CITATION.cff).

```bibtex
@mastersthesis{netwatch2026,
  author = {parsabe},
  title  = {{NetWatch}: A Cybernetic Guardrail Architecture for Multimodal Autonomous Systems},
  school = {Master's thesis},
  year   = {2026},
  url    = {https://github.com/parsabe/NetWatch},
  note   = {RQ1 Horizon: branch RQ1-Prompt-Injection; RQ2 VectorDB Flame: RQ2-VectorDB-Flame; RQ3 BlackWall: RQ3-BlackWall}
}
```

Each folder's README lists the papers that the RQ builds on.

- [RQ1 references](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection#references)
- [RQ2 references](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame#references)
- [RQ3 references](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall#references)

The thesis bibliography is in `Documentation/9 - Referneces/references.tex`.

## Licenses

- **Own code and text:** MIT License, see [`LICENSE`](LICENSE).
- **Third-party software, models and datasets** keep their own licenses. The RQ1 branch lists them in
  [`THIRD_PARTY_LICENSES.md`](https://github.com/parsabe/NetWatch/blob/RQ1-Prompt-Injection/THIRD_PARTY_LICENSES.md),
  and the full license texts are in [`LICENSES/`](LICENSES). Datasets and model weights are downloaded by the
  setup scripts and are not stored in this repository.
- **`Ref/`:** the papers belong to their authors and publishers. They are **not** covered by this repository's
  license.
