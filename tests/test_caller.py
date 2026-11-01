"""The AI caller (tests/caller/caller.py): every scenario, a real call with Claude Code and Standby's own voice,
the caller's voice fed in as the mic, scored. On demand only (its own login; a few minutes a scenario)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "caller"))
import caller  # noqa: E402

sys.exit(caller.main(["run"] + sys.argv[1:]))
