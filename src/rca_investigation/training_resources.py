"""Elapsed active training time and OS-reported process peak resident memory."""
import os
from time import monotonic


def peak_resident_bytes():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                       *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                         'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                         'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    import sys
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == 'darwin' else peak * 1024)


class TrainingResources:
    def __init__(self, previous=None):
        self.previous = previous or {'active_seconds': 0., 'process_peak_resident_bytes': 0}
        self.started = monotonic()

    def snapshot(self):
        return {'active_seconds': self.previous['active_seconds'] + monotonic() - self.started,
                'process_peak_resident_bytes': max(self.previous['process_peak_resident_bytes'], peak_resident_bytes()),
                'memory_scope': 'whole_process_peak_including_validation'}
