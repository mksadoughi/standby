"""Only mac/native/macos/ imports the Mac's own frameworks (AppKit, ScreenCaptureKit, Core
Audio, MLX, ...): everything else asks mac/native/<area>.py, so another OS replaces just
those. The modules not moved yet are listed below with what they still import; the list
only shrinks. A new import of one, outside native/macos/, fails here, and so does a listed
one that is gone (take it off the list)."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import ast
import os
import sys

MAC_ONLY = {"AppKit", "Foundation", "Quartz", "ScreenCaptureKit", "AVFoundation", "CoreAudio", "CoreML", "objc",
            "WebKit", "PyObjCTools", "CoreFoundation", "ApplicationServices", "CoreMedia", "Security",
            "UniformTypeIdentifiers", "mlx", "mlx_audio", "mlx_whisper", "parakeet_mlx", "mlx_metal"}
# Not moved yet: module -> the Mac frameworks it still imports itself.
NOT_YET = {}               # every module moved: a new one would be listed here, and the list only shrinks
fails = []


def check(label, ok, detail=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def imports(path):
    """The Mac-only frameworks a file imports, anywhere in it (inside functions too)."""
    got = set()
    for n in ast.walk(ast.parse(open(path).read())):
        if isinstance(n, ast.Import):
            got.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            got.add(n.module.split(".")[0])
    return got & MAC_ONLY


here = {}
for root, dirs, files in os.walk("mac"):
    dirs[:] = [d for d in dirs if d not in ("vendor", "__pycache__")]
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            rel = os.path.relpath(path, "mac")
            if not rel.startswith(os.path.join("native", "macos") + os.sep):
                got = imports(path)
                if got:
                    here[rel] = got

new = {f: sorted(got - NOT_YET.get(f, set())) for f, got in here.items() if got - NOT_YET.get(f, set())}
check("outside mac/native/macos/, no new import of a Mac framework", not new, new)
gone = {f: sorted(want - here.get(f, set())) for f, want in NOT_YET.items() if want - here.get(f, set())}
check("  and none listed as not moved yet that is gone already (take it off NOT_YET)", not gone, gone)
check("mac/native/<area>.py, the interfaces, import none themselves",
      not [f for f in here if f.startswith("native" + os.sep)], [f for f in here if f.startswith("native" + os.sep)])
sys.path.insert(0, "mac")
import native  # noqa: E402
import native.permissions  # noqa: E402,F401  (it loads on this Mac)
check("an area's implementation is this OS's", native.impl("permissions").__name__ == "native.macos.permissions")
real = sys.platform
sys.platform = "linux"
try:
    native.impl("permissions")
    said = None
except ImportError as exc:
    said = str(exc)
finally:
    sys.platform = real
check("  on an OS without one yet: said plainly, not half-done", said and "no implementation for linux yet" in said, said)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
