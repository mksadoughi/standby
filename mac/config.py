"""Where Standby keeps its files, and config.json, which the app, the recorder and
dictation all read and write.

Standard library only, and nothing else of Standby's: dictation imports this at
launch, so it must never depend on the code that reaches Claude Code.
"""

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
CONFIG = ROOT / "config.json"


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG)
