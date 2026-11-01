#!/usr/bin/env bash
# Git for the NetWatch thesis: five branches, never merged. Each branch holds ONE folder, at the branch root.
#
#   folder in this checkout     branch                 short name   holds
#   . (this folder)             master                 master       README, LICENSE, CITATION.cff, git.sh, Ref/, Meetings/
#   RQ1-Prompt-injection/       RQ1-Prompt-Injection   RQ1          Horizon (prompt injection) + its results/report/figures
#   RQ2-VectorDB-Flame/         RQ2-VectorDB-Flame     RQ2          secure vector DB / RAG
#   RQ3-BlackWall/              RQ3-BlackWall          RQ3          BlackWall oversight
#   Documentation/              DOCUMENTATION          DOC          the thesis (LaTeX)
#
# The four subfolders are git worktrees of their branches inside the master checkout: one folder on disk with
# everything in it, but every subfolder commits to its own branch (master's .gitignore leaves them out).
#
#   ./git.sh setup               attach the four folders to their branches (fresh clone: checks them out;
#                                existing folders: keeps the files as they are, nothing is overwritten)
#   ./git.sh status              all five: branch, uncommitted files, commits not pushed yet, last commit
#   ./git.sh                     commit EVERY folder that changed on its own branch and push those branches
#   ./git.sh "message"           the same, with your commit message
#   ./git.sh RQ1 ["message"]     only one folder (RQ1, RQ2, RQ3, DOC, master)
#   ./git.sh push-all            push all five branches
#
# Run it from anywhere; it works on the checkout it lives in.
set -euo pipefail
ROOT="$(dirname "$(readlink -f "$0")")"
cd "$ROOT"

BRANCHES=(master RQ1-Prompt-Injection RQ2-VectorDB-Flame RQ3-BlackWall DOCUMENTATION)
DIRS=(. RQ1-Prompt-injection RQ2-VectorDB-Flame RQ3-BlackWall Documentation)
MAX_MB=95                                  # GitHub refuses files over 100 MB

idx() {                                    # short or full name → position in BRANCHES/DIRS
  case "${1,,}" in
    master|main|root) echo 0 ;;                 # exact names only: a message like "RQ1: fix" stays a message
    rq1|rq1-prompt-injection) echo 1 ;;
    rq2|rq2-vectordb-flame) echo 2 ;;
    rq3|rq3-blackwall) echo 3 ;;
    doc|docs|documentation) echo 4 ;;
    *) return 1 ;;
  esac
}
is_name() { idx "$1" >/dev/null 2>&1; }
attached() { [ "$1" = 0 ] || [ -e "$ROOT/${DIRS[$1]}/.git" ]; }
not_pushed() {                             # commits on the branch that origin does not have yet
  git rev-parse -q --verify "refs/remotes/origin/$1" >/dev/null || { echo "all"; return; }
  git rev-list --count "refs/remotes/origin/$1..refs/heads/$1" --
}

ensure_branch() {                          # local branch, from origin if it is there, else an empty start commit
  local b="$1"
  git show-ref -q --verify "refs/heads/$b" && return 0
  if git fetch -q origin "$b" 2>/dev/null; then git branch -q "$b" FETCH_HEAD; return 0; fi
  local empty; empty=$(git hash-object -t tree /dev/null)
  git branch -q "$b" "$(git commit-tree "$empty" -m "$b: start the branch")"
}

attach() {                                 # make folder i a worktree of its branch
  local i="$1" b="${BRANCHES[$1]}" d="$ROOT/${DIRS[$1]}"
  [ "$i" = 0 ] && return 0
  if [ -e "$d/.git" ]; then echo "  ${DIRS[$i]}/ → $b (already attached)"; return 0; fi
  ensure_branch "$b"
  if [ -d "$d" ] && [ -n "$(ls -A "$d")" ]; then
    # keep the files on disk exactly as they are; only the git link and the index are added
    local tmp="$d.attach.$$"
    mv "$d" "$tmp"
    git worktree add -q --no-checkout "$d" "$b"
    mv "$d/.git" "$tmp/.git"
    rmdir "$d"
    mv "$tmp" "$d"
    git -C "$d" reset -q                   # index = branch, working files untouched
    echo "  ${DIRS[$i]}/ → $b (existing files kept; './git.sh status' shows what differs from the branch)"
  else
    rmdir "$d" 2>/dev/null || true
    git worktree add -q "$d" "$b"
    echo "  ${DIRS[$i]}/ → $b (checked out)"
  fi
}

commit_one() {                             # commit folder i on its branch; push if anything is not on GitHub
  local i="$1" msg="$2" b="${BRANCHES[$1]}" d="$ROOT/${DIRS[$1]}"
  if ! attached "$i"; then echo "!! ${DIRS[$i]}/ is not attached to $b — run ./git.sh setup"; return 1; fi
  local on; on=$(git -C "$d" branch --show-current)
  if [ "$on" != "$b" ]; then echo "!! ${DIRS[$i]}/ is on '$on', expected $b — not touching it"; return 1; fi
  git -C "$d" add -A
  local big
  big=$(cd "$d" && git diff --cached --name-only --diff-filter=AM -z \
        | xargs -0 -r -I{} find {} -maxdepth 0 -type f -size +"${MAX_MB}"M 2>/dev/null || true)
  if [ -n "$big" ]; then
    echo "!! ${DIRS[$i]}/: files over ${MAX_MB} MB (GitHub refuses them) — add them to .gitignore; nothing committed:"
    echo "$big" | sed 's/^/     /'; git -C "$d" reset -q; return 1
  fi
  if git -C "$d" diff --cached --quiet; then
    echo "  $b: nothing to commit"
  else
    git -C "$d" commit -q -m "${msg:-update ${DIRS[$i]} $(date '+%Y-%m-%d %H:%M')}"
    echo "  $b: committed $(git -C "$d" log -1 --format='%h %s' | cut -c1-80)"
  fi
  if [ "$(not_pushed "$b")" != 0 ]; then git push -q -u origin "$b" && echo "  $b: pushed"; fi
}

case "${1:-}" in
  setup)
    git worktree prune
    echo "attaching the folders in $ROOT:"
    for i in 1 2 3 4; do attach "$i"; done
    ;;
  status)
    git fetch -q origin 2>/dev/null || echo "(could not reach GitHub — showing local state)"
    for i in 0 1 2 3 4; do
      b="${BRANCHES[$i]}"; d="$ROOT/${DIRS[$i]}"
      if ! attached "$i"; then printf '  %-22s %-22s not attached (./git.sh setup)\n' "$b" "${DIRS[$i]}/"; continue; fi
      changed=$(git -C "$d" status --porcelain | wc -l)
      printf '  %-22s %-22s %3s changed file(s) · %s not pushed · %s\n' "$b" "${DIRS[$i]}/" "$changed" \
             "$(not_pushed "$b")" "$(git log -1 --format='%h %cr: %s' "refs/heads/$b" -- | cut -c1-60)"
    done
    ;;
  push-all)
    for b in "${BRANCHES[@]}"; do git show-ref -q --verify "refs/heads/$b" && git push -u origin "$b"; done
    ;;
  -h|--help|help)
    sed -n '2,23p' "$0"
    ;;
  open|switch)
    echo "'$1' is gone: every branch is always open in its own folder (${DIRS[*]:1}). See ./git.sh help"; exit 2
    ;;
  *)
    if [ $# -ge 1 ] && is_name "$1"; then
      commit_one "$(idx "$1")" "${2:-}"
    else
      rc=0
      for i in 1 2 3 4 0; do commit_one "$i" "${1:-}" || rc=1; done
      exit "$rc"
    fi
    ;;
esac
