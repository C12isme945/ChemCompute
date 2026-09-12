"""One background instance per runtime without reserving random TCP ports."""
import hashlib
import os
from pathlib import Path


def acquire_runtime_lock(home=None):
    name = hashlib.sha256(str(Path(home or Path.cwd()).resolve()).encode()).hexdigest()
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, 'Local\\ChemCompute-' + name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(handle)
            return None
        return lambda: kernel.CloseHandle(handle)
    import fcntl
    stream = (Path(home or Path.cwd()) / '.chemcompute.lock').open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    return stream.close
