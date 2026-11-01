# NetWatch thesis (LaTeX)

[NetWatch](https://github.com/parsabe/NetWatch) · [RQ1 Prompt injection](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) · [RQ2 VectorDB Flame](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) · [RQ3 BlackWall](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) · **Documentation**

The master's thesis text: *NetWatch: A Cybernetic Guardrail Architecture for Multimodal Autonomous Systems*.
This branch holds only the thesis. The code and results of each research question are on their own branches:

| Chapter material | Branch | Where the numbers and charts come from |
|---|---|---|
| RQ1 Horizon (prompt injection) | [`RQ1-Prompt-Injection`](https://github.com/parsabe/NetWatch/tree/RQ1-Prompt-Injection) | `report/campaign/` (campaign report, charts), `figures/` (F1–F18) |
| RQ2 VectorDB Flame | [`RQ2-VectorDB-Flame`](https://github.com/parsabe/NetWatch/tree/RQ2-VectorDB-Flame) | `report/`, `figures/` |
| RQ3 BlackWall | [`RQ3-BlackWall`](https://github.com/parsabe/NetWatch/tree/RQ3-BlackWall) | `report/`, `figures/` |

## Build

```bash
source ./use-texlive.sh      # cluster only: the account-local TeX Live and a Perl that latexmk accepts
./compile_latex.sh docs      # → main.pdf  (./compile_latex.sh slides builds ../Meetings/20.10 on master)
```

## Layout

```
main.tex                      title page, table of contents, one \input per chapter
0 - abstract/ … 10 - Appendixes/   one folder per chapter
9 - Referneces/references.tex      the bibliography (kept inside the .tex, no bibtex)
figures/                      images used by the thesis, copied from the RQ folders' figures/ and report/
notes/propose.md              the thesis proposal
compile_latex.sh  use-texlive.sh   build
```

## References

The bibliography is [`9 - Referneces/references.tex`](9%20-%20Referneces/references.tex). The papers themselves
are in [`Ref/`](https://github.com/parsabe/NetWatch/tree/master/Ref) on `master`.

## Citation

```bibtex
@mastersthesis{netwatch2026,
  author = {parsabe},
  title  = {{NetWatch}: A Cybernetic Guardrail Architecture for Multimodal Autonomous Systems},
  school = {Master's thesis},
  year   = {2026},
  url    = {https://github.com/parsabe/NetWatch},
  note   = {thesis text: branch DOCUMENTATION}
}
```

`CITATION.cff` in this folder has the same entry. GitHub's "Cite this repository" button on
[`master`](https://github.com/parsabe/NetWatch) uses it.

## License

The thesis text is MIT-licensed, see [`LICENSE`](LICENSE). Quoted papers and figures from other works belong to
their authors.
