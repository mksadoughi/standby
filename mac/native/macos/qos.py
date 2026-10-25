"""native/qos.py on a Mac: a thread's quality of service (libSystem's pthread QoS calls) and an
activity for the process (Foundation's NSProcessInfo): user-interactive threads run before the
default work of everything else, on the performance cores, and an activity keeps App Nap off."""

QOS_CLASS_USER_INTERACTIVE = 0x21
# NSActivityUserInitiated | NSActivityLatencyCritical (Foundation's NSProcessInfo.h)
_ACTIVITY = 0x00FFFFFF | 0xFF00000000
_LIB = []


def _lib():
    if not _LIB:
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
        lib.pthread_set_qos_class_self_np.restype = ctypes.c_int
        lib.pthread_self.restype = ctypes.c_void_p
        lib.pthread_get_qos_class_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
                                                 ctypes.POINTER(ctypes.c_int)]
        lib.pthread_get_qos_class_np.restype = ctypes.c_int
        _LIB.append(lib)
    return _LIB[0]


def interactive():
    return _lib().pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) == 0


def current():
    import ctypes
    lib = _lib()
    cls, rel = ctypes.c_uint(0), ctypes.c_int(0)
    if lib.pthread_get_qos_class_np(lib.pthread_self(), ctypes.byref(cls), ctypes.byref(rel)) != 0:
        return None
    return cls.value


def hold(reason):
    from Foundation import NSProcessInfo
    return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(_ACTIVITY, reason)


def release(token):
    from Foundation import NSProcessInfo
    NSProcessInfo.processInfo().endActivity_(token)
