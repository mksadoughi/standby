"""Standby's apps run their own copy of the code, never the checkout.

With the checkout in ~/Documents or ~/Desktop, every rebuild of the apps (an ad-hoc signature is a new app
to macOS) cost them that folder: Standby Dictation was asked for Documents, and Standby could no longer run
its own scripts there, so a call failed ("Operation not permitted"). So install.sh copies mac/ and plugin/
to ~/.standby/code and the apps run from there; everything they start runs from there too.

Only the checkout it was installed from updates the copy (a teammate's worktree or a test never does): the
`standby` command, run from that checkout, brings it up to date first, writing only what changed (by size
and time), so Standby Dictation's CodeWatch restarts into a merge as before.

    python codecopy.py --install DEST     (install.sh: this checkout becomes the source)
"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
CODE = ROOT / "code"
PARTS = ("mac", "plugin", "lite", ".claude-plugin")    # lite/ and .claude-plugin/: Claude Code's plugin, read from here
SKIP = ("__pycache__", ".DS_Store")
SOURCE = ".source"                    # in the copy: the checkout it was installed from
CHECKOUT = Path(__file__).resolve().parent.parent


def source(dest=CODE):
    try:
        return (Path(dest) / SOURCE).read_text().strip() or None
    except OSError:
        return None


def sync(src=None, dest=CODE, install=False):
    """Bring `dest` up to date with PARTS of the checkout `src`: only what changed is written (with its
    source's time, so unchanged files are never read again), what's gone is removed. Unless `install`,
    only from the checkout it was installed from. The paths written or removed, sorted."""
    src, dest = Path(src or CHECKOUT).resolve(), Path(dest)
    if dest.exists() and src == dest.resolve():
        return []
    if not install and source(dest) != str(src):
        return []
    changed = []
    for part in PARTS:
        have = set()
        for d, dirs, files in os.walk(src / part):
            dirs[:] = [x for x in dirs if x not in SKIP]
            for name in files:
                if name in SKIP or name.endswith((".pyc", ".tmp")):
                    continue
                f = Path(d) / name
                rel = f.relative_to(src)
                have.add(rel)
                out = dest / rel
                st = f.stat()
                try:
                    was = out.stat()
                    if (was.st_size, was.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                        continue
                except OSError:
                    pass
                data = f.read_bytes()
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_name(out.name + ".tmp")
                tmp.write_bytes(data)
                os.chmod(tmp, st.st_mode & 0o777)
                os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
                os.replace(tmp, out)
                changed.append(str(rel))
        for d, dirs, files in os.walk(dest / part):
            dirs[:] = [x for x in dirs if x not in SKIP]
            for name in files:
                rel = (Path(d) / name).relative_to(dest)
                if rel not in have and not name.endswith(".pyc"):
                    os.remove(Path(d) / name)
                    changed.append(str(rel))
    if install:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / SOURCE).write_text(str(src) + "\n")
    return sorted(changed)


def main(argv):
    install = "--install" in argv
    rest = [a for a in argv if a != "--install"]
    dest = Path(rest[0]) if rest else CODE
    changed = sync(dest=dest, install=install)
    if install or changed:
        print("%d file(s) of Standby's code updated in %s" % (len(changed), dest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
