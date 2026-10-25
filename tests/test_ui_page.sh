#!/usr/bin/env bash
# Parse and smoke-run the UI page's JavaScript against a fake DOM (needs node).
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -euo pipefail
cd "$(dirname "$0")/.."
NODE="${NODE:-$(command -v node || echo ~/.local/share/mise/installs/node/22.21.1/bin/node)}"
# This run's own copy: a shared /tmp file was overwritten by runs in other worktrees.
D="$(mktemp -d -t sbpage)"; JS="$D/page.js"; trap 'rm -rf "$D"' EXIT    # node wants .js
python3 -c "
import re, sys; s=open('mac/ui.py').read(); page=s[s.index('PAGE = r\"\"\"')+len('PAGE = r\"\"\"'):]
open(sys.argv[1],'w').write(re.search(r'<script>(.*?)</script>', page, re.S).group(1))" "$JS"
"$NODE" --check "$JS"
# The agents view is its own file beside the page: it parses, and the page loads it.
cp mac/agents.js "$D/agents.js"; "$NODE" --check "$D/agents.js"
# An ended card's summary is final once the card exists, points or not (a quick test call)
"$NODE" tests/agents_summary.js
# A repaint redraws only the messages that changed, and events paint once a frame
"$NODE" tests/agents_patch.js
"$NODE" tests/agents_join.js
"$NODE" tests/agents_openfor.js
"$NODE" tests/agents_menu.js
"$NODE" tests/agents_orbbutton.js
"$NODE" tests/agents_ask.js
"$NODE" tests/agents_busy.js
"$NODE" tests/agents_head.js
"$NODE" tests/agents_hear.js
"$NODE" tests/agents_delete.js
# Standby opened on its own: past meetings and calls, what's live on top, no chat
"$NODE" tests/agents_past.js
# The old session view never shows: what's live opens in Standby's own window, at launch too
"$NODE" tests/agents_launch.js
# Never made-up data: a first load the server can't answer keeps the spinner and asks again
"$NODE" tests/agents_load.js
# A call updates live and ends one way, whoever ends it; one poller keeps the screen fresh
"$NODE" tests/agents_callflow.js
# A call's conversation: what you said, what the voice said, what it asked Claude Code; nothing else
"$NODE" tests/agents_callrows.js
"$NODE" tests/voice_escape.js
# The companion's "Open Standby" lands on the agent's page: mxShowLive never opens the old session view
python3 -c "
import re,sys; s=open('mac/agents.js').read(); m=re.search(r'async function mxShowLive\(\) \{(.*?)\n\}', s, re.S)
ok=bool(m) and 'select(' not in m.group(1) and 'mxOpenSession' not in m.group(1) and 'mxGo(' in m.group(1)
print('  %-52s%s' % ('the orb opens what is live, never the old view', 'OK' if ok else 'FAIL')); sys.exit(0 if ok else 1)"
grep -q '<script src="/agents.js"></script>' mac/ui.py && grep -q 'href="/agents.css"' mac/ui.py || { echo "  the page loads the agents view                        FAIL"; exit 1; }
# The companion's page (mac/companion.html): its script parses.
python3 -c "
import sys; s=open('mac/companion.html').read(); open(sys.argv[1],'w').write(s[s.rindex('<script>')+8:s.rindex('</script>')])" "$D/companion.js"
"$NODE" --check "$D/companion.js"
"$NODE" tests/companion_smoke.js
UI_PAGE_JS="$JS" "$NODE" tests/ui_page_smoke.js
# And the smokes fail on an error no check saw (an async call, a timer, a handler): one
# planted in each page must fail its smoke, or a broken menu could pass as ALL PASS again.
{ cat "$JS"; echo 'setTimeout(() => notDeclaredAnywhere(), 0);'; } > "$D/planted.js"
if UI_PAGE_JS="$D/planted.js" "$NODE" tests/ui_page_smoke.js > /dev/null 2>&1; then echo "  a stray error in the page fails its smoke             FAIL"; exit 1; fi
python3 -c "
import sys; s=open('mac/companion.html').read(); i=s.rindex('</script>')
open(sys.argv[1],'w').write(s[:i]+'\nsetTimeout(() => notDeclaredAnywhere(), 0);\n'+s[i:])" "$D/planted.html"
if COMPANION_HTML="$D/planted.html" "$NODE" tests/companion_smoke.js > /dev/null 2>&1; then echo "  a stray error in the companion fails its smoke        FAIL"; exit 1; fi
echo "  a stray error fails each smoke                        OK"
# And in a real browser (headless Chrome, the preview's page): every (?) in Settings shows its help.
