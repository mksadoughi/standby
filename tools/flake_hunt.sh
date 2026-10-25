#!/usr/bin/env bash
# One test, run N times, each as the first run in a fresh worktree (no __pycache__
# anywhere, as a new checkout is), under whatever load the Mac has now. Every log is
# kept, and each run's line says how long it took, the load, and which checks failed
# (the names a test prints as "  FAILED: <check>", test_session_start's included).
#
#   tools/flake_hunt.sh [-n 10] [-c COMMIT] [-o DIR] tests/test_session_start.py
#
# It adds no load of its own beyond the test, and takes no suite lock: the point is
# the Mac as it is. Worktrees go under DIR and are removed after each run.
set -u
n=10; commit=HEAD; out=""
while getopts "n:c:o:" opt; do
  case "$opt" in
    n) n="$OPTARG" ;;
    c) commit="$OPTARG" ;;
    o) out="$OPTARG" ;;
    *) echo "usage: tools/flake_hunt.sh [-n N] [-c COMMIT] [-o DIR] tests/<test>.py" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
test_path="${1:?usage: tools/flake_hunt.sh [-n N] [-c COMMIT] [-o DIR] tests/<test>.py}"
repo="$(git rev-parse --show-toplevel)" || exit 2
sha="$(git -C "$repo" rev-parse --short "$commit")" || exit 2
out="${out:-${TMPDIR:-/tmp}/standby-flake-$(date +%Y%m%d-%H%M%S)}"
PY="${PY:-$HOME/.standby/venv/bin/python}"
mkdir -p "$out"
echo "hunting: $test_path at $sha, $n fresh worktrees; logs in $out" | tee "$out/summary.txt"
fails=0
for i in $(seq 1 "$n"); do
  wt="$out/wt-$i"
  git -C "$repo" worktree add -q --detach "$wt" "$sha" || exit 1
  load="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')"
  [ -n "$load" ] || load="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
  t0="$(date +%s)"
  (cd "$wt" && LC_ALL=C "$PY" "$test_path" > "$out/run-$i.log" 2>&1)
  rc=$?
  took=$(( $(date +%s) - t0 ))
  names="$(grep '^  FAILED: ' "$out/run-$i.log" | sed 's/^  FAILED: //' | paste -sd ';' -)"
  printf 'run %2d  rc=%d  %3ds  load %-5s  %s\n' "$i" "$rc" "$took" "${load:-?}" "$names" | tee -a "$out/summary.txt"
  [ "$rc" -ne 0 ] && fails=$((fails + 1))
  git -C "$repo" worktree remove --force "$wt"
done
git -C "$repo" worktree prune
echo "$fails of $n failed; every run's log: $out/run-<n>.log" | tee -a "$out/summary.txt"
[ "$fails" -eq 0 ]
