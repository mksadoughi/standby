"""native/mics.py on a Mac: Core Audio's devices, read and listened to through pyobjc.

PortAudio (sounddevice) learns the device list once, when it starts: the system
default and any device that connects later are invisible to it until it starts
again. So what is connected, which mic is the system's default, and whether another
app has a mic open come from Core Audio here, with a word when any of it changes.
Nothing here opens audio: these are property reads and listeners.
"""

import atexit
import struct
import time

from devices import BLUETOOTH, BUILT_IN, USB, VIRTUAL, Device, headset_mic  # noqa: F401  (the portable kinds)

TRANSPORTS = {b"bltn": BUILT_IN, b"blue": BLUETOOTH, b"blea": BLUETOOTH, b"usb ": USB,
              b"virt": VIRTUAL, b"grup": VIRTUAL, b"airp": "airplay", b"ccap": "continuity",
              b"thun": "thunderbolt", b"hdmi": "hdmi", b"dprt": "displayport", b"pci ": "pci"}


class CoreAudio(object):
    """The real devices. on_change, from watch(): called on Core Audio's own
    thread when the default input or the device list changes; it should only
    set a flag."""

    def __init__(self):
        import CoreAudio as CA
        import objc
        self.CA, self.objc = CA, objc
        self._listeners = []

    def _addr(self, sel, scope=None):
        CA = self.CA
        return CA.AudioObjectPropertyAddress(sel, CA.kAudioObjectPropertyScopeGlobal if scope is None else scope,
                                             CA.kAudioObjectPropertyElementMain)

    def _get(self, obj, sel, size, scope=None):
        st, n, data = self.CA.AudioObjectGetPropertyData(obj, self._addr(sel, scope), 0, b"", size, None)
        return bytes(data[:n]) if st == 0 and data is not None else b""

    def _size(self, obj, sel, scope=None):
        st, n = self.CA.AudioObjectGetPropertyDataSize(obj, self._addr(sel, scope), 0, b"", None)
        return n if st == 0 else 0

    def _u32(self, obj, sel, scope=None):
        raw = self._get(obj, sel, 4, scope)
        return struct.unpack("<I", raw)[0] if len(raw) == 4 else None

    def _text(self, obj, sel):
        raw = self._get(obj, sel, 8)
        if len(raw) != 8:
            return None
        ptr = struct.unpack("<Q", raw)[0]
        return str(self.objc.objc_object(c_void_p=ptr)) if ptr else None

    def _device(self, ident):
        tt = self._get(ident, self.CA.kAudioDevicePropertyTransportType, 4)
        return Device(self._text(ident, self.CA.kAudioDevicePropertyDeviceUID) or str(ident),
                      self._text(ident, self.CA.kAudioObjectPropertyName) or "?",
                      TRANSPORTS.get(tt[::-1], "other"), ident)

    def inputs(self):
        CA, out = self.CA, []
        system = CA.kAudioObjectSystemObject
        raw = self._get(system, CA.kAudioHardwarePropertyDevices, self._size(system, CA.kAudioHardwarePropertyDevices))
        for ident in struct.unpack("<%dI" % (len(raw) // 4), raw):
            if self._size(ident, CA.kAudioDevicePropertyStreams, CA.kAudioObjectPropertyScopeInput) > 0:
                out.append(self._device(ident))
        return out

    def default_input(self):
        ident = self._u32(self.CA.kAudioObjectSystemObject, self.CA.kAudioHardwarePropertyDefaultInputDevice)
        return self._device(ident) if ident else None

    def default_output(self):
        """Where the Mac plays now (what it learnt of the room is kept per output)."""
        ident = self._u32(self.CA.kAudioObjectSystemObject, self.CA.kAudioHardwarePropertyDefaultOutputDevice)
        return self._device(ident) if ident else None

    def output_leaks(self):
        """Could the Mac's sound reach its mic? Not on headphones: wired ones
        (the output's data source is 'hdpn'; their transport says built-in, like the
        speakers, so the transport cannot tell), or a Bluetooth headset: one with a mic,
        on the output device itself or, as AirPods are, a Bluetooth input of its own with
        the same address ("<address>:output" and "<address>:input"). Anything else may:
        the built-in speakers, a monitor's, a Bluetooth speaker. Unsure: it may (what that
        costs is a check that drops nothing it is not sure of)."""
        CA = self.CA
        ident = self._u32(CA.kAudioObjectSystemObject, CA.kAudioHardwarePropertyDefaultOutputDevice)
        if not ident:
            return True
        source = self._get(ident, CA.kAudioDevicePropertyDataSource, 4, CA.kAudioObjectPropertyScopeOutput)
        if source[::-1] == b"hdpn":
            return False
        if TRANSPORTS.get(self._get(ident, CA.kAudioDevicePropertyTransportType, 4)[::-1]) != BLUETOOTH:
            return True
        if self._size(ident, CA.kAudioDevicePropertyStreams, CA.kAudioObjectPropertyScopeInput) > 0:
            return False
        return not headset_mic(self._device(ident), self.inputs())

    def in_use(self, device):
        """Another process has its audio running (a call on that mic). Only for
        a mic not recorded here: one recorded here is running because of us."""
        return bool(device.id and self._u32(device.id, self.CA.kAudioDevicePropertyDeviceIsRunningSomewhere))

    def watch(self, on_change):
        CA = self.CA

        def heard(_n, _addresses):
            on_change()
        if not self._listeners:
            # Gone before Python is: a listener left in place is called on Core Audio's
            # thread into an interpreter shutting down, and the process dies in libdispatch
            # ("Python quit unexpectedly" as a call's recorder exited, 2026-10-26).
            atexit.register(self.unwatch)
        for sel in (CA.kAudioHardwarePropertyDefaultInputDevice, CA.kAudioHardwarePropertyDevices):
            st = CA.AudioObjectAddPropertyListenerBlock(CA.kAudioObjectSystemObject, self._addr(sel), None, heard)
            if st != 0:
                return False
            self._listeners.append((sel, heard))
        return True

    def unwatch(self):
        """Every listener watch() added, removed; then a moment for one Core Audio had
        already started, while Python can still take it (this runs at exit, before the
        interpreter goes)."""
        CA, listeners, self._listeners = self.CA, self._listeners, []
        for sel, block in listeners:
            try:
                CA.AudioObjectRemovePropertyListenerBlock(CA.kAudioObjectSystemObject, self._addr(sel), None, block)
            except Exception:
                pass
        if listeners:
            time.sleep(0.1)
