"""Device resolution must survive macOS renumbering. Replays two real failures:
index 0 that became an OUTPUT device, and index 2 that became ZoomAudioDevice
(silent outside a Zoom call), which made every UI session record silence."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import sys, types
from importlib.machinery import SourceFileLoader

def fake_sd(devices, default_in):
    return types.SimpleNamespace(
        query_devices=lambda i=None: devices if i is None else devices[i],
        default=types.SimpleNamespace(device=[default_in, 0]))

TODAY = [   # the device list from the failing UI session
    {"name": "MacBook Pro Speakers", "max_input_channels": 0},     # 0 output
    {"name": "External Headphones", "max_input_channels": 0},      # 1 output
    {"name": "ZoomAudioDevice", "max_input_channels": 2},          # 2 virtual
    {"name": "BlackHole 2ch", "max_input_channels": 2},            # 3 virtual
    {"name": "MacBook Pro Microphone", "max_input_channels": 1},   # 4 real
]
fails = []
def check(label, got, want):
    print("  %-62s %s" % (label, "OK" if got == want else "FAIL got=%r" % (got,)))
    got == want or fails.append(label)

sys.modules["sounddevice"] = fake_sd(TODAY, 4)
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sd = sys.modules["sounddevice"]

check("saved index 2 (now ZoomAudioDevice) -> real mic, not Zoom",
      sb.resolve_device(2, "Room"), 4)
check("saved index 0 (now an output) -> real mic", sb.resolve_device(0, "Room"), 4)
check("mic pinned by exact name wins", sb.resolve_device("MacBook Pro Microphone", "Room"), 4)
check("virtual device still usable when NAMED explicitly",
      sb.resolve_device("ZoomAudioDevice", "Room"), 2)
check("loopback role keeps honouring its index (BlackHole)", sb.resolve_device(3, "Others"), 3)
check("unknown name falls back to the real mic", sb.resolve_device("AirPods", "Room"), 4)

sd.default = types.SimpleNamespace(device=[2, 0])       # default input = Zoom
check("virtual system default is skipped for the mic", sb.resolve_device(None, "Room"), 4)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
