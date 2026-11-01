# RQ2 · VectorDB Flame: secure vector RAG and retrieval integrity

[NetWatch](https://github.com/parsabe/NetWatch) · [RQ1 Prompt injection](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) · **RQ2 VectorDB Flame** · [RQ3 BlackWall](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) · [Documentation](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION)

**Research question.** How can retrieval-augmented generation (RAG) pipelines that use vector databases be
shielded against corpus poisoning, indirect instruction delivery and data exfiltration?

**Status:** not started. This branch holds only this folder. The thesis text is on
[`DOCUMENTATION`](https://github.com/parsabe/NetWatch/tree/DOCUMENTATION), and the prompt-injection defence that
RQ2 builds on (Horizon) is on [`RQ1-Prompt-Injection`](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection).

## Planned components

| Component | Engineering | Mathematics |
|---|---|---|
| Chunk attestation | Ed25519 signature on every chunk at ingestion; unsigned or altered chunks are never retrieved | signature security (EUF-CMA) |
| Zero-trust access | attribute-based access control (ABAC) on every query, per user and per chunk | policy as a decidable predicate |
| Poison detection | quarantine pipeline before chunks enter the index | Mahalanobis distance in the embedding space, $D_M(x)=\sqrt{(x-\mu)^\top\Sigma^{-1}(x-\mu)}$ |
| Exfiltration tripwires | cryptographic honeytokens planted in the corpus; any appearance in output raises an alarm | false-alarm and detection bounds |

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

1. W. Zou, R. Geng, B. Wang, J. Jia. *PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation
   of Large Language Models.* USENIX Security 2025. [arXiv:2402.07867](https://arxiv.org/abs/2402.07867)
2. K. Greshake, S. Abdelnabi, S. Mishra, C. Endres, T. Holz, M. Fritz. *Not what you've signed up for:
   Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec 2023.
   [arXiv:2302.12173](https://arxiv.org/abs/2302.12173)
3. D. J. Bernstein, N. Duif, T. Lange, P. Schwabe, B.-Y. Yang. *High-speed high-security signatures.* Journal of
   Cryptographic Engineering 2(2), 2012. [ed25519.cr.yp.to](https://ed25519.cr.yp.to/ed25519-20110926.pdf)
4. V. C. Hu et al. *Guide to Attribute Based Access Control (ABAC) Definition and Considerations.* NIST SP 800-162,
   2014. [doi:10.6028/NIST.SP.800-162](https://doi.org/10.6028/NIST.SP.800-162)
5. K. Lee, K. Lee, H. Lee, J. Shin. *A Simple Unified Framework for Detecting Out-of-Distribution Samples and
   Adversarial Attacks.* NeurIPS 2018. [arXiv:1807.03888](https://arxiv.org/abs/1807.03888)
6. L. Spitzner. *Honeytokens: The Other Honeypot.* SecurityFocus, 2003.
7. E. Debenedetti et al. *Defeating Prompt Injections by Design* (CaMeL). 2025.
   [arXiv:2503.18813](https://arxiv.org/abs/2503.18813)

More papers on this topic are in [`Ref/`](https://github.com/parsabe/NetWatch/tree/master/Ref) on `master`
(folder "Secure Vector Databases & RAG").

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
  note   = {RQ2 VectorDB Flame, branch RQ2-VectorDB-Flame}
}
```

## License

Own code and text: MIT, see [`LICENSE`](LICENSE). Papers and third-party components keep their own licenses.
