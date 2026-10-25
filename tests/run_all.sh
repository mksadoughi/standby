#!/usr/bin/env bash
# Run the suites from the repo root, in parallel, and summarize. Runs on Linux or macOS; no
# microphone, model or window needed for the default run. PY picks the python (needs numpy).
#
#     bash tests/run_all.sh                     the logic suites, together
#     STANDBY_TESTS=full bash tests/run_all.sh  also the ones that need the real world (a window,
#                                               the speaker models, ssh to localhost), one at a time
#     STANDBY_TESTS_JOBS=4 ...                  how many at once (default: the number of CPUs)
#     bash tests/run_all.sh test_calls test_ui  just these (looked up in tests/suites.txt)
#
# Which suite is which: tests/suites.txt. A suite gets a STANDBY_ROOT of its own and no
# devices (STANDBY_NO_DEVICES=1), and refuses the real ~/.standby itself (tests/agentpath.py,
# tests/guard.sh), so they can run side by side.
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
WANT=" $* "; ASKED=$#
# One full run at a time per checkout: a second one in it waits (tests/suite_lock.sh).
source tests/suite_lock.sh
suite_lock_take || exit $?        # 3: it left the queue, as it was told to (STANDBY_SUITE_WAIT_MAX, _IF_HEAD)
OUT="$(mktemp -d -t sbsuite)"
export STANDBY_NO_DEVICES=1
# CI keeps suite stdout/stderr only, including partial output if interrupted.
# Never copy scratch session folders, audio or other files into the log archive.
keep_logs() {
  [[ -n "${STANDBY_TEST_LOG_DIR:-}" ]] || return 0
  mkdir -p "$STANDBY_TEST_LOG_DIR" || return
  local f
  for f in "$OUT"/*.out; do
    [[ -f "$f" ]] && cp "$f" "$STANDBY_TEST_LOG_DIR/$(basename "${f%.out}").log"
  done
}
trap 'keep_logs; rm -rf "$OUT"; suite_lock_release' EXIT
trap 'kill $(jobs -p) 2>/dev/null; exit 130' INT; trap 'kill $(jobs -p) 2>/dev/null; exit 143' TERM; trap 'exit 129' HUP
DIAR_PY="${SB_DIAR_PY:-/tmp/sbdiar/venv/bin/python}"; [[ -x "$DIAR_PY" ]] || DIAR_PY="$PY"
JOBS="${STANDBY_TESTS_JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
FULL=0; [[ "${STANDBY_TESTS:-}" == "full" ]] && FULL=1
now() { perl -MTime::HiRes=time -e 'printf "%.1f\n", time'; }

# The suites asked for (or all), as "name how group", in the manifest's order.
SUITES=()
while read -r name how group _; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  if [[ $ASKED -gt 0 ]]; then
    [[ "$WANT" == *" $name "* ]] || continue
  elif [[ "$group" == "full" && $FULL -eq 0 ]]; then
    continue
  fi
  SUITES+=("$name $how $group")
done < tests/suites.txt
if [[ ${#SUITES[@]} -eq 0 ]]; then echo "no such suite in tests/suites.txt: $WANT" >&2; exit 2; fi
# Every tests/test_* file is in the manifest, so a new suite can't be left out of every run.
UNLISTED=()
for f in tests/test_*.py tests/test_*.sh; do
  f="$(basename "${f%.*}")"
  grep -qE "^$f[[:space:]]" tests/suites.txt || UNLISTED+=("$f")
done
if [[ ${#UNLISTED[@]} -gt 0 ]]; then
  echo "not in tests/suites.txt, so never run: ${UNLISTED[*]} (add each: name, py or sh, par or full)" >&2; exit 2
fi

# One suite, to $OUT/<name>.{out,rc,s}: its own STANDBY_ROOT, the right python.
run_suite() {
  local name="$1" how="$2" t0 root rc
  root="$OUT/$name.root"; mkdir -p "$root"; t0="$(now)"
  case "$how" in
    sh)   STANDBY_ROOT="$root" PY="$PY" bash "tests/$name.sh" > "$OUT/$name.out" 2>&1 < /dev/null ;;
    diar) STANDBY_ROOT="$root" "$DIAR_PY" "tests/$name.py" > "$OUT/$name.out" 2>&1 < /dev/null ;;
    *)    STANDBY_ROOT="$root" "$PY" "tests/$name.py" > "$OUT/$name.out" 2>&1 < /dev/null ;;
  esac
  rc=$?
  echo "$rc" > "$OUT/$name.rc"; perl -e 'printf "%.1f\n", $ARGV[1] - $ARGV[0]' "$t0" "$(now)" > "$OUT/$name.s"
  if tail -n 1 "$OUT/$name.out" | grep -qE "^SKIP|SKIPPED"; then : > "$OUT/$name.skip"; fi
}

T_START="$(now)"
# The par ones, together: the slowest first (the manifest's order), a few at once.
for item in "${SUITES[@]}"; do
  read -r name how group <<< "$item"
  [[ "$group" == "par" ]] || continue
  while [[ "$(jobs -rp | wc -l)" -ge "$JOBS" ]]; do sleep 0.1; done
  run_suite "$name" "$how" &
done
wait
# The full ones, one at a time and behind the machine's own lock: they use the display, the
# pointer and real windows, so two checkouts' runs at once would trip over each other.
FULLS=0
for item in "${SUITES[@]}"; do read -r _ _ group <<< "$item"; [[ "$group" == "full" ]] && FULLS=$((FULLS+1)); done
if [[ $FULLS -gt 0 ]]; then
  MINE="$SUITE_LOCK"; MINE_Q="$SUITE_QUEUE"
  SUITE_LOCK="${STANDBY_REAL_LOCK:-/tmp/standby-real.lock}"; SUITE_QUEUE="$SUITE_LOCK.queue"
  suite_lock_take || exit $?
  for item in "${SUITES[@]}"; do
    read -r name how group <<< "$item"
    [[ "$group" == "full" ]] && run_suite "$name" "$how"
  done
  suite_lock_release
  SUITE_LOCK="$MINE"; SUITE_QUEUE="$MINE_Q"
fi
T_END="$(now)"

pass=0; fail=0; failed=(); n=0
for item in "${SUITES[@]}"; do
  read -r name _ _ <<< "$item"; n=$((n+1))
  rc="$(cat "$OUT/$name.rc" 2>/dev/null || echo 1)"; secs="$(cat "$OUT/$name.s" 2>/dev/null || echo 0)"
  last="$(tail -n 1 "$OUT/$name.out" 2>/dev/null)"
  if [[ $rc -eq 0 ]]; then
    pass=$((pass+1)); printf '  %-26s %6ss  %s\n' "$name" "$secs" "$last"
  else
    fail=$((fail+1)); failed+=("$name")
    printf '  %-26s %6ss  FAILED\n%s\n' "$name" "$secs" "$(tail -n 15 "$OUT/$name.out")"
    # All of it, for a flake that won't come back: the tail can miss the checks that failed.
    keep="${TMPDIR:-/tmp}/standby-suite-$name-$$.log"
    cp "$OUT/$name.out" "$keep" && printf '  %-26s (all of its output: %s)\n' "" "$keep"
  fi
done
echo
printf 'ran %d suite(s) in %.0f s, %s at a time%s\n' "$n" "$(perl -e 'print $ARGV[1]-$ARGV[0]' "$T_START" "$T_END")" "$JOBS" \
  "$([[ $FULL -eq 1 ]] && echo ', with the real-world ones' || echo '; STANDBY_TESTS=full adds the real-world ones')"
if [[ $fail -eq 0 ]]; then echo "ALL $pass SUITES PASS"; else echo "$fail FAILED: ${failed[*]}"; exit 1; fi
