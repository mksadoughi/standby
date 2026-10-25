# Sourced first by every shell test: never the user's real ~/.standby.
# A test that sets no STANDBY_ROOT gets a throwaway one; the real one stops it.
_sb_real="$(cd ~/.standby 2>/dev/null && pwd -P || echo "$HOME/.standby")"
[[ -n "${STANDBY_ROOT:-}" ]] || export STANDBY_ROOT="$(mktemp -d -t sbtest)"
export STANDBY_NO_DEVICES=1      # nor the real mic or speakers (voice mode's engine)
[[ -n "${STANDBY_LAUNCH_AGENTS:-}" ]] || export STANDBY_LAUNCH_AGENTS="$STANDBY_ROOT/LaunchAgents"   # nor the user's login items
if [[ "$(cd "$STANDBY_ROOT" 2>/dev/null && pwd -P || echo "$STANDBY_ROOT")" == "$_sb_real" ]]; then
  echo "refusing to run a test in the real $_sb_real" >&2
  exit 2
fi
