#!/usr/bin/env bash
# Compile the thesis and the meeting slides to PDF (pdflatex, in place, next to each .tex file).
#
#   ./compile_latex.sh            # both documents
#   ./compile_latex.sh docs       # only  4 - Documentation/main.tex
#   ./compile_latex.sh slides     # only  Meetings/20.10/combined_presentation.tex
#
# Uses latexmk when it is installed, otherwise runs pdflatex three times (table of contents, references
# and page numbers need several passes; both documents keep their references inside the .tex, no bibtex).
# Output: main.pdf / combined_presentation.pdf next to their .tex files; full logs: <name>.log there.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

DOCS=("$ROOT/4 - Documentation|main.tex")
SLIDES=("$ROOT/Meetings/20.10|combined_presentation.tex")
case "${1:-all}" in
  docs)   JOBS=("${DOCS[@]}") ;;
  slides) JOBS=("${SLIDES[@]}") ;;
  all)    JOBS=("${DOCS[@]}" "${SLIDES[@]}") ;;
  *)      echo "usage: $0 [all|docs|slides]"; exit 2 ;;
esac

if ! command -v pdflatex >/dev/null 2>&1; then
  module load texlive 2>/dev/null || true          # some clusters provide TeX as a module
fi
if ! command -v pdflatex >/dev/null 2>&1; then
  echo "pdflatex not found. Install TeX Live (e.g. 'sudo apt install texlive-full') or compile on a machine that has it."
  exit 1
fi

failed=0
for job in "${JOBS[@]}"; do
  dir="${job%%|*}"
  tex="${job##*|}"
  name="${tex%.tex}"
  echo "==== $dir/$tex"
  if [ ! -f "$dir/$tex" ]; then
    echo "   !! file not found"; failed=1; continue
  fi
  (
    cd "$dir" || exit 1
    if command -v latexmk >/dev/null 2>&1; then
      latexmk -pdf -interaction=nonstopmode -halt-on-error "$tex" >/dev/null 2>&1
    else
      for pass in 1 2 3; do
        pdflatex -interaction=nonstopmode -halt-on-error "$tex" >/dev/null 2>&1 || exit 1
      done
    fi
  )
  if [ $? -eq 0 ]; then
    echo "   OK → $dir/$name.pdf"
  else
    failed=1
    echo "   !! FAILED — first errors from $dir/$name.log:"
    grep -A3 -E '^!|LaTeX Error' "$dir/$name.log" 2>/dev/null | head -20 | sed 's/^/      /'
  fi
  overfull=$(grep -c 'Overfull \\hbox\|Overfull \\vbox' "$dir/$name.log" 2>/dev/null || true)
  [ "${overfull:-0}" -gt 0 ] && echo "   note: $overfull overfull box warning(s) — text may run past the page/slide edge"
done
exit "$failed"
