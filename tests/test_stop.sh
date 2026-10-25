#!/usr/bin/env bash
# `standby stop` must end every recorder, including one that ignores SIGTERM
# and an orphan the pidfile no longer knows about. Recreates the real incident.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -uo pipefail
cd "$(dirname "$0")/.."
NAME="standby"".py"      # split so this script's own cmdline never matches
# `standby stop` kills every recorder on the machine, not just ours. Never run
# this while a real one is capturing a meeting.
# (A test's own stand-in recorder, in a "fake" folder, is not a real one: suites run side by side.)
if pgrep -lf 'standby[.]py run' 2>/dev/null | grep -v '/fake/standby[.]py' | awk '$2 ~ /[Pp]ython[0-9.]*$/' | grep -q .; then
  echo "SKIP (a real recorder is running; this test would stop it)"; exit 0
fi
T="$(mktemp -d)"; mkdir -p "$T/fake" "$T/home/.standby"
# Only this test's fakes count, and only they are stopped: never the user's
# recording, nor a teammate's test running at the same time.
export STANDBY_RECORDER_MATCH="$T/fake/standby[.]py run"
cat > "$T/fake/$NAME" <<'PY'
import os, signal, sys, time
if "--stubborn" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)     # a hung shutdown
open(os.environ["SB_READY"], "w").close()
time.sleep(300)
PY
SB_READY="$T/ready.stub" python3 "$T/fake/$NAME" run --stubborn 2>/dev/null & STUB=$!
disown $STUB 2>/dev/null
SB_READY="$T/ready.orphan" python3 "$T/fake/$NAME" run 2>/dev/null & ORPHAN=$!
disown $ORPHAN 2>/dev/null
echo $STUB > "$T/home/.standby/daemon.pid"          # orphan is NOT in the pidfile
# Wait until both are really up, not a fixed sleep: macOS python3 re-execs through
# a launcher, and until then pgrep can miss it or SIGTERM can beat the handler.
for _ in $(seq 1 100); do
  [ -e "$T/ready.stub" ] && [ -e "$T/ready.orphan" ] && break
  sleep 0.1
done
fail=0
check() { if eval "$2"; then printf "  %-54s OK\n" "$1"; else printf "  %-54s FAIL\n" "$1"; fail=1; fi; }
check "start refuses while recorders run" \
  "! HOME=$T/home bash mac/standby start test >/dev/null 2>&1"
st="$(HOME=$T/home bash mac/standby status 2>&1)"
check "status warns about 2 recorders" "echo \"\$st\" | grep -q '2 recorders running'"
out="$(HOME=$T/home bash mac/standby stop 2>&1)"
check "stop forces the stubborn one" "echo \"\$out\" | grep -q forcing"
check "stubborn recorder is dead" "! kill -0 $STUB 2>/dev/null"
check "orphan (not in pidfile) is dead" "! kill -0 $ORPHAN 2>/dev/null"
check "stop reports no recorder running" "echo \"\$out\" | grep -q 'no recorder running'"
check "pidfile removed only after death" "[ ! -e $T/home/.standby/daemon.pid ]"
check "second stop is a clean no-op" "HOME=$T/home bash mac/standby stop | grep -q 'not running'"
# The app polls for recorders with pgrep; that pgrep's argv holds the pattern and
# used to count as a recorder, so start refused and stop "failed" at random.
( while :; do pgrep -f "$T/fake/standby.py run" >/dev/null; done ) & POLL=$!
busy=0
for _ in $(seq 1 40); do
  [ -n "$(HOME=$T/home bash mac/standby status | grep WARNING)" ] && busy=1
  HOME=$T/home bash mac/standby stop | grep -q 'not running' || busy=1
done
kill $POLL 2>/dev/null; wait $POLL 2>/dev/null
check "another pgrep polling is never taken for a recorder" "[ $busy -eq 0 ]"
kill -9 $STUB $ORPHAN 2>/dev/null; rm -rf "$T"
[ $fail -eq 0 ] && echo "ALL PASS" || echo "FAILURES"
exit $fail
