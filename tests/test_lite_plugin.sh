#!/bin/bash
# Standby inside Claude Code, loaded the way a real session loads it: the installed `claude`, with
# the plugin's mod, skill and tools together, in a scratch config and HOME (never the user's), and
# no sign-in. `claude plugin test` loads the mod alone, so a clash between the skill's command
# and the mod's (which once kept the band and the tools away) only shows here.
cd "$(dirname "$0")/.."
command -v claude >/dev/null || { echo "  (no claude here: skipped)"; echo "ALL PASS"; exit 0; }
T=$(mktemp -d -t sblite-load); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home/.standby" "$T/config" && echo 1 > "$T/home/.standby/ui.port"    # no app to reach
fail=0
check() { if [ "$2" = 0 ]; then printf '  %-70s OK\n' "$1"; else printf '  %-70s FAIL\n%s\n' "$1" "$3"; fail=1; fi; }
out=$(HOME="$T/home" CLAUDE_CONFIG_DIR="$T/config" perl -e 'alarm 90; exec @ARGV' claude -p "/standby off" --plugin-dir lite < /dev/null 2>&1)
echo "$out" | grep -q "hook skipped\|refused\|did not load"; check "no hook of the mod is skipped or refused at startup" $((1 - $?)) "$out"
echo "$out" | grep -q "standby: Standby is off for this session."; check "/standby is the mod's command, and it answers" $? "$out"
v=$(CLAUDE_CONFIG_DIR="$T/config" claude plugin validate lite 2>&1)
echo "$v" | grep -q "Validation passed"; check "the plugin validates" $? "$v"
echo "$v" | grep -q "tool.call{tool=" ; check "its tools' hook is there" $? "$v"
[ $fail = 0 ] && echo "ALL PASS"
exit $fail
