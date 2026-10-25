#!/usr/bin/env bash
# The website (docs/): its script links to the right repository, and its stylesheet parses (needs node).
set -euo pipefail
cd "$(dirname "$0")/.."
NODE="${NODE:-$(command -v node || true)}"
[[ -n "$NODE" ]] || { echo "SKIP (needs node)"; exit 0; }
[[ -f docs/.nojekyll ]] || { echo "docs/.nojekyll missing: GitHub Pages would run Jekyll"; exit 1; }
"$NODE" tests/site_smoke.js
"$NODE" tests/site_css.js
