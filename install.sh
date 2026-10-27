#!/usr/bin/env bash
# Standby installer for macOS (Apple Silicon). Safe to re-run: it updates in place, and
# Python packages stay at the versions in constraints.txt (adding any that are missing).
#
#   ./install.sh                 install or update
#   ./install.sh --from-here     from a branch or worktree that isn't the main checkout
#                                (the team's own testing only)
#
# What it does: a private Python venv under ~/.standby, the on-device speech and
# speaker models, the `standby` command, the meeting skill for Claude Code,
# ~/Applications/Standby.app and Standby Dictation.app. Nothing is sent anywhere.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HOME/.standby"
VENV="$ROOT/venv"
BINDIR="$HOME/.local/bin"
APP="$HOME/Applications/Standby.app"
DAPP="$HOME/Applications/Standby Dictation.app"
SPEAKER_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx"
SPEAKER_SUM="ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"
TURN_MODEL="smart-turn-v3.2-cpu.onnx"     # voice mode: has the speaker finished? (pipecat-ai, BSD-2)
TURN_URL="https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/$TURN_MODEL"
TURN_SUM="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
VAD_MODEL="silero_vad_v5.1.2.onnx"       # voice mode: is talking over the voice speech? (snakers4, MIT)
VAD_URL="https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx"
VAD_SUM="2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mxx %s\033[0m\n' "$*" >&2; exit 1; }


# Only Standby's own checkout, on main, installs. The apps run straight from the
# folder they were installed from, so an install from a git worktree or a
# work-in-progress branch would make the user's Standby run that branch (it did,
# 2026-10-08). Checked before anything is changed. A download without git skips it.
FROM_HERE=no
for a in "$@"; do [[ "$a" == "--from-here" ]] && FROM_HERE=yes; done
if [[ "$FROM_HERE" == no ]] && git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_DIR_HERE="$(cd "$REPO" && cd "$(git rev-parse --git-dir)" && pwd -P)"
  GIT_COMMON="$(cd "$REPO" && cd "$(git rev-parse --git-common-dir)" && pwd -P)"
  BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  MAIN_CHECKOUT="$(dirname "$GIT_COMMON")"
  SHOW_MAIN="${MAIN_CHECKOUT/#$HOME/~}"
  if [[ "$GIT_DIR_HERE" != "$GIT_COMMON" ]]; then
    die "Not installed: ${REPO/#$HOME/~} is a git worktree (branch $BRANCH), not Standby's own checkout.
   Install from Standby's own checkout instead:
     cd $SHOW_MAIN && ./install.sh"
  elif [[ "$BRANCH" != "main" ]]; then
    die "Not installed: ${REPO/#$HOME/~} is on branch $BRANCH, not main.
   Switch it back to main first:
     cd $SHOW_MAIN && git checkout main && ./install.sh"
  fi
fi

say "Checking this Mac"
[[ "$(uname -s)" == "Darwin" ]] || die "Standby runs on macOS."
[[ "$(uname -m)" == "arm64" ]] || die "Standby needs an Apple Silicon Mac (on-device speech uses MLX)."
# macOS 14 (Sonoma) or later: the pinned packages (constraints.txt: mlx, numpy, scipy, onnxruntime,
# torch) are built for nothing older, and an older macOS failed deep in the package install.
MACOS="$(sw_vers -productVersion 2>/dev/null || true)"
[[ "${MACOS%%.*}" =~ ^[0-9]+$ && "${MACOS%%.*}" -ge 14 ]] \
  || die "Standby needs macOS 14 (Sonoma) or later. This Mac has macOS ${MACOS:-of an unknown version}:
   update it in System Settings > General > Software Update, then run this again."
ok "macOS $MACOS on Apple Silicon"

# Room for what it puts on this Mac: the Python packages (about 1.5 GB), the speech model (up
# to 2.5 GB), Standby's voice (about 240 MB) and the smaller models, with some to spare. An
# update adds little: 1 GB is enough then.
NEED_GB=6; [[ -x "$VENV/bin/python" ]] && NEED_GB=1
FREE_KB="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
if [[ "$FREE_KB" =~ ^[0-9]+$ ]] && (( FREE_KB < NEED_GB * 1024 * 1024 )); then
  die "Standby needs about $NEED_GB GB free to install. This Mac has $(awk -v k="$FREE_KB" 'BEGIN {printf "%.1f", k / 1048576}') GB free:
   make some room, then run this again."
fi
# Memory: at 9 GB or less (an 8 GB Mac) Standby uses the Small speech model, as speechmodel.py
# decides, measured the same way. It works; it is said here so it is no surprise.
# (Shown as Apple names it: an 8 GB Mac has 8 GiB, which is 8.6 GB by speechmodel.py's count.)
read -r MEM_SHOWN MEM_SMALL <<<"$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d %d", $1 / 1073741824, ($1 > 0 && $1 / 1e9 <= 9)}')"
if [[ "${MEM_SMALL:-0}" == 1 ]]; then
  warn "This Mac has $MEM_SHOWN GB of memory: Standby uses its Small speech model (English only). 16 GB is better for calls."
fi

# Python 3.12, or 3.13 if the pinned packages install on it (checked after the install below).
# Not 3.11 or earlier: the pinned numpy, scipy and onnxruntime have no builds for those. Not 3.14
# or later yet: nothing pinned has been tried on it. None on this Mac: a prebuilt Python 3.12 of
# Standby's own, in ~/.standby/python, put there by uv (Astral's tool; its release pinned and checked
# here, like the models): a few seconds, no Homebrew and no admin password. (Homebrew builds Python
# from source on macOS 14: about 11 minutes, measured.)
UV_VERSION="0.12.24"
UV_URL="${STANDBY_UV_URL:-https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz}"
UV_SUM="${STANDBY_UV_SUM:-0c4346de7abdb49495b393b9ec809fe387aa43e586be20fecb972216c1e71732}"
# Homebrew's Python, where there is one, found even when this Terminal hasn't been told about Homebrew
# yet (the "Next steps" its installer prints), so it is used rather than a second Python fetched.
HOMEBREW="${STANDBY_HOMEBREW:-/opt/homebrew}"       # its place on Apple Silicon (tests point elsewhere)
[[ -x "$HOMEBREW/bin/brew" && ":$PATH:" != *":$HOMEBREW/bin:"* ]] && PATH="$PATH:$HOMEBREW/bin"     # last: a Python on PATH still wins
PY=""
[[ "${STANDBY_PREBUILT_PYTHON:-}" == 1 ]] || for c in python3.12 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info[:2] not in ((3, 12), (3, 13)))'; then
    PY="$(command -v "$c")"; break
  fi
done
if [[ -z "$PY" ]]; then
  say "Python 3.12 for Standby (prebuilt, in ${ROOT/#$HOME/~}/python)"
  export UV_CACHE_DIR="$ROOT/cache/uv" UV_PYTHON_INSTALL_DIR="$ROOT/python" UV_NO_CONFIG=1
  mkdir -p "$ROOT/bin" "$UV_PYTHON_INSTALL_DIR"
  if ! "$ROOT/bin/uv" --version 2>/dev/null | grep -q "^uv $UV_VERSION "; then
    UVTMP="$(mktemp -d)"
    curl -fsSL -o "$UVTMP/uv.tar.gz" "$UV_URL" \
      || { rm -rf "$UVTMP"; die "couldn't download uv, which brings Python 3.12: check the internet connection, then run this again"; }
    echo "$UV_SUM  $UVTMP/uv.tar.gz" | shasum -a 256 -c >/dev/null 2>&1 \
      || { rm -rf "$UVTMP"; die "uv's download didn't match its checksum: run this again"; }
    tar xzf "$UVTMP/uv.tar.gz" -C "$UVTMP" && mv "$UVTMP/uv-aarch64-apple-darwin/uv" "$ROOT/bin/uv"
    rm -rf "$UVTMP"
  fi
  "$ROOT/bin/uv" python install 3.12 --no-bin --no-registry >/dev/null 2>&1 \
    || die "couldn't install Python 3.12 (with uv): check the internet connection, then run this again"
  PY="$("$ROOT/bin/uv" python find 3.12 --managed-python --no-project 2>/dev/null || true)"
  [[ -x "$PY" ]] || die "Python 3.12 didn't install in ${ROOT/#$HOME/~}/python: run this again"
fi
ok "Python: $("$PY" -V)"

# Claude Code 2.1.299 or later: the plugin (lite/) is a hooks module, which older releases don't load or
# run (measured on published releases), though `claude plugin install` says it worked on every one.
CC_MIN="2.1.299"
CC_OLD=no
# Claude Code's own installer puts it in ~/.local/bin and may leave that off the PATH: found there too
CLAUDE="$(command -v claude 2>/dev/null || true)"
[[ -z "$CLAUDE" && -x "$BINDIR/claude" ]] && CLAUDE="$BINDIR/claude"
if [[ -n "$CLAUDE" ]]; then
  CC_VER="$("$CLAUDE" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  if [[ -n "$CC_VER" && "$(printf '%s\n%s\n' "$CC_MIN" "$CC_VER" | sort -V | head -1)" != "$CC_MIN" ]]; then
    CC_OLD=yes
    warn "Claude Code $CC_VER is too old for Standby: it needs $CC_MIN or later. Update it with
     claude update
   then start a new Claude Code session. Everything else installs now; /standby works once it's updated."
  else
    ok "Claude Code: $("$CLAUDE" --version 2>/dev/null | head -1)"
  fi
else
  warn "Claude Code isn't on this Mac yet: Standby needs it (install it, then run this again)."
fi

say "Python environment ($VENV)"
mkdir -p "$ROOT" "$ROOT/sessions" "$ROOT/models"
chmod 700 "$ROOT" "$ROOT/sessions"
[[ -d "$VENV" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel
echo "   installing packages (a few minutes the first time)..."
# The versions in constraints.txt, the set that works, and no --upgrade: a fresh install
# gets exactly those, and running this again keeps what is there and adds what is missing.
PINS=(-c "$REPO/constraints.txt")
# pyobjc-framework-ScreenCaptureKit: a call's own sound, straight from macOS (macOS 14+).
# pyobjc-framework-CoreML: who's speaking, on the Neural Engine (macOS 14+).
"$VENV/bin/pip" install --progress-bar off "${PINS[@]}" numpy sounddevice parakeet-mlx mlx-whisper sherpa-onnx pywebview onnxruntime \
  pyobjc-framework-AVFoundation pyobjc-framework-ScreenCaptureKit pyobjc-framework-CoreML | grep -E "^(Successfully|ERROR)" || true
if ! "$VENV/bin/python" -c "import numpy, sounddevice, parakeet_mlx, sherpa_onnx, webview" 2>/dev/null; then
  if "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))' 2>/dev/null; then
    die "The packages Standby uses don't install on Python 3.13 on this Mac. Use a Python 3.12 of Standby's own:
     rm -rf ${VENV/#$HOME/~} && STANDBY_PREBUILT_PYTHON=1 ./install.sh"
  fi
  die "package install failed. Re-run with: $VENV/bin/pip install -c $REPO/constraints.txt numpy sounddevice parakeet-mlx mlx-whisper sherpa-onnx pywebview"
fi
ok "speech (parakeet-mlx), speakers (sherpa-onnx), app window (pywebview)"
"$VENV/bin/python" -c "import onnxruntime" 2>/dev/null \
  || warn "onnxruntime did not install: voice mode will tell when you finish speaking by pauses alone"
"$VENV/bin/python" -c "import AVFoundation" 2>/dev/null \
  || warn "AVFoundation for Python did not install: in voice mode, use headphones or tap to interrupt Claude"
# Voice mode's own voice: Pocket TTS by Kyutai (CC BY 4.0) on the GPU, about 145 MB of packages.
# mlx-audio pinned (constraints.txt): mac/tts.py uses its Pocket TTS directly. The model (about
# 240 MB) comes from Hugging Face the first time voice mode opens, not here.
"$VENV/bin/pip" install --progress-bar off "${PINS[@]}" mlx-audio sentencepiece | grep -E "^(Successfully|ERROR)" || true
if "$VENV/bin/python" -c "import mlx_audio, sentencepiece" 2>/dev/null; then
  ok "voice mode's voice (Pocket TTS; its model comes the first time voice mode opens)"
else
  warn "Pocket TTS did not install: voice mode speaks with a macOS voice"
fi

say "Models (downloaded once, then used offline)"
if [[ -s "$ROOT/models/nemo_en_titanet_small.onnx" ]]; then
  ok "speaker model already present"
else
  curl -fL $([[ -t 2 ]] && echo --progress-bar || echo -sS) -o "$ROOT/models/nemo_en_titanet_small.onnx.part" "$SPEAKER_URL"
  printf '%s  %s\n' "$SPEAKER_SUM" "$ROOT/models/nemo_en_titanet_small.onnx.part" | shasum -a 256 -c --status \
    || { rm -f "$ROOT/models/nemo_en_titanet_small.onnx.part"; die "the speaker model's download didn't match its checksum: re-run install.sh"; }
  mv "$ROOT/models/nemo_en_titanet_small.onnx.part" "$ROOT/models/nemo_en_titanet_small.onnx"
  ok "speaker model (40 MB)"
fi
if [[ -s "$ROOT/models/$TURN_MODEL" ]]; then
  ok "end-of-turn model already present"
elif curl -fsSL -o "$ROOT/models/$TURN_MODEL.part" "$TURN_URL" \
     && printf '%s  %s\n' "$TURN_SUM" "$ROOT/models/$TURN_MODEL.part" | shasum -a 256 -c --status \
     && mv "$ROOT/models/$TURN_MODEL.part" "$ROOT/models/$TURN_MODEL"; then
  ok "end-of-turn model for voice mode (9 MB)"
else
  rm -f "$ROOT/models/$TURN_MODEL.part"
  warn "could not download the end-of-turn model: voice mode will judge by pauses alone"
fi
if [[ -s "$ROOT/models/$VAD_MODEL" ]]; then
  ok "speech detection model already present"
elif curl -fsSL -o "$ROOT/models/$VAD_MODEL.part" "$VAD_URL" \
     && printf '%s  %s\n' "$VAD_SUM" "$ROOT/models/$VAD_MODEL.part" | shasum -a 256 -c --status \
     && mv "$ROOT/models/$VAD_MODEL.part" "$ROOT/models/$VAD_MODEL"; then
  ok "speech detection model for talking over the voice (2 MB)"
else
  rm -f "$ROOT/models/$VAD_MODEL.part"
  warn "could not download the speech detection model: talking over the voice is judged by loudness alone"
fi
# Who's speaking: NVIDIA's Nemotron-3-Diarization in Fluid Inference's Core ML build
# (OpenMDW-1.1), on the Neural Engine. Hugging Face, a fixed revision, checked by its sha256.
# macOS 14 or later; without it, speakers are matched by voice prints alone, as before.
DIAR="$ROOT/models/nemotron-3-diarization"
DIAR_URL="https://huggingface.co/FluidInference/nemotron-3-diarization-coreml/resolve/25a90f97f254428d4b30374b76af9c74fdee8327"
DIAR_FILES="monolithic/v2/Nemotron3Diarizer_fast.mlmodelc/coremldata.bin
monolithic/v2/Nemotron3Diarizer_fast.mlmodelc/model.mil
monolithic/v2/Nemotron3Diarizer_fast.mlmodelc/analytics/coremldata.bin
monolithic/v2/Nemotron3Diarizer_fast.mlmodelc/weights/weight.bin
learnable_sil_emb.bin"
DIAR_SUMS="5efb213341eb1d772d662d852a9a9cf9f45372a599862e16035880742c491967  Nemotron3Diarizer_fast.mlmodelc/weights/weight.bin
d4417b3c0eabdf7c47032fac2b5b5a7ee83d819a6ddda8fd8eaf74e2b5cc4ac7  learnable_sil_emb.bin"
if [[ -s "$DIAR/Nemotron3Diarizer_fast.mlmodelc/weights/weight.bin" && -s "$DIAR/learnable_sil_emb.bin" ]]; then
  ok "speaker diarization model already present"
elif [[ "$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)" -lt 14 ]]; then
  warn "speaker diarization needs macOS 14: speakers are matched by voice prints alone"
else
  rm -rf "$DIAR.part"
  got=yes
  while IFS= read -r f; do
    dst="$DIAR.part/${f#monolithic/v2/}"
    mkdir -p "$(dirname "$dst")"
    curl -fsSL -o "$dst" "$DIAR_URL/$f" || { got=no; break; }
  done <<< "$DIAR_FILES"
  if [[ $got == yes ]] && (cd "$DIAR.part" && printf '%s\n' "$DIAR_SUMS" | shasum -a 256 -c --status); then
    rm -rf "$DIAR" && mv "$DIAR.part" "$DIAR"
    ok "speaker diarization model (190 MB; ready about a minute into the first recording)"
  else
    rm -rf "$DIAR.part"
    warn "could not download the speaker diarization model: speakers are matched by voice prints alone"
  fi
fi
# The speech model Settings > Speech would choose here (Small on an 8 GB Mac,
# Standard otherwise, the English one on an English Mac), pinned, unless it is here. Public models: the
# download library's advice to sign in for higher rate limits is not for the user.
HF_HUB_VERBOSITY=error "$VENV/bin/python" - "$REPO/mac" "$ROOT/config.json" <<'PYCODE'
import json, sys
sys.path.insert(0, sys.argv[1])
import speechmodel
try:
    cfg = json.load(open(sys.argv[2]))
except Exception:
    cfg = {}
speechmodel.migrate(cfg)                 # a stored size no longer offered: for this fetch;
                                         # the app writes it back at its next start
m = speechmodel.MODELS[speechmodel.chosen(cfg)]
if speechmodel.local(speechmodel.chosen(cfg)) is None:
    print("   fetching the speech model, %s (about %.1f GB, first time only)..." % (m.words, m.bytes / 1e9))
    speechmodel.fetch(speechmodel.chosen(cfg))
print("   \033[32m✓\033[0m speech model: %s (%s; Settings > Recording to change it)" % (m.words, m.repo.split("/")[1]))
PYCODE

say "Commands"
mkdir -p "$BINDIR"
chmod +x "$REPO/mac/standby" "$REPO/plugin/bin/standby-agent" "$REPO/plugin/bin/standby-ctx"
ln -sfn "$REPO/mac/standby" "$BINDIR/standby"
ln -sfn "$REPO/plugin/bin/standby-ctx" "$BINDIR/standby-ctx"
ok "standby, standby-ctx -> $BINDIR"
NEWTERM=""
case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *) NEWTERM=" (open a new Terminal window first: this one doesn't have the standby command yet)"
     if ! grep -qF '.local/bin' "$HOME/.zshrc" 2>/dev/null; then
       printf '\n# added by the Standby installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
       ok "added ~/.local/bin to PATH in ~/.zshrc (new terminals)"
     fi ;;
esac

say "Claude Code plugin"
# This repo is a plugin marketplace (.claude-plugin/marketplace.json): the plugin "standby" (lite/),
# installed for every folder. Added and installed again on a re-run, which changes nothing. It loads
# in place from lite/: after a git pull, a new Claude Code session (or /reload-plugins) has it.
PLUGIN_CMDS="claude plugin marketplace add \"$REPO\" && claude plugin install standby@standby --scope user"
if [[ -z "$CLAUDE" ]]; then
  warn "Claude Code isn't on this Mac yet: once it is, add Standby to it with"
  echo "     $PLUGIN_CMDS"
elif "$CLAUDE" plugin marketplace add "$REPO" >/dev/null 2>&1 && "$CLAUDE" plugin install standby@standby --scope user >/dev/null 2>&1; then
  if [[ "$CC_OLD" == yes ]]; then           # added, but this Claude Code can't run it: never "type /standby"
    warn "plugin standby@standby added, but Claude Code $CC_VER can't run it: update it with 'claude update' (needs $CC_MIN or later)"
  else
    ok "plugin standby@standby, in every folder: type /standby in Claude Code (a new session, or /reload-plugins)"
  fi
else
  warn "couldn't add the plugin to Claude Code; add it with"
  echo "     $PLUGIN_CMDS"
fi

say "Configuration"
"$VENV/bin/python" - "$ROOT/config.json" <<'PYCODE'
import json, os, sys
p = sys.argv[1]
cfg = {}
if os.path.exists(p):
    cfg = json.load(open(p))
agent = cfg.get("agent") or {}
agent["mode"] = "local"                      # Claude Code runs on this Mac: an SSH host saved before goes
for k in ("host", "remote_dir"):
    agent.pop(k, None)
cfg["agent"] = agent
for k in ("host", "remote_dir"):
    cfg.pop(k, None)
if isinstance(cfg.get("mic_device"), int):   # saved indices are unreliable
    cfg.pop("mic_device")
cfg.setdefault("mode", "auto")
json.dump(cfg, open(p, "w"), indent=2)
print("   \033[32m✓\033[0m %s" % p)
PYCODE

# What macOS keys an app's permissions on: its ad-hoc signature's CDHash (else its Info.plist).
# A rebuild with nothing changed gives the same one, and keeps them; a new one is asked once more.
app_mark() {
  [[ -d "$1" ]] || return 0
  { codesign -dvvv "$1" 2>&1 | sed -n 's/^CDHash=//p' | grep . ; } \
    || { shasum "$1/Contents/Info.plist" 2>/dev/null | cut -d' ' -f1; } || true
}
ASK_AGAIN=()

say "Standby.app"
# A real app bundle (py2app, alias mode: runs the files in this folder in place),
# so macOS shows Standby in the Dock and asks for the microphone as Standby.
BUILD="$ROOT/app-build"
rm -rf "$BUILD"
mkdir -p "$BUILD"
# The icons: the orb for Standby, the mic for Standby Dictation, on navy macOS
# rounded squares (mac/icons.py, numpy only).
"$VENV/bin/python" "$REPO/mac/icons.py" "$BUILD" || warn "could not draw the app icons; the apps work without them"
SITE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
cat > "$BUILD/Standby.py" <<PYMAIN
# Generated by install.sh: the Standby.app entry point.
import os, site, sys
log = open(os.path.expanduser("~/.standby/ui.log"), "a", buffering=1)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
sys.stdout = sys.stderr = log
site.addsitedir("$SITE")
sys.path.insert(0, "$REPO/mac")
sys.executable = "$VENV/bin/python"
if "--window" not in sys.argv:
    sys.argv.append("--window")
import ui
sys.exit(ui.main())
PYMAIN
cat > "$BUILD/setup.py" <<'PYSETUP'
import os
from setuptools import setup
WHY = "So a call can read the project you’re working on in Claude Code."
plist = {
    "CFBundleName": "Standby", "CFBundleDisplayName": "Standby",
    "CFBundleIdentifier": "dev.standby.app",
    "CFBundleShortVersionString": "0.9", "CFBundleVersion": "0.9",
    "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
    "NSMicrophoneUsageDescription":
        "Standby transcribes your meeting on this Mac so your Claude Code can answer questions about it.",
    # macOS asks for these when a call's Claude reads a project kept in one of them: its words say why
    **{key: WHY for key in ("NSDesktopFolderUsageDescription", "NSDocumentsFolderUsageDescription",
                            "NSDownloadsFolderUsageDescription", "NSRemovableVolumesUsageDescription",
                            "NSNetworkVolumesUsageDescription")},
}
opts = {"alias": True, "argv_emulation": False, "plist": plist}
if os.path.exists("Standby.icns"):
    opts["iconfile"] = "Standby.icns"
setup(app=["Standby.py"], name="Standby", options={"py2app": opts})
PYSETUP
if "$VENV/bin/pip" install --quiet "${PINS[@]}" py2app >"$BUILD/build.log" 2>&1 &&
   (cd "$BUILD" && "$VENV/bin/python" setup.py py2app -A >>"$BUILD/build.log" 2>&1) &&
   [[ -d "$BUILD/dist/Standby.app" ]]; then
  WAS="$(app_mark "$APP")"
  rm -rf "$APP"
  mkdir -p "$(dirname "$APP")"
  mv "$BUILD/dist/Standby.app" "$APP"
  touch "$APP"
  [[ -n "$WAS" && "$WAS" != "$(app_mark "$APP")" ]] && ASK_AGAIN+=("Standby")
  ok "$APP  (open it from Spotlight, Finder or the Dock)"
  OPEN="open ~/Applications/Standby.app     (or from Terminal: standby app)"
else
  rm -rf "$APP"
  warn "could not build Standby.app (log: $BUILD/build.log)."
  warn "Everything else works: open the app window from Terminal with: standby app"
  warn "(macOS then asks for its permissions as Terminal, not Standby)"
  OPEN="standby app"
fi

say "Standby Dictation.app"
# Dictation in any app: a second, menu-bar-only app (no Dock icon), so it can run
# while Standby's window is closed and macOS asks for its permissions (microphone,
# Input Monitoring, Accessibility) as "Standby Dictation".
DBUILD="$BUILD/dictation"
mkdir -p "$DBUILD"
[[ -f "$BUILD/Dictation.icns" ]] && cp "$BUILD/Dictation.icns" "$DBUILD/"
cat > "$DBUILD/StandbyDictation.py" <<PYMAIN
# Generated by install.sh: the Standby Dictation.app entry point.
import os, site, sys
log = open(os.path.expanduser("~/.standby/dictation.log"), "a", buffering=1)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
sys.stdout = sys.stderr = log
site.addsitedir("$SITE")
sys.path.insert(0, "$REPO/mac")
sys.executable = "$VENV/bin/python"
import dictation
sys.exit(dictation.main(["run"]))
PYMAIN
cat > "$DBUILD/setup.py" <<'PYSETUP'
import os
from setuptools import setup
plist = {
    "CFBundleName": "Standby Dictation", "CFBundleDisplayName": "Standby Dictation",
    "CFBundleIdentifier": "dev.standby.dictation",
    "CFBundleShortVersionString": "0.9", "CFBundleVersion": "0.9",
    "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
    "LSUIElement": True,
    "NSMicrophoneUsageDescription":
        "Standby Dictation turns what you say into text on this Mac, in whatever app you are typing in.",
}
opts = {"alias": True, "argv_emulation": False, "plist": plist}
if os.path.exists("Dictation.icns"):
    opts["iconfile"] = "Dictation.icns"
setup(app=["StandbyDictation.py"], name="Standby Dictation", options={"py2app": opts})
PYSETUP
if (cd "$DBUILD" && "$VENV/bin/python" setup.py py2app -A >>"$BUILD/build.log" 2>&1) &&
   [[ -d "$DBUILD/dist/Standby Dictation.app" ]]; then
  WAS="$(app_mark "$DAPP")"
  rm -rf "$DAPP"
  mkdir -p "$(dirname "$DAPP")"
  mv "$DBUILD/dist/Standby Dictation.app" "$DAPP"
  touch "$DAPP"
  [[ -n "$WAS" && "$WAS" != "$(app_mark "$DAPP")" ]] && ASK_AGAIN+=("Standby Dictation")
  ok "$DAPP  (turn it on with: standby dictation on)"
  # Already on: restart it, so it runs this version.
  if [[ -f "$HOME/Library/LaunchAgents/dev.standby.dictation.plist" ]] &&
     launchctl kickstart -k "gui/$(id -u)/dev.standby.dictation" >/dev/null 2>&1; then
    ok "restarted Standby Dictation"
  fi
else
  rm -rf "$DAPP"
  warn "could not build Standby Dictation.app (log: $BUILD/build.log)."
  warn "'standby dictation on' still works: it runs the script instead of the app (macOS then asks for its"
  warn "permissions as Python, not Standby Dictation)."
fi


say "Done"
if [[ ${#ASK_AGAIN[@]} -gt 0 ]]; then
  # The app changed in a way macOS sees (its Info.plist, or where it was built from).
  warn "macOS will ask for $(printf "%s's and " "${ASK_AGAIN[@]}" | sed 's/ and *$//') permissions once more"
  # macOS keeps a permission per copy of the app, so a switch that is already on may be the
  # old copy's: what to do then, for the one that records the call.
  for a in "${ASK_AGAIN[@]}"; do
    if [[ "$a" == "Standby" ]]; then
      echo "     If it keeps asking with Standby already switched on in Screen & System Audio Recording,"
      echo "     the switch is the old copy's: click \"It's on already\" on the call's sound in Standby,"
      echo "     or run: tccutil reset ScreenCapture dev.standby.app, then allow it again."
    fi
  done
fi
cat <<EOF
   Open Standby:   $OPEN
   First run:      start a meeting or a call from Claude Code, in the folder you work in
   Dictation:      standby dictation on     (speak into any app, instead of macOS Dictation)
   Terminal:       standby help$NEWTERM

   macOS will ask, the first time each is needed; choose Allow:
     Microphone                             your first meeting or call
     Screen & System Audio Recording        the first time an app plays sound in a meeting: Standby
                                            takes only the sound, never the screen; macOS then reopens it
     Microphone, Input Monitoring and       when you turn dictation on (Standby Dictation: to hear you,
     Accessibility                          notice the F5 key, and paste the text)
   Changed your mind: System Settings > Privacy & Security.
EOF
