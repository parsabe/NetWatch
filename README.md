# RQ3 · BlackWall: autonomous oversight and anti-rogue containment

[NetWatch](https://github.com/parsabe/NetWatch) · [RQ1 Prompt injection](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) · [RQ2 VectorDB Flame](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) · **RQ3 BlackWall** · [Documentation](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION)

**Research question.** How can a supervisor predict, detect and mathematically constrain rogue agent trajectories
(goal drift, deceptive alignment, harmful actions) with latent-space probing, control barrier functions and
hardware-rooted guarantees?

**Status:** not started. This branch holds only this folder. The thesis text is on
[`DOCUMENTATION`](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION).

## Planned components

| Component | Engineering | Mathematics |
|---|---|---|
| Latent probing | read the agent model's activations at every step and flag drift from the user's task | linear probes, activation deltas |
| Action interception | middleware that freezes every tool call and its arguments before execution | — |
| Safety filter | block an action that would leave the safe set | control barrier function: safe set $h(x)\ge 0$, allow only actions with $\dot h(x) \ge -\alpha(h(x))$ |
| Hardware root of trust | the supervisor's decisions and logs are signed inside a trusted execution environment | attestation guarantees |

## Folder layout

```
README.md  LICENSE  CITATION.cff
src/         code (to come)
results/     what every run produces: per-item CSV/JSON, logs, calibration data
report/      tables, charts and PDF reports built from results/
figures/     images and plots for the thesis
```

Everything this RQ produces stays inside this folder. Nothing is shared through the repository root.

## References

1. A. D. Ames, S. Coogan, M. Egerstedt, G. Notomista, K. Sreenath, P. Tabuada. *Control Barrier Functions: Theory
   and Applications.* European Control Conference 2019. [arXiv:1903.11199](https://arxiv.org/abs/1903.11199)
2. S. Abdelnabi, A. Fay, G. Cherubin, A. Salem, M. Fritz, A. Paverd. *Get my drift? Catching LLM Task Drift with
   Activation Deltas.* SaTML 2025. [arXiv:2406.00799](https://arxiv.org/abs/2406.00799)
3. C. Burns, H. Ye, D. Klein, J. Steinhardt. *Discovering Latent Knowledge in Language Models Without
   Supervision.* ICLR 2023. [arXiv:2212.03827](https://arxiv.org/abs/2212.03827)
4. A. Zou et al. *Representation Engineering: A Top-Down Approach to AI Transparency.* 2023.
   [arXiv:2310.01405](https://arxiv.org/abs/2310.01405)
5. E. Hubinger et al. *Sleeper Agents: Training Deceptive LLMs that Persist Through Safety Training.* 2024.
   [arXiv:2401.05566](https://arxiv.org/abs/2401.05566)
6. R. Greenblatt, B. Shlegeris, K. Sachan, F. Roger. *AI Control: Improving Safety Despite Intentional
   Subversion.* ICML 2024. [arXiv:2312.06942](https://arxiv.org/abs/2312.06942)

More papers on this topic are in [`Ref/`](https://github.com/parsabe/NetWatch/tree/master/Ref) on `master`
(folders "Rogue AI, Deception & Cognitive" and "System Sandboxing & Cryptographic Hardware").

## Citation

This folder is one part of NetWatch. Please cite the whole project; GitHub's "Cite this repository" button on
[`master`](https://github.com/parsabe/NetWatch) uses `CITATION.cff`, which is also in this folder.

```bibtex
@mastersthesis{netwatch2026,
  author = {parsabe},
  title  = {{NetWatch}: A Cybernetic Guardrail Architecture for Multimodal Autonomous Systems},
  school = {Master's thesis},
  year   = {2026},
  url    = {https://github.com/parsabe/NetWatch},
  note   = {RQ3 BlackWall, branch RQ3-BlackWall}
}
```

## License

Own code and text: MIT, see [`LICENSE`](LICENSE). Papers and third-party components keep their own licenses.
