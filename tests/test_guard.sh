#!/usr/bin/env bash
# The safety net itself: pointed at the user's real ~/.standby, a test
# stops before it does anything; given no root, it gets a throwaway one. Uses
# suites that touch no files, so even a broken guard could do no harm here.
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
fails=0
check() { if eval "$2"; then printf '  %-56s OK\n' "$1"; else printf '  %-56s FAIL\n' "$1"; fails=$((fails+1)); fi; }
out="$(STANDBY_ROOT="$HOME/.standby" "$PY" tests/test_utterance.py 2>&1)"; rc=$?
check "a python test refuses the real ~/.standby" "[[ $rc -eq 2 ]] && grep -q 'refusing' <<<\"\$out\" && ! grep -q PASS <<<\"\$out\""
out="$(STANDBY_ROOT="$HOME/.standby/../.standby/" "$PY" tests/test_utterance.py 2>&1)"; rc=$?
check "  however the path is written" "[[ $rc -eq 2 ]]"
out="$(STANDBY_ROOT="$HOME/.standby" bash tests/test_ui_page.sh 2>&1)"; rc=$?
check "a shell test refuses it too" "[[ $rc -eq 2 ]] && grep -q 'refusing' <<<\"\$out\" && ! grep -q PASS <<<\"\$out\""
got="$(env -u STANDBY_ROOT "$PY" -c 'import sys; sys.path.insert(0, "tests"); import agentpath, os; print(os.environ["STANDBY_ROOT"])')"
check "no root given: a throwaway one, not the real one" "[[ -d '$got' && '$got' != *'/.standby' ]]"
got="$(env -u STANDBY_ROOT bash -c 'source tests/guard.sh; echo "$STANDBY_ROOT"')"
check "  in shell tests too" "[[ -d '$got' && '$got' != *'/.standby' ]]"
got="$(env -u STANDBY_NO_DEVICES "$PY" -c 'import sys, os; sys.path.insert(0, "tests"); import agentpath; print(os.environ.get("STANDBY_NO_DEVICES"))')"
check "and no real mic or speakers (voice mode's engine stays a fake)" "[[ '$got' == 1 ]]"
got="$(env -u STANDBY_NO_DEVICES bash -c 'source tests/guard.sh; echo "${STANDBY_NO_DEVICES:-}"')"
check "  in shell tests too" "[[ '$got' == 1 ]]"
echo; [[ $fails -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES: $fails"; exit 1; }
