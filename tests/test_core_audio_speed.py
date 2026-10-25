"""How fast reading every process Core Audio knows is (meeting_audio.audio_apps): cheap
enough for the recorder to poll. A measurement of this Mac, so it runs only in the full run
(STANDBY_TESTS=full), alone: beside the other suites a loaded Mac took 70 to 99 ms for
what takes about 9 ms otherwise."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
if sys.platform != "darwin":
    print("SKIP (Core Audio is macOS's)")
    sys.exit(0)
from native.macos import appaudio as ma  # noqa: E402

ma.audio_apps()                          # warm
times = []
for _ in range(3):
    t0 = time.perf_counter()
    ma.audio_apps()
    times.append((time.perf_counter() - t0) * 1000)
ms = min(times)
ok = ms < 60
print("  reading every audio process is cheap enough to poll (%.0f ms, warm, the best of 3)  %s" % (ms, "OK" if ok else "FAIL"))
if not ok:
    print("     %s" % times)
print("\n%s" % ("ALL PASS" if ok else "FAILURES: 1"))
sys.exit(0 if ok else 1)
