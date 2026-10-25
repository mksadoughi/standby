#!/usr/bin/env bash
# A resume or a restart keeps the runs before it: `standby start` moves daemon.log to
# daemon.log.1 (then .2, .3), and the new run's log starts empty. A stand-in Python
# under a made-up HOME: never the real recorder, nor the real ~/.standby.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -uo pipefail
cd "$(dirname "$0")/.."
T="$(cd "$(mktemp -d)" && pwd -P)"; H="$T/home"; mkdir -p "$H/.standby/venv/bin"
export STANDBY_RECORDER_MATCH="$T/no-such-recorder[.]py run"     # none of the user's count
cat > "$H/.standby/venv/bin/python" <<'PY'
#!/bin/bash
# The recorder, as start sees it: it says it is live, then ends.
echo "  Standby is live. Session: run-$(date +%s%N)"
PY
chmod +x "$H/.standby/venv/bin/python"
fail=0
check() { if eval "$2"; then printf "  %-62s OK\n" "$1"; else printf "  %-62s FAIL\n" "$1"; fail=1; fi; }
L="$H/.standby/daemon.log"
start() { HOME="$H" bash mac/standby start --no-claude "$1" > "$T/out.$1" 2>&1; }
start first
check "a first start: its log, nothing kept before it" "[ -s $L ] && [ ! -e $L.1 ]"
echo "the first run's bug" >> "$L"
start second
check "a second start (a resume): the first run's log kept as daemon.log.1" "grep -q \"the first run's bug\" $L.1"
check "  and the new run's log is its own, fresh" "! grep -q \"the first run's bug\" $L && grep -q 'Standby is live' $L"
start third; start fourth; start fifth
check "three runs back are kept, no more (the first run's is gone by the fifth)" \
  "[ -e $L.3 ] && [ ! -e $L.4 ] && ! grep -q \"the first run's bug\" $L $L.1 $L.2 $L.3"
check "  the newest kept is the run just before" "[ \"\$(grep -c 'Standby is live' $L.1)\" = 1 ]"
check "start still waits for this run's \"live\", not an old one's" "grep -q 'Standby is live' $T/out.fifth"
rm -rf "$T"
[ $fail -eq 0 ] && echo "ALL PASS" || echo "FAILURES"
exit $fail
