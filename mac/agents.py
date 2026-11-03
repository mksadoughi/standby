"""What's left of Standby's messages to agents: their files, tidied away.

Standby once sent a call's or a meeting's summary into the agent's own Claude Code
conversation, with files beside it. It sends nothing into a conversation now; the files
from then, in ~/.standby/messages, go once they are older than FILES_KEEP.
"""
import shutil
import threading
import time
from pathlib import Path

PRUNE_EVERY = 86400.0       # s: the files from before are looked at once a day
FILES_KEEP = 90 * 86400     # s: a message's files were kept this long on the Mac


class Agents(object):
    def __init__(self, ui, m, clock=time.time):
        self.ui, self.m, self.clock = ui, m, clock

    def install(self, prune=True):
        if prune:
            threading.Thread(target=self._prune_forever, name="agents-prune", daemon=True).start()

    def _prune_forever(self):
        while True:
            try:
                self.prune()
            except Exception:
                pass
            time.sleep(PRUNE_EVERY)

    def messages_dir(self):
        return Path(self.m.root) / "messages"

    def prune(self, now=None):
        """Files sent with a message, from when Standby had messages: gone once older than FILES_KEEP."""
        now = now or self.clock()
        for folder in self.messages_dir().glob("*") if self.messages_dir().is_dir() else []:
            try:
                if not folder.is_dir():
                    continue
                if folder.name.startswith("a-"):
                    for f in folder.iterdir():
                        if now - f.stat().st_mtime > FILES_KEEP:
                            f.unlink()
                if now - folder.stat().st_mtime > FILES_KEEP:
                    shutil.rmtree(str(folder))
            except OSError:
                pass


def install(ui, m):
    got = Agents(ui, m)
    got.install()
    return got
