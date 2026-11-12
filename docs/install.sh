#!/bin/bash
# Standby in one command:
#
#   curl -fsSL https://mksadoughi.github.io/standby/install.sh | bash
#
# It checks this Mac first, then fetches Standby's code from GitHub (the main branch, over HTTPS, from
# github.com only) into ~/.standby/source and runs its installer, install.sh, which says what it fetches as it
# goes. Safe to run again: that updates Standby. No sudo, and nothing is sent anywhere. Already installed from a
# git clone of your own: that clone is updated instead (git pull), never a second copy made.
#
# Read it first: https://github.com/mksadoughi/standby/blob/main/docs/install.sh
# Remove Standby: the README's "Update or uninstall".
set -euo pipefail

REPO_URL="https://github.com/mksadoughi/standby"
REF="${STANDBY_REF:-main}"               # a branch or tag of that repository; main is the released one
ROOT="$HOME/.standby"
DEST="$ROOT/source"                      # the code installed from (the apps run from their own copy of it)
CC_MIN="2.1.299"                         # the plugin is a hooks module, which older Claude Code doesn't load

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mxx %s\033[0m\n' "$*" >&2; exit 1; }
older() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" != "$2" ]]; }      # older VERSION THAN

# All of it in one function, run on the last line: piped into bash, the whole script is read before any of it runs.
main() {
  [[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "STANDBY_REF isn't a branch or tag name: $REF"

  say "Checking this Mac"
  export STANDBY_ONE_LINE_CHECKS_SHOWN=1         # install.sh, run below, checks again: this heading, macOS and Claude Code it doesn't show twice
  [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || die "Standby needs a Mac with Apple Silicon (M1 or later)."
  local macos; macos="$(sw_vers -productVersion 2>/dev/null || true)"
  [[ -n "$macos" ]] && ! older "$macos" 14.4 \
    || die "Standby needs macOS 14.4 or later; this Mac has ${macos:-an unknown version}. Update it in System Settings > General > Software Update."
  ok "macOS $macos on Apple Silicon"

  local claude; claude="$(command -v claude 2>/dev/null || true)"
  [[ -z "$claude" && -x "$HOME/.local/bin/claude" ]] && claude="$HOME/.local/bin/claude"
  [[ -n "$claude" ]] || die "Claude Code isn't installed. Install it (https://code.claude.com/docs/en/overview), sign in, then run this again."
  local ver; ver="$("$claude" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  [[ -n "$ver" ]] && older "$ver" "$CC_MIN" && die "Claude Code $ver is too old: Standby needs $CC_MIN or later. Run: claude update, then run this again."
  local auth; auth="$("$claude" auth status --json 2>/dev/null || true)"
  if grep -qE '"loggedIn": *false' <<<"$auth"; then
    die "Claude Code isn't signed in. Run: claude, sign in (/login), then run this again."
  elif grep -qE '"loggedIn": *true' <<<"$auth"; then
    ok "Claude Code ${ver:-} (signed in)"
  else
    warn "Claude Code ${ver:-}: couldn't tell whether it's signed in (Standby needs it signed in)"
  fi

  local need=6 free; [[ -x "$ROOT/venv/bin/python" ]] && need=1           # an update adds little
  free="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ "$free" =~ ^[0-9]+$ ]] && (( free < need * 1024 * 1024 )); then
    die "Standby needs about $need GB free; this Mac has $(awk -v k="$free" 'BEGIN {printf "%.1f", k / 1048576}') GB. Free some space, then run this again."
  fi
  ok "$need GB free or more"

  # Installed before from a git clone of the user's own (the copy of the code says which folder): that one is
  # updated and installed, never a second copy beside it. Only a clean main is pulled; anything else is theirs.
  local src; src="$(cat "$ROOT/code/.source" 2>/dev/null || true)"
  if [[ -n "$src" && "$src" != "$DEST" && -f "$src/install.sh" && -e "$src/.git" ]]; then
    local branch changes
    branch="$(git -C "$src" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    changes="$(git -C "$src" status --porcelain --untracked-files=no 2>/dev/null || echo unknown)"
    [[ "$branch" == main && -z "$changes" ]] \
      || die "Standby is installed from your clone at ${src/#$HOME/~} (branch $branch$([[ -n "$changes" ]] && echo ", with changes of your own")). Update it there: cd ${src/#$HOME/~} && git pull && ./install.sh"
    say "Updating your clone at ${src/#$HOME/~}"
    git -C "$src" pull --ff-only || die "git pull didn't finish in ${src/#$HOME/~}: see above, then run this again."
    bash "$src/install.sh" </dev/null           # it ends with what to do next
    return
  fi

  # A fresh copy of the code, swapped in only once all of it is here: a download cut short changes nothing.
  if [[ -x "$ROOT/venv/bin/python" ]]; then      # an update: the packages and models are here already
    say "Downloading Standby ($REF, about 12 MB)"
  else
    say "Downloading Standby ($REF, about 12 MB; then about 4.5 GB of packages and models, once, less on an 8 GB Mac)"
  fi
  mkdir -p "$ROOT"; chmod 700 "$ROOT"
  TMP="$(mktemp -d "$ROOT/.source-new.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT
  # Quiet (an error is still shown): GitHub's archive doesn't say its size, so a progress bar has nothing to draw.
  curl -fsSL --proto '=https' --tlsv1.2 \
    -o "$TMP/standby.tar.gz" "$REPO_URL/archive/$REF.tar.gz" \
    || die "Couldn't download Standby from GitHub: check the internet connection, then run this again."
  mkdir "$TMP/code"
  tar -xzf "$TMP/standby.tar.gz" -C "$TMP/code" --strip-components 1 && [[ -f "$TMP/code/install.sh" ]] \
    || die "The download wasn't Standby's code: run this again."
  [[ -e "$DEST" ]] && mv "$DEST" "$TMP/old"
  mv "$TMP/code" "$DEST"
  ok "${DEST/#$HOME/~}"
  bash "$DEST/install.sh" </dev/null             # it ends with what to do next
}

main "$@"
