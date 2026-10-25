"""Where Claude Code is, on the machine that runs this: the one finder, for the
agent (standby-agent, here or on a host), the Mac's checks (mac/transport.py) and the
ssh commands to a host, which may have no Standby on it yet.

It is sh, run in /bin/sh, the one shell every host has, and sets C (empty for none):

    FIND_CLAUDE        $STANDBY_CLAUDE, PATH, the shell's own PATH (an interactive
                       $SHELL: nvm and the like add themselves only there), where its
                       installers put it, nvm's newest. It notes what it found in
                       <root>/agent/claude-path. For Test connection, the agent's start
                       and its probe.
    FIND_CLAUDE_QUICK  $STANDBY_CLAUDE, the one noted, PATH, the places, nvm's newest:
                       never the shell, which costs up to a second and runs the user's
                       rc files. For every agent command.

What isn't in these places, the user's shell finds.
"""
import os
import subprocess

# $STANDBY_CLAUDE, the one override, as it is (~/ for the home); and Standby's folder here.
_START = ('C="${STANDBY_CLAUDE:-}"; case "$C" in "~/"*) C="$HOME/${C#??}";; esac; '
          'R="${STANDBY_ROOT:-$HOME/.standby}"; ')
_NOTED = ('if [ -z "$C" ] && [ -r "$R/agent/claude-path" ]; then '
          'c=$(head -n 1 "$R/agent/claude-path"); [ -x "$c" ] && C="$c"; fi; ')
_PATH = ('if [ -z "$C" ]; then C=$(command -v claude 2>/dev/null); '
         'case "$C" in /*) ;; *) C="";; esac; fi; ')
_SHELL = ('if [ -z "$C" ] && [ -n "$SHELL" ]; then '
          "P=$(\"$SHELL\" -i -c 'echo __SBPATH__$PATH' 2>/dev/null </dev/null | sed -n 's/.*__SBPATH__//p' | tail -n 1); "
          'for d in $(printf %s "$P" | tr ": " "\\n\\n"); do [ -x "$d/claude" ] && { C="$d/claude"; break; }; done; fi; ')
# Its own installer's, then npm's, volta's, Homebrew's (macOS, then Linux), the system's.
_PLACES = ('if [ -z "$C" ]; then for c in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" '
           '"$HOME/.npm-global/bin/claude" "$HOME/.volta/bin/claude" /opt/homebrew/bin/claude '
           '/home/linuxbrew/.linuxbrew/bin/claude /usr/local/bin/claude; do '
           '[ -x "$c" ] && { C="$c"; break; }; done; fi; ')
# nvm's, the newest Node's (by its version's numbers: v9 is older than v22).
_NVM = ('if [ -z "$C" ]; then C=$(for c in "$HOME"/.nvm/versions/node/v*/bin/claude; do '
        '[ -x "$c" ] && printf "%s\\n" "$c"; done | '
        'awk -F/ \'{v=$(NF-2); sub(/^v/, "", v); split(v, a, "."); printf "%06d%06d%06d %s\\n", a[1], a[2], a[3], $0}\' | '
        'sort | tail -n 1 | cut -d" " -f2-); fi')
_NOTE = ('; if [ -n "$C" ] && [ -x "$C" ] && [ -d "$R/agent" ]; then '       # one that runs: not a missing override
         'printf "%s\\n" "$C" > "$R/agent/claude-path" 2>/dev/null; fi')

FIND_CLAUDE_QUICK = _START + _NOTED + _PATH + _PLACES + _NVM
FIND_CLAUDE = _START + _PATH + _SHELL + _PLACES + _NVM + _NOTE


def find_claude(full=False):
    """Claude Code's path here, or None: $STANDBY_CLAUDE as it is (whether or not
    it's there: the caller says so), else the first one found."""
    try:
        out = subprocess.run(["/bin/sh", "-c", (FIND_CLAUDE if full else FIND_CLAUDE_QUICK) + '; printf "%s\\n" "$C"'],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=30 if full else 10).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[-1] if out and out[-1] else None
