#!/usr/bin/env bash
# Run install.sh end to end in a throwaway HOME, with macOS-only tools stubbed:
# links, skill, app bundle, config migration from the old layout, idempotency.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# -P: macOS hands out /var/folders/... (a symlink to /private/var/...) and python
# reports the physical path, so paths built here must be physical to compare.
T="$(cd "$(mktemp -d)" && pwd -P)"; H="$T/home"; B="$T/bin"; mkdir -p "$H" "$B" "$T/stubs/parakeet_mlx"
# Honour $PY like the rest of the suite. It must find numpy under the throwaway
# HOME below, so a venv python beats a system one whose numpy is in the user's
# site-packages (macOS python3 keeps it under $HOME and would lose it here).
REALPY="$(command -v "${PY:-python3}")"
fails=0

# BSD and GNU disagree on both of these; the suite runs on macOS and Linux.
mode_of() { stat -f %Lp "$1" 2>/dev/null || stat -c %a "$1"; }
# No `timeout` on a stock macOS. Fall back to gtimeout, else run unbounded.
if command -v timeout >/dev/null 2>&1;  then TIMEOUT=(timeout 60)
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT=(gtimeout 60)
else TIMEOUT=(); fi
check() {
  if eval "$2"; then printf '  %-58s OK\n' "$1"; return 0
  else printf '  %-58s FAIL\n' "$1"; fails=$((fails+1)); return 1; fi
}

cat > "$B/uname" <<'EOF'
#!/bin/bash
case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; *) echo Darwin ;; esac
EOF
cat > "$B/python3.12" <<'EOF'
#!/bin/bash
[[ "$1" == "-V" ]] && { echo "Python 3.12.4"; exit 0; }
exit 0
EOF
# This Mac as the checks see it, set per run: macOS (SW_VERS), free space in KB (DF_KB) and
# memory in bytes (MEMSIZE). By default a Mac that passes them all.
cat > "$B/sw_vers" <<'EOF'
#!/bin/bash
[[ -n "${SW_VERS-14.7.1}" ]] && echo "${SW_VERS-14.7.1}"
EOF
cat > "$B/df" <<'EOF'
#!/bin/bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/disk3s5 488245288 100000000 ${DF_KB:-104857600} 50% /System/Volumes/Data"
EOF
cat > "$B/sysctl" <<'EOF'
#!/bin/bash
[[ "$*" == "-n hw.memsize" ]] && echo "${MEMSIZE:-17179869184}"
EOF
cat > "$B/claude" <<EOF
#!/bin/bash
echo "\$*" >> "$T/claude.args"
[[ "\$1" == "plugin" && -e "$T/claude-fails" ]] && exit 1
echo "\${CC_VERSION:-2.1.310} (Claude Code)"
EOF
cat > "$B/iconutil" <<'EOF'
#!/bin/bash
touch "${@: -1}"
EOF
chmod +x "$B"/*
echo 'def from_pretrained(name): return None' > "$T/stubs/parakeet_mlx/__init__.py"
# The speech model: a stand-in Hugging Face that notes what was asked for and writes
# a file where the real one would, under the throwaway HOME. Never a real download.
unset HF_HOME HF_HUB_CACHE
cat > "$T/stubs/huggingface_hub.py" <<'EOF'
import os
def hf_hub_download(repo_id, filename, revision=None, **kw):
    home = os.environ["HOME"]
    open(os.path.join(home, "hf.args"), "a").write("%s %s %s\n" % (repo_id, filename, revision))
    snap = os.path.join(home, ".cache", "huggingface", "hub", "models--" + repo_id.replace("/", "--"), "snapshots", revision)
    os.makedirs(snap, exist_ok=True)
    open(os.path.join(snap, filename), "w").write("x")
    return os.path.join(snap, filename)
EOF
for m in sounddevice sherpa_onnx webview; do : > "$T/stubs/$m.py"; done
# numpy: the real one if this python has it (mac/icons.py draws the icons with it), else
# a stand-in, so the installer runs with any python3, only without icons.
if HOME="$H" "$REALPY" -c "import numpy" 2>/dev/null; then ICON_STANDBY=Standby.icns; ICON_DICT=Dictation.icns
else : > "$T/stubs/numpy.py"; ICON_STANDBY=None; ICON_DICT=None
  echo "  (no numpy in $REALPY: a stand-in, and the apps built without icons; set PY to a venv's python for them)"; fi

# A fake py2app: setup() writes the bundle it was asked for, so the test sees
# exactly what install.sh requested (alias mode, plist, icon, entry point).
mkdir -p "$T/fake_setuptools"
cat > "$T/fake_setuptools/setuptools.py" <<'EOF'
import os, plistlib
def setup(app, name, options):
    o = options["py2app"]
    c = os.path.join("dist", name + ".app", "Contents")
    os.makedirs(os.path.join(c, "MacOS"))
    plistlib.dump(dict(o["plist"], CFBundleExecutable=name), open(os.path.join(c, "Info.plist"), "wb"))
    open(os.path.join(c, "build.txt"), "w").write("%s|%s|%s" % (os.path.abspath(app[0]), o.get("alias"), o.get("iconfile")))
EOF

# A venv that already exists: pip is a no-op, python is the real one + stubs.
V="$H/.standby/venv/bin"; mkdir -p "$V"
printf '#!/bin/bash\necho "$*" >> "%s/pip.args"\nexit 0\n' "$T" > "$V/pip"
cat > "$V/python" <<EOF
#!/bin/bash
if [[ "\$1" == "setup.py" && "\$2" == "py2app" ]]; then
  [[ -f "$T/py2app-fails" ]] && exit 1
  PYTHONPATH="$T/fake_setuptools" exec "$REALPY" setup.py
fi
PYTHONPATH="$T/stubs" exec "$REALPY" "\$@"
EOF
chmod +x "$V"/*
mkdir -p "$H/.standby/models"; echo x > "$H/.standby/models/nemo_en_titanet_small.onnx"
echo x > "$H/.standby/models/smart-turn-v3.2-cpu.onnx"      # no downloads in a test
echo x > "$H/.standby/models/silero_vad_v5.1.2.onnx"
mkdir -p "$H/.standby/models/nemotron-3-diarization/Nemotron3Diarizer_fast.mlmodelc/weights"
echo x > "$H/.standby/models/nemotron-3-diarization/Nemotron3Diarizer_fast.mlmodelc/weights/weight.bin"
echo x > "$H/.standby/models/nemotron-3-diarization/learnable_sil_emb.bin"
echo '{"host": "devbox.example.com", "mic_device": 3, "loopback_device": "BlackHole 2ch", "speech_model": "small", "english_only": true}' > "$H/.standby/config.json"

run() { HOME="$H" PATH="$B:/usr/bin:/bin" SHELL=/bin/bash bash "$REPO/install.sh" --from-here > "$T/out.$1" 2>&1; }
run 1; rc=$?
check "installer succeeds" "[[ $rc -eq 0 ]]" || tail -20 "$T/out.1"
check "standby command linked to the repo" "[[ \"\$(readlink $H/.local/bin/standby)\" == $REPO/mac/standby ]]"
check "standby-ctx linked" "[[ -x $H/.local/bin/standby-ctx ]]"
check "no standalone meeting skill linked any more" "[[ ! -e $H/.claude/skills/meeting-standby ]]"
P="$H/Applications/Standby.app/Contents"
check "app bundle: Info.plist valid, asks for the microphone" \
  "$REALPY -c \"import plistlib,sys; d=plistlib.load(open('$P/Info.plist','rb')); sys.exit(not (d['CFBundleName']=='Standby' and d['CFBundleIdentifier']=='dev.standby.app' and d.get('NSMicrophoneUsageDescription')))\""
E="$H/.standby/app-build/Standby.py"
check "app built in alias mode from the generated entry point, with icon" \
  "[[ \"\$(cat $P/build.txt)\" == '$E|True|$ICON_STANDBY' ]]"
check "entry point: venv packages, repo code, window mode, log file" \
  "$REALPY -m py_compile $E && grep -q 'site.addsitedir(\"/' $E && grep -q 'sys.path.insert(0, \"$REPO/mac\")' $E && grep -q 'venv/bin/python\"' $E && grep -q -- '--window' $E && grep -q 'ui.log' $E"
check "installer says how to open the app" "grep -q 'open ~/Applications/Standby.app' $T/out.1"
check "speech model: the one chosen, at its own size (not 'about 600 MB')" \
  "grep -q 'fetching the speech model, Small, English (about 0.5 GB, first time only)' $T/out.1 && ! grep -q '600 MB' $T/out.1 && grep -q 'speech model: Small, English (parakeet-tdt_ctc-110m; Settings > Recording to change it)' $T/out.1"
check "  from Hugging Face, pinned to the revision measured" \
  "[[ \$(grep -c '^mlx-community/parakeet-tdt_ctc-110m [a-z.]* d62547387c356a1ab6bb3d85d98b2103f655282e\$' $H/hf.args) -eq 2 ]] && grep -q ' config.json ' $H/hf.args && grep -q ' model.safetensors ' $H/hf.args"
check "voice mode's end-of-turn model is kept, not fetched again" "grep -q 'end-of-turn model already present' $T/out.1"
check "every package line pinned to constraints.txt, none upgrading what is there" \
  "[[ \$(grep -c -- '-c $REPO/constraints.txt' $T/pip.args) -eq 3 ]] && ! grep -v '^install --quiet --upgrade pip wheel\$' $T/pip.args | grep -q -- '--upgrade'"
check "  constraints.txt pins every package install.sh names, mlx-audio among them" \
  "$REALPY - $REPO/constraints.txt <<'PYC'
import re, sys
pins = {re.sub(r'[-_.]+', '-', l.split('==')[0]).lower() for l in open(sys.argv[1]) if '==' in l and not l.startswith('#')}
named = 'numpy sounddevice parakeet-mlx mlx-whisper sherpa-onnx pywebview onnxruntime pyobjc-framework-AVFoundation pyobjc-framework-ScreenCaptureKit mlx-audio sentencepiece py2app'
sys.exit(0 if all(n.lower() in pins for n in named.split()) else 1)
PYC"
check "voice mode's own voice: Pocket TTS's packages" "grep -q -- '-c $REPO/constraints.txt mlx-audio sentencepiece' $T/pip.args"
check "  said either way: installed, or a macOS voice speaks" \
  "grep -q \"voice mode's voice (Pocket TTS\" $T/out.1 || grep -q 'Pocket TTS did not install: voice mode speaks with a macOS voice' $T/out.1"
DP="$H/Applications/Standby Dictation.app/Contents"
check "dictation app: menu-bar only, asks for the microphone, own id" \
  "$REALPY -c \"import plistlib,sys; d=plistlib.load(open('$DP/Info.plist','rb')); sys.exit(not (d['CFBundleName']=='Standby Dictation' and d['CFBundleIdentifier']=='dev.standby.dictation' and d.get('LSUIElement') is True and d.get('NSMicrophoneUsageDescription')))\""
DE="$H/.standby/app-build/dictation/StandbyDictation.py"
check "dictation app built in alias mode from its entry point, with icon" \
  "[[ \"\$(cat \"$DP/build.txt\")\" == '$DE|True|$ICON_DICT' ]]"
check "dictation entry point: venv packages, repo code, runs it, own log" \
  "$REALPY -m py_compile $DE && grep -q 'site.addsitedir(\"/' $DE && grep -q 'sys.path.insert(0, \"$REPO/mac\")' $DE && grep -qF 'dictation.main([\"run\"])' $DE && grep -q 'dictation.log' $DE"
check "installer says how to turn dictation on" "grep -q 'standby dictation on' $T/out.1"
mkdir -p "$T/fakewebview"
cat > "$T/fakewebview/webview.py" <<'EOF'
import json, os, urllib.request
class _Events(object):
    def __getattr__(self, name): return self          # closing, before_show, ...: all take handlers
    def __iadd__(self, fn): return self
class _Win(object):
    def __init__(self): self.events = _Events()
def create_window(title, url, **kw):
    global _url
    _url = url
    open(os.environ["SB_WIN_OUT"], "w").write(title + "|" + url)
    return _Win()
def start(func=None, *args, **kw):     # pywebview's: func runs once the GUI is up (the companion); not here
    base, _, query = _url.partition("?")              # the window's own url carries this launch's token
    st = json.loads(urllib.request.urlopen(base + "api/state?" + query, timeout=10).read())
    open(os.environ["SB_WIN_OUT"], "a").write("|state-ok" if "sessions" in st else "|state-bad")
EOF
# A free port: a fixed one can be taken by another server or another run.
UIPORT=$("$REALPY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
HOME="$H" SHELL=/bin/bash STANDBY_UI_PORT=$UIPORT SB_WIN_OUT="$T/win" PYTHONPATH="$T/fakewebview" \
  ${TIMEOUT[@]+"${TIMEOUT[@]}"} "$REALPY" "$E"; rc=$?
check "entry point boots the app window and its server" \
  "[[ $rc -eq 0 && \"\$(cat $T/win 2>/dev/null)\" == 'Standby|http://127.0.0.1:$UIPORT/?token='*'|state-ok' ]]" || tail -5 "$H/.standby/ui.log"
check "  and logs to ~/.standby/ui.log" "grep -q 'serving http://127.0.0.1:$UIPORT' $H/.standby/ui.log"
check "an old config's SSH host: moved to this Mac (local mode), the host gone" \
  "$REALPY -c \"import json,sys; c=json.load(open('$H/.standby/config.json')); sys.exit(not (c['agent']=={'mode':'local'} and 'host' not in c and 'mic_device' not in c and c['loopback_device']=='BlackHole 2ch'))\""
check "PATH line added to ~/.zshrc" "[[ \$(grep -c '.local/bin' $H/.zshrc) -eq 1 ]]"
check "private dirs are owner-only" "[[ \$(mode_of $H/.standby) == 700 && \$(mode_of $H/.standby/sessions) == 700 ]]"
run 2; rc=$?
check "re-run succeeds (update in place)" "[[ $rc -eq 0 ]]"
check "Claude Code plugin: this repo added as its marketplace, standby installed for every folder" \
  "grep -qx 'plugin marketplace add $REPO' $T/claude.args && grep -qx 'plugin install standby@standby --scope user' $T/claude.args && grep -q 'plugin standby@standby, in every folder' $T/out.1"
check "  a re-run asks the same again (harmless: Claude Code says it's already there)" \
  "[[ \$(grep -c 'plugin install standby@standby' $T/claude.args) -eq 2 ]] && grep -q 'plugin standby@standby, in every folder' $T/out.2"
check "  the repo is a marketplace with standby from lite/" \
  "$REALPY -c \"import json,sys; m=json.load(open('$REPO/.claude-plugin/marketplace.json')); sys.exit(not (m['name']=='standby' and m['plugins'][0]['name']=='standby' and m['plugins'][0]['source']=='./lite'))\""
touch "$T/claude-fails"; run 6; rc=$?; rm -f "$T/claude-fails"
check "  Claude Code refusing it: the install still succeeds, and says the two commands" \
  "[[ $rc -eq 0 ]] && grep -q \"couldn't add the plugin\" $T/out.6 && grep -q 'claude plugin install standby@standby --scope user' $T/out.6"
NOC="$T/noclaude"; mkdir -p "$NOC"; cp "$B"/* "$NOC"/; rm -f "$NOC/claude"
HOME="$H" PATH="$NOC:/usr/bin:/bin" SHELL=/bin/bash bash "$REPO/install.sh" --from-here > "$T/out.7" 2>&1; rc=$?
check "  no Claude Code yet: the install still succeeds, and says how to add it later" \
  "[[ $rc -eq 0 ]] && grep -q \"Claude Code isn't on this Mac yet\" $T/out.7 && grep -q 'claude plugin marketplace add' $T/out.7"
CC_VERSION=2.1.250 run 8; rc=$?
check "  Claude Code too old for the plugin (before 2.1.299): says so, and how to update" \
  "[[ $rc -eq 0 ]] && grep -q 'Claude Code 2.1.250 is too old for Standby: it needs 2.1.299 or later' $T/out.8 && grep -q 'claude update' $T/out.8"
check "    and never says /standby is there to type" \
  "! grep -q 'type /standby in Claude Code' $T/out.8 && grep -q \"plugin standby@standby added, but Claude Code 2.1.250 can't run it\" $T/out.8"
CC_VERSION=2.1.299 run 9
check "  2.1.299 itself: fine" "grep -q 'Claude Code: 2.1.299' $T/out.9 && grep -q 'type /standby in Claude Code' $T/out.9"
check "re-run does not duplicate the PATH line" "[[ \$(grep -c '.local/bin' $H/.zshrc) -eq 1 ]]"
check "re-run: the speech model is here, not fetched again" \
  "! grep -q 'fetching the speech model' $T/out.2 && grep -q 'speech model: Small, English' $T/out.2 && [[ \$(wc -l < $H/hf.args) -eq 2 ]]"
check "re-run keeps the migrated config" "! grep -q devbox.example.com $H/.standby/config.json && grep -q '\"mode\": \"local\"' $H/.standby/config.json"
check "a first install says nothing of asking again" "! grep -q 'permissions once more' $T/out.1"
check "  nor a rebuild with nothing changed (the same app: macOS keeps its permissions)" "! grep -q 'permissions once more' $T/out.2"
# An older build whose Info.plist differs (as when a key was added): macOS sees a new app.
"$REALPY" -c "import plistlib,sys; p=sys.argv[1]; d=plistlib.load(open(p,'rb')); d['OldKey']=1; plistlib.dump(d, open(p,'wb'))" \
  "$H/Applications/Standby.app/Contents/Info.plist"
run 5; rc=$?
check "an app that changed: macOS will ask for its permissions once more, and the install says so" \
  "[[ $rc -eq 0 ]] && grep -q \"macOS will ask for Standby's permissions once more\" $T/out.5"
check "  and what to do if it keeps asking with the switch already on: the button, or the reset" \
  "grep -q \"click \\\"It's on already\\\" on the call's sound in Standby\" $T/out.5 && grep -q 'tccutil reset ScreenCapture dev.standby.app' $T/out.5"
check "  neither said when nothing changed" "! grep -q 'tccutil reset' $T/out.2"
touch "$T/py2app-fails"; run 4; rc=$?
check "app build failure is not fatal" "[[ $rc -eq 0 ]]"
check "  and points to 'standby app' instead" "grep -q 'Open Standby:   standby app' $T/out.4 && [[ ! -e $H/Applications/Standby.app ]]"
check "  and dictation still works without its app" \
  "grep -q 'still works' $T/out.4 && [[ ! -e \"$H/Applications/Standby Dictation.app\" ]]"
# Python 3.12 or later only: the pinned numpy, scipy and onnxruntime have no wheels for 3.10 or
# 3.11. A Mac that has only 3.11 is told so (no Homebrew), or gets 3.12 from Homebrew.
P="$T/py311"; mkdir -p "$P" "$T/brewprefix/bin"
cp "$B/uname" "$B/sw_vers" "$B/df" "$B/sysctl" "$P/"
printf '#!/bin/bash\n[[ "$1" == "-V" ]] && { echo "Python 3.11.9"; exit 0; }\n[[ "$1" == "-c" ]] && exit 1\nexit 0\n' > "$P/python3"
cp "$P/python3" "$P/python3.11"; cp "$P/python3" "$P/python3.10"
cp "$B/python3.12" "$T/brewprefix/bin/python3.12"
printf '#!/bin/bash\necho "$*" >> "%s/brew.args"\n[[ "$1" == "--prefix" ]] && echo "%s/brewprefix"\nexit 0\n' "$T" "$T" > "$P/brew"
chmod +x "$P"/* "$T/brewprefix/bin"/*
mkdir -p "$T/onlybrewless"; cp "$P/uname" "$P/sw_vers" "$P/df" "$P/sysctl" "$P/python3" "$P/python3.11" "$T/onlybrewless/"
HOME="$H" PATH="$T/onlybrewless:/usr/bin:/bin" bash "$REPO/install.sh" --from-here > "$T/out.py1" 2>&1; rc=$?
check "only Python 3.11, no Homebrew: refused, saying 3.12 is needed" \
  "[[ $rc -ne 0 ]] && grep -q 'Python 3.12 is needed' $T/out.py1"
HOME="$H" PATH="$P:/usr/bin:/bin" SHELL=/bin/bash bash "$REPO/install.sh" --from-here > "$T/out.py2" 2>&1
check "  with Homebrew: it installs python@3.12, and does not use 3.10 or 3.11" \
  "grep -q 'install python@3.12' $T/brew.args && grep -q 'Installing Python 3.12 with Homebrew' $T/out.py2 && ! grep -q 'Python: Python 3.1[01]' $T/out.py2"
# The closing lines name the standby command; a Terminal opened before the install has no PATH for it yet.
check "the closing lines tell the user to open a new Terminal window first" \
  "grep -q 'standby help (open a new Terminal window first' $T/out.1"
HOME="$H" PATH="$B:$H/.local/bin:/usr/bin:/bin" SHELL=/bin/bash bash "$REPO/install.sh" --from-here > "$T/out.py3" 2>&1
check "  not when the command is already on this Terminal's PATH" \
  "grep -q 'Terminal:       standby help' $T/out.py3 && ! grep -q 'open a new Terminal window first' $T/out.py3"

# Which Macs: macOS 14 or later, room on the disk, and a word about memory; Python 3.12 or 3.13.
SW_VERS=13.6.9 run m1; rc=$?
check "macOS 13: refused, naming macOS 14 (Sonoma) and the version found" \
  "[[ $rc -ne 0 ]] && grep -q 'Standby needs macOS 14 (Sonoma) or later. This Mac has macOS 13.6.9' $T/out.m1 && grep -q 'Software Update' $T/out.m1"
SW_VERS= run m2; rc=$?
check "  a version it can't read: refused, not guessed" "[[ $rc -ne 0 ]] && grep -q 'macOS of an unknown version' $T/out.m2"
SW_VERS=15.1 run m3; rc=$?
check "  macOS 15: on it goes, and says so" "[[ $rc -eq 0 ]] && grep -q 'macOS 15.1 on Apple Silicon' $T/out.m3"
F="$T/fresh"; mkdir -p "$F"
HOME="$F" PATH="$B:/usr/bin:/bin" DF_KB=3145728 bash "$REPO/install.sh" --from-here > "$T/out.d1" 2>&1; rc=$?
check "a first install with 3 GB free: refused, saying how much is needed and how much there is" \
  "[[ $rc -ne 0 ]] && grep -q 'Standby needs about 6 GB free to install. This Mac has 3.0 GB free' $T/out.d1 && [[ ! -e $F/.standby/venv ]]"
DF_KB=3145728 run d2; rc=$?
check "  an update with 3 GB free: fine, it adds little" "[[ $rc -eq 0 ]] && ! grep -q 'GB free' $T/out.d2"
DF_KB=524288 run d3; rc=$?
check "  but not with half a GB: it needs 1 GB then" "[[ $rc -ne 0 ]] && grep -q 'needs about 1 GB free' $T/out.d3"
MEMSIZE=8589934592 run r1; rc=$?
check "an 8 GB Mac: installed, with a word that Small is used, and that 16 GB is better for calls" \
  "[[ $rc -eq 0 ]] && grep -q 'This Mac has 8 GB of memory: Standby uses its Small speech model (English only). 16 GB is better for calls.' $T/out.r1"
check "  not on a 16 GB Mac" "! grep -q 'Small speech model' $T/out.1"
Q="$T/py314"; mkdir -p "$Q"; cp "$B/uname" "$B/sw_vers" "$B/df" "$B/sysctl" "$Q/"
printf '#!/bin/bash\n[[ "$1" == "-V" ]] && { echo "Python 3.14.0"; exit 0; }\n[[ "$1" == "-c" ]] && exit 1\nexit 0\n' > "$Q/python3"
cp "$Q/python3" "$Q/python3.14"; chmod +x "$Q"/*
HOME="$H" PATH="$Q:/usr/bin:/bin" bash "$REPO/install.sh" --from-here > "$T/out.p4" 2>&1; rc=$?
check "only Python 3.14, no Homebrew: refused, 3.12 needed and 3.14 not supported yet" \
  "[[ $rc -ne 0 ]] && grep -q \"3.14 or later isn't supported yet\" $T/out.p4 && ! grep -q 'Python: Python 3.14' $T/out.p4"
# Python 3.13 whose packages don't install: said plainly, with the way to 3.12.
H13="$T/home313"; V13="$H13/.standby/venv/bin"; mkdir -p "$V13"; cp -R "$H/.standby/models" "$H13/.standby/"
printf '#!/bin/bash\nexit 0\n' > "$V13/pip"
printf '#!/bin/bash\n[[ "$*" == *"import numpy, sounddevice"* ]] && exit 1\nexit 0\n' > "$V13/python"
chmod +x "$V13"/*
HOME="$H13" PATH="$B:/usr/bin:/bin" bash "$REPO/install.sh" --from-here > "$T/out.p3" 2>&1; rc=$?
check "Python 3.13 whose packages don't install: refused, saying to use 3.12, and how" \
  "[[ $rc -ne 0 ]] && grep -q \"don't install on Python 3.13 on this Mac. Use Python 3.12\" $T/out.p3 && grep -q 'brew install python@3.12' $T/out.p3"

# Stub uname rather than trusting the host's: on macOS the real one says Darwin,
# so install.sh would get past the OS gate and fail later for another reason.
mkdir -p "$T/linuxbin"
printf '#!/bin/bash\necho Linux\n' > "$T/linuxbin/uname"; chmod +x "$T/linuxbin/uname"
check "Linux is refused with a clear message" \
  "! HOME=$H PATH=$T/linuxbin:/usr/bin:/bin bash $REPO/install.sh --from-here >$T/out.3 2>&1 && grep -q 'Standby runs on macOS' $T/out.3"

# Only Standby's own checkout, on main, installs (an install from the wrong
# folder would make the apps run work in progress). In a throwaway git repo,
# with install.sh cut off right after its checks, so nothing past them can run.
G="$T/which"; mkdir -p "$G/home"
git init -q -b main "$G/repo"
sed '/^say "Checking this Mac"/,$d' "$REPO/install.sh" > "$G/repo/install.sh"
echo 'echo PAST-THE-CHECK' >> "$G/repo/install.sh"
git -C "$G/repo" add install.sh && git -C "$G/repo" -c user.email=t@example.com -c user.name=t commit -qm init
gi() { HOME="$G/home" bash "$@" > "$G/out" 2>&1; }
gi "$G/repo/install.sh"
check "the main checkout on main installs" "grep -q PAST-THE-CHECK $G/out" || cat "$G/out"
git -C "$G/repo" checkout -q -b wip
gi "$G/repo/install.sh"
check "  on a work-in-progress branch: refused before anything changes, saying how to fix it" \
  "! grep -q PAST-THE-CHECK $G/out && grep -q 'on branch wip, not main' $G/out && grep -q 'git checkout main && ./install.sh' $G/out" || cat "$G/out"
git -C "$G/repo" checkout -q main
git -C "$G/repo" worktree add -q "$G/teammate" -b feature
gi "$G/teammate/install.sh"
check "  from a git worktree: refused, naming it and the checkout to install from" \
  "! grep -q PAST-THE-CHECK $G/out && grep -q \"is a git worktree (branch feature)\" $G/out && grep -q 'cd .*/repo && ./install.sh' $G/out" || cat "$G/out"
gi "$G/teammate/install.sh" --from-here
check "  --from-here: let through, for testing a branch" "grep -q PAST-THE-CHECK $G/out" || cat "$G/out"
rm -rf "$T"
echo; [[ $fails -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES: $fails"; exit 1; }
