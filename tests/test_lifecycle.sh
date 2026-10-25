#!/usr/bin/env bash
# Server lifecycle: liveness must never lie, and SIGTERM must clean up.
# In a throwaway STANDBY_ROOT. It used to run in the real ~/.standby: it
# stopped the user's own agent, emptied its spool, and (with no setsid on
# macOS) left a mock server answering in its place.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
A="$(cd "$(dirname "$0")/.." && pwd)/plugin/bin/standby-agent"
PY=/usr/bin/python3
export STANDBY_ROOT="$(mktemp -d -t sblife)"
PIDFILE="$STANDBY_ROOT/agent/server.pid"
run() { "$PY" "$A" "$@"; }
trap 'run stop >/dev/null 2>&1; rm -rf "$STANDBY_ROOT"' EXIT
fails=0
check() { if eval "$2"; then printf '  %-50s OK\n' "$1"; else printf '  %-50s FAIL\n' "$1"; fails=$((fails+1)); fi; }

check "not alive before start" "! run alive >/dev/null"
nohup "$PY" "$A" serve --engine mock >"$STANDBY_ROOT/serve.log" 2>&1 &
for _ in $(seq 1 40); do run alive >/dev/null && break; sleep 0.1; done
check "alive once started, detached" "run alive >/dev/null"
SERVER="$(cat "$PIDFILE" 2>/dev/null)"
# Only with one running: otherwise this serve would start, and never return.
check "a second serve is refused, and returns" "run alive >/dev/null && run serve --engine mock 2>&1 | grep -q 'already serving'"
check "answers a spooled question" "run --engine mock q 'what did I just say' | grep -q MOCK"
run stop >/dev/null
check "not alive after stop" "! run alive >/dev/null"
check "SIGTERM removed the pidfile" "[[ ! -e '$PIDFILE' ]]"
check "this run's server is gone" "[[ -n '$SERVER' ]] && ! kill -0 '$SERVER' 2>/dev/null"
echo; [[ $fails -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES: $fails"; exit 1; }
