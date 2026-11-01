#!/usr/bin/env bash
# Git for the NetWatch thesis: five branches, never merged into each other.
#
#   master                 README + licenses (what every branch shares)
#   RQ1-Prompt-Injection   RQ1 · Horizon (prompt injection)          short name: RQ1
#   RQ2-VectorDB-Flame     RQ2 · secure vector DB / RAG              short name: RQ2
#   RQ3-BlackWall          RQ3 · BlackWall cognitive overseer         short name: RQ3
#   DOCUMENTATION          the thesis text (LaTeX)                    short name: DOC
#
#   ./git.sh                     commit EVERYTHING changed here on the current branch and push that branch
#   ./git.sh "message"           the same, with your commit message
#   ./git.sh status              all five branches: last commit, commits not pushed yet, folders they are open in
#   ./git.sh open RQ2            open a branch in its OWN folder (~/NetWatch/<branch>) — safest way to work on it
#   ./git.sh switch RQ2          change the branch of THIS folder (refused while you have PBS jobs: the cluster
#                                runs Horizon from ~/0, and switching swaps the code under a running job)
#   ./git.sh push-all            push all five branches
#   ./git.sh setup               create any of the five branches that is missing (from master) and push them
#
# Short names: RQ1, RQ2, RQ3, DOC (or the full names). Run it from anywhere; it works on the folder it lives in.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

BRANCHES=(master RQ1-Prompt-Injection RQ2-VectorDB-Flame RQ3-BlackWall DOCUMENTATION)
FOLDERS="$HOME/NetWatch"
MAX_MB=95                                  # GitHub refuses files over 100 MB

full() {
  case "${1,,}" in
    master|main) echo master ;;
    rq1*) echo RQ1-Prompt-Injection ;;
    rq2*) echo RQ2-VectorDB-Flame ;;
    rq3*) echo RQ3-BlackWall ;;
    doc*) echo DOCUMENTATION ;;
    *) echo "unknown branch '$1' (use master, RQ1, RQ2, RQ3, DOC)" >&2; exit 2 ;;
  esac
}
here() { git branch --show-current; }
has_remote() { git ls-remote --exit-code --heads origin "$1" >/dev/null 2>&1; }

commit_push() {
  local msg="${1:-update $(here) $(date '+%Y-%m-%d %H:%M')}"
  git add -A
  local big
  big=$(git diff --cached --name-only --diff-filter=AM -z | xargs -0 -r -I{} find {} -maxdepth 0 -type f -size +"${MAX_MB}"M 2>/dev/null || true)
  if [ -n "$big" ]; then
    echo "!! files over ${MAX_MB} MB (GitHub refuses them) — add them to .gitignore, nothing was committed:"
    echo "$big"; git reset -q; exit 1
  fi
  if git diff --cached --quiet; then echo "nothing to commit on $(here)"; else git commit -m "$msg"; fi
  git push -u origin "$(here)"
  echo "DONE: $(here) pushed"
}

case "${1:-}" in
  status)
    git fetch -q origin 2>/dev/null || echo "(could not reach GitHub — showing local state)"
    echo "this folder: $(pwd) · on branch $(here)"
    for b in "${BRANCHES[@]}"; do
      if ! git show-ref -q --verify "refs/heads/$b"; then printf '  %-22s (missing — ./git.sh setup)\n' "$b"; continue; fi
      ahead=$(git rev-list --count "origin/$b..$b" 2>/dev/null || echo "all")
      printf '  %-22s %s · %s not pushed\n' "$b" "$(git log -1 --format='%h %cr: %s' "$b" | cut -c1-70)" "$ahead"
    done
    echo "open folders:"; git worktree list | sed 's/^/  /'
    ;;
  open)
    b=$(full "${2:?which branch? e.g. ./git.sh open RQ2}")
    dir="$FOLDERS/$b"
    if git worktree list --porcelain | grep -qx "branch refs/heads/$b"; then
      echo "already open: $(git worktree list | grep "\[$b\]" | awk '{print $1}')"; exit 0
    fi
    mkdir -p "$FOLDERS"
    git show-ref -q --verify "refs/heads/$b" || { git fetch -q origin "$b" && git branch "$b" "origin/$b"; }
    git worktree add "$dir" "$b"
    echo "opened $b in $dir — work there, then run ./git.sh inside it"
    ;;
  switch)
    b=$(full "${2:?which branch? e.g. ./git.sh switch RQ2}")
    if [ -n "$(git status --porcelain)" ]; then echo "!! uncommitted changes — run ./git.sh first"; exit 1; fi
    if command -v qstat >/dev/null && [ -n "$(qstat -u "$USER" 2>/dev/null)" ] && [ "${3:-}" != "--force" ]; then
      echo "!! you have PBS jobs and the cluster runs Horizon from this folder — use: ./git.sh open ${2}"
      echo "   (or ./git.sh switch ${2} --force if no job uses this folder)"; exit 1
    fi
    git switch "$b"
    ;;
  push-all)
    for b in "${BRANCHES[@]}"; do git show-ref -q --verify "refs/heads/$b" && git push -u origin "$b"; done
    ;;
  setup)
    for b in "${BRANCHES[@]}"; do
      git show-ref -q --verify "refs/heads/$b" && continue
      if has_remote "$b"; then git branch "$b" "origin/$b"; else git branch "$b" master; fi
      echo "created $b"
    done
    "$0" push-all
    ;;
  -h|--help|help)
    sed -n '2,22p' "$0"
    ;;
  *)
    commit_push "${1:-}"
    ;;
esac
