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
#   ./git.sh                     THE one command: every folder that changed is committed on its own branch, then
#                                ALL branches are pushed in ONE push (one login); per-branch changes + summary shown
#   ./git.sh "message"           the same, with your message for every branch
#   ./git.sh --dry-run           only show what it would do (changes nothing)
#
#   rarely needed:  ./git.sh status · ./git.sh RQ1 ["message"] (one folder) · ./git.sh setup (fresh clone)
#
# Without a message every branch gets its own one, e.g. "RQ1: update docs/, config/ (5 files)".
# Run it from anywhere; it works on the checkout it lives in.
set -euo pipefail
ROOT="$(dirname "$(readlink -f "$0")")"
cd "$ROOT"

BRANCHES=(master RQ1-Prompt-Injection RQ2-VectorDB-Flame RQ3-BlackWall DOCUMENTATION)
DIRS=(. RQ1-Prompt-injection RQ2-VectorDB-Flame RQ3-BlackWall Documentation)
SHORT=(master RQ1 RQ2 RQ3 DOC)
URL="https://github.com/parsabe/NetWatch/tree"
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
is_dry() { [ "$1" = "--dry-run" ] || [ "$1" = "-n" ]; }
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

auto_message() {                           # "RQ1: update docs/, config/ (5 files)" from the staged changes
  local d="$ROOT/${DIRS[$1]}" files n tops shown
  files=$(git -C "$d" diff --cached --name-only)
  n=$(echo "$files" | wc -l)
  tops=$(echo "$files" | awk -F/ '{print (NF>1 ? $1"/" : $1)}' | sort -u)
  shown=$(echo "$tops" | head -4 | paste -sd, - | sed 's/,/, /g')
  [ "$(echo "$tops" | wc -l)" -gt 4 ] && shown="$shown, …"
  echo "${SHORT[$1]}: update $shown ($n file$([ "$n" = 1 ] || echo s))"
}

declare -a COMMITTED=()

commit_one() {                             # folder i: show its changes, add + commit them on its own branch
  local i="$1" msg="$2" dry="$3" b="${BRANCHES[$1]}" d="$ROOT/${DIRS[$1]}"
  echo; echo "──── ${SHORT[$i]} · branch $b · folder ${DIRS[$i]}/"
  COMMITTED[$i]="-"
  if ! attached "$i"; then echo "  !! not attached — run ./git.sh setup"; COMMITTED[$i]="not attached"; return 1; fi
  local on; on=$(git -C "$d" branch --show-current)
  if [ "$on" != "$b" ]; then echo "  !! folder is on '$on', expected $b — skipped"; COMMITTED[$i]="wrong branch"; return 1; fi
  local changes; changes=$(git -C "$d" status --porcelain)
  if [ -z "$changes" ]; then echo "  changes: none"; return 0; fi
  local n; n=$(echo "$changes" | wc -l)
  echo "  changes ($n file(s)):"
  echo "$changes" | head -15 | sed 's/^/    /'
  [ "$n" -gt 15 ] && echo "    … and $((n - 15)) more"
  if [ "$dry" = 1 ]; then COMMITTED[$i]="would commit"; return 0; fi
  git -C "$d" add -A
  local big
  big=$(cd "$d" && git diff --cached --name-only --diff-filter=AM -z \
        | xargs -0 -r -I{} find {} -maxdepth 0 -type f -size +"${MAX_MB}"M 2>/dev/null || true)
  if [ -n "$big" ]; then
    echo "  !! files over ${MAX_MB} MB (GitHub refuses them) — add them to .gitignore; nothing committed:"
    echo "$big" | sed 's/^/     /'; git -C "$d" reset -q; COMMITTED[$i]="too large"; return 1
  fi
  git -C "$d" commit -q -m "${msg:-$(auto_message "$i")}"
  COMMITTED[$i]=$(git -C "$d" log -1 --format='%h')
  echo "  committed: $(git -C "$d" log -1 --format='%h %s' | cut -c1-90)"
}

run() {                                    # $1 = message, $2 = dry run (0/1), $3.. = folders (indices)
  local msg="$1" dry="$2" rc=0 i b; shift 2
  [ "$dry" = 1 ] && echo "DRY RUN: nothing is added, committed or pushed"
  git fetch -q origin 2>/dev/null || echo "(could not reach GitHub — 'not pushed' counts may be stale)"
  for i in "$@"; do commit_one "$i" "$msg" "$dry" || rc=1; done

  # ONE push for every branch with commits GitHub does not have yet → one login, not one per branch
  local todo=() pushed="-"
  for i in "$@"; do
    b="${BRANCHES[$i]}"
    if [ "$(not_pushed "$b")" != 0 ] || [ "${COMMITTED[$i]:-}" = "would commit" ]; then todo+=("$b"); fi
  done
  echo
  if [ ${#todo[@]} -eq 0 ]; then
    echo "════ GitHub: everything is up to date, nothing to push"
  elif [ "$dry" = 1 ]; then
    echo "════ GitHub: would push ${todo[*]} (one push)"; pushed="would push"
  elif git push -q -u origin "${todo[@]}"; then
    echo "════ GitHub: pushed ${todo[*]}"; pushed="pushed"
  else
    echo "════ !! push failed (login? network?) — the commits stay local; run ./git.sh again to push them"
    pushed="FAILED"; rc=1
  fi

  echo; echo "════ summary"
  printf '  %-22s %-14s %-11s %s\n' branch commit GitHub link
  for i in "$@"; do
    b="${BRANCHES[$i]}"
    local p="up to date"; [[ " ${todo[*]:-} " == *" $b "* ]] && p="$pushed"
    printf '  %-22s %-14s %-11s %s\n' "$b" "${COMMITTED[$i]:--}" "$p" "$URL/$b"
  done
  return "$rc"
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
  -h|--help|help)
    sed -n '2,22p' "$0"
    ;;
  push-all|publish|open|switch)
    echo "'$1' is not needed any more: just run ./git.sh (or ./git.sh --dry-run to preview)"; exit 2
    ;;
  *)
    if [ $# -ge 1 ] && is_name "$1"; then                       # one folder only
      if is_dry "${2:-}"; then run "" 1 "$(idx "$1")"; else run "${2:-}" 0 "$(idx "$1")"; fi
    elif [ $# -ge 1 ] && is_dry "$1"; then
      run "" 1 1 2 3 4 0
    elif [ $# -ge 1 ] && [[ "$1" == -* ]]; then
      echo "unknown option '$1' (nothing done). Use: ./git.sh  ·  ./git.sh \"message\"  ·  ./git.sh --dry-run"; exit 2
    else
      run "${1:-}" 0 1 2 3 4 0
    fi
    ;;
esac
