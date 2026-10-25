#!/usr/bin/env bash
# One full run at a time (tests/suite_lock.sh), on a lock of its own, never the
# real /tmp/standby-suite.lock: a second taker waits and says for whom, then
# takes it once it is free, and waiting runs go in the order they came; a run
# told to (STANDBY_SUITE_WAIT_MAX, STANDBY_SUITE_IF_HEAD) leaves the queue by itself; a stale lock (its run gone, or never given a pid)
# is taken over; a run frees only its own; and run_all.sh's own lines free it
# on exit, Ctrl-C or kill, while kill -9 leaves a lock the next run takes over.
# Every process started here has its own end and is stopped by its recorded pid.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -uo pipefail
cd "$(dirname "$0")/.."
T="$(mktemp -d -t sblock)"
export STANDBY_SUITE_LOCK="$T/suite.lock" STANDBY_SUITE_LOCK_POLL=0.1
# Not the full run's own wishes: it may have been started told to leave the queue
# (STANDBY_SUITE_IF_HEAD, _WAIT_MAX), and these takers would obey them too.
unset STANDBY_SUITE_IF_HEAD STANDBY_SUITE_WAIT_MAX
L="$STANDBY_SUITE_LOCK"
started=()
cleanup() {
  local p
  for p in "${started[@]:-}"; do
    [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null && kill "$p"
  done
  rm -rf "$T"
}
trap cleanup EXIT
fails=0
check() {
  if eval "$2"; then printf '  %-62s OK\n' "$1"
  else printf '  %-62s FAIL\n' "$1"; [[ -n "${3:-}" ]] && printf '     %s\n' "$3"; fails=$((fails+1)); fi
}
# Until $1 is true, at most $2 seconds.
until_true() { local i=0; while ! eval "$1"; do sleep 0.05; i=$((i+1)); [[ $i -ge $(( $2 * 20 )) ]] && return 1; done; }
taker() {       # a full run's lock, held for $1 seconds, then freed; says what it did in $2
  bash -c 'source tests/suite_lock.sh; suite_lock_take; echo "took $$"; sleep "$0"; suite_lock_release; echo freed' "$1" > "$2" 2>&1 &
  started+=($!)
}

taker 1.5 "$T/a"; A=$!
until_true '[[ -s "$L/pid" ]]' 5
check "a full run takes the lock, its pid inside" "[[ \$(cat '$L/pid') == $A ]]"
taker 0 "$T/b"; B=$!
until_true 'grep -q waiting "$T/b"' 5
check "  a second one waits, and says for whom" "grep -q 'waiting for another full run (pid $A)…' '$T/b' && ! grep -q took '$T/b'"
wait "$A"
until_true 'grep -q freed "$T/b"' 5
check "  and takes it once the first is done, then frees it" "grep -q 'took $B' '$T/b' && [[ ! -e '$L' ]]"
wait "$B"

# First come, first served: B came before C, so B goes next, however they poll.
taker 1.5 "$T/a2"; A2=$!
until_true '[[ -s "$L/pid" ]]' 5
taker 0.8 "$T/b2"; B2=$!
until_true 'grep -q waiting "$T/b2"' 5
sleep 1.1                                             # a later second: C's ticket is younger
taker 0 "$T/c2"; C2=$!
until_true 'grep -q waiting "$T/c2"' 5
wait "$A2"
until_true 'grep -q took "$T/b2" || grep -q took "$T/c2"' 5
check "runs take it in the order they came: the one waiting longest first" \
  "grep -q 'took $B2' '$T/b2' && ! grep -q took '$T/c2'" "$(cat "$T/b2" "$T/c2")"
until_true 'grep -q freed "$T/c2"' 8
check "  then the next" "grep -q 'took $C2' '$T/c2'"
check "  and each one's ticket gone once it had its turn" "[[ -z \$(ls '$L.queue') ]]" "$(ls "$L.queue")"
wait "$B2" "$C2"

mkdir -p "$L.queue" && : > "$L.queue/1.$$"            # an older ticket, of a run still alive (this test)
taker 0 "$T/f"; F=$!
until_true 'grep -q waiting "$T/f"' 5
sleep 0.5
check "  the lock free, but a run that came earlier still waiting: its turn first" \
  "grep -q 'waiting for another full run (pid $$)' '$T/f' && ! grep -q took '$T/f' && [[ ! -e '$L' ]]" "$(cat "$T/f")"
rm -f "$L.queue/1.$$"
until_true 'grep -q freed "$T/f"' 5
check "  and once it has gone, the next" "grep -q 'took $F' '$T/f'"
wait "$F"

bash -c 'exit 0' & dead=$!; wait "$dead"
mkdir -p "$L.queue" && : > "$L.queue/1.$dead"
taker 0 "$T/q"; Q=$!
until_true 'grep -q freed "$T/q"' 5
check "a ticket left by a run that's gone: cleared, never in the way" \
  "grep -q 'took $Q' '$T/q' && [[ ! -e '$L.queue/1.$dead' ]]" "$(cat "$T/q")"
wait "$Q"

mkdir "$L" && echo "$dead" > "$L/pid"
taker 0 "$T/c"; C=$!
until_true 'grep -q freed "$T/c"' 5
check "a stale lock (its run gone): taken over at once" "grep -q 'the last full run (pid $dead) is gone' '$T/c' && grep -q 'took $C' '$T/c'"
wait "$C"

mkdir "$L"
taker 0 "$T/d"; D=$!
until_true 'grep -q waiting "$T/d"' 5
check "a lock still being taken (no pid yet): waited for" "grep -q 'pid ?' '$T/d' && ! grep -q took '$T/d'" "$(cat $T/d)"
touch -t 202001010000 "$L"
until_true 'grep -q freed "$T/d"' 5
check "  but one left like that for over a minute is taken over" "grep -q 'no run behind it' '$T/d' && grep -q 'took $D' '$T/d'"
wait "$D"

mkdir "$L" && echo $$ > "$L/pid"
bash -c 'source tests/suite_lock.sh; suite_lock_release'
check "a run frees only its own lock" "[[ \$(cat '$L/pid') == $$ ]]"
rm -rf "$L"

# Leaving the queue by itself, as the run was told when it started.
leaver() {      # $1: its environment, $2: where it says what it did, $3: the folder it runs in
  (cd "${3:-.}" && env $1 bash -c 'source "$0"; suite_lock_take; rc=$?; echo "rc=$rc"
                                     [[ $rc -eq 0 ]] && { echo "took $$"; suite_lock_release; echo freed; }' "$REPO/tests/suite_lock.sh") > "$2" 2>&1 &
  started+=($!)
}
REPO="$PWD"
taker 3 "$T/h"; H=$!
until_true '[[ -s "$L/pid" ]]' 5
leaver "STANDBY_SUITE_WAIT_MAX=1" "$T/w"; Wt=$!
until_true 'grep -q rc= "$T/w"' 5
check "STANDBY_SUITE_WAIT_MAX: it gives up after that long, says so, runs nothing (3)" \
  "grep -q 'gave up waiting after 1 s' '$T/w' && grep -q 'rc=3' '$T/w' && ! grep -q took '$T/w'" "$(cat "$T/w")"
check "  its ticket gone, the holder untouched" "[[ -z \$(ls '$L.queue') && \$(cat '$L/pid') == $H ]]" "$(ls "$L.queue")"
wait "$Wt"
G="$T/repo"
git init -q "$G" && git -C "$G" -c user.email=t@example.com -c user.name=T -c commit.gpgsign=false commit -q --allow-empty -m one
sha="$(git -C "$G" rev-parse HEAD)"
leaver "STANDBY_SUITE_IF_HEAD=${sha:0:9}" "$T/i" "$G"; I=$!
until_true 'grep -q waiting "$T/i"' 5
git -C "$G" -c user.email=t@example.com -c user.name=T -c commit.gpgsign=false commit -q --allow-empty -m two
until_true 'grep -q rc= "$T/i"' 5
check "STANDBY_SUITE_IF_HEAD: while it waits, the checkout moves on: it leaves, runs nothing (3)" \
  "grep -q 'not at ${sha:0:9} any more' '$T/i' && grep -q 'rc=3' '$T/i' && ! grep -q took '$T/i'" "$(cat "$T/i")"
wait "$I" "$H"
leaver "STANDBY_SUITE_IF_HEAD=$(git -C "$G" rev-parse HEAD) STANDBY_SUITE_WAIT_MAX=30" "$T/k" "$G"; K=$!
until_true 'grep -q rc= "$T/k"' 5
check "  at that commit still, and its turn: it takes the lock as usual" "grep -q 'rc=0' '$T/k' && grep -q freed '$T/k'" "$(cat "$T/k")"
wait "$K"
leaver "STANDBY_SUITE_IF_HEAD=no-such-commit" "$T/n" "$G"; N=$!
until_true 'grep -q rc= "$T/n"' 5
check "  a commit that isn't there: it doesn't run" "grep -q 'rc=3' '$T/n' && [[ ! -e '$L' ]]" "$(cat "$T/n")"
wait "$N"
check "run_all.sh stops with that 3, before anything runs" "grep -q 'suite_lock_take || exit \$?' tests/run_all.sh"

# run_all.sh's own lines, from taking the lock to its traps, then ten seconds of "suites".
{ sed -n '/^source tests\/suite_lock.sh/,/^trap .*exit 130/p' tests/run_all.sh
  echo 'echo "$OUT" > "$1"; for i in $(seq 1 100); do sleep 0.1; done'; } > "$T/head.sh"
check "run_all.sh takes the lock before anything else, and frees it on exit" \
  "[[ \$(grep -c 'suite_lock_take\|suite_lock_release' '$T/head.sh') -eq 2 && \$(wc -l < '$T/head.sh') -ge 6 ]]"
for sig in TERM INT; do
  # Started in the background, a script ignores Ctrl-C and can't trap it; a terminal's
  # run doesn't, so Ctrl-C's default comes back before it starts (exec keeps the pid).
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp("bash", ["bash"] + sys.argv[1:])' \
    "$T/head.sh" "$T/root.$sig" & R=$!; started+=($R)
  until_true '[[ -s "$L/pid" && -s "$T/root.$sig" ]]' 5
  kill -s "$sig" "$R"
  until_true '[[ ! -e "$L" ]]' 3
  check "  killed ($sig): the lock and the run's root are gone" "[[ -s '$T/root.$sig' && ! -e '$L' && ! -e \$(cat '$T/root.$sig') ]]"
  wait "$R" 2>/dev/null
done
bash "$T/head.sh" "$T/root.KILL" & R=$!; started+=($R)
until_true '[[ -s "$L/pid" ]]' 5
kill -9 "$R"; wait "$R" 2>/dev/null
taker 0 "$T/e"; E=$!
until_true 'grep -q freed "$T/e"' 5
check "  kill -9 leaves its lock, and the next run takes it over" "grep -q 'took $E' '$T/e' && grep -q 'is gone' '$T/e'" "$(cat "$T/e")"
wait "$E"
rm -rf "$(cat "$T/root.KILL" 2>/dev/null)"      # what kill -9 left behind
echo
if [[ $fails -eq 0 ]]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
