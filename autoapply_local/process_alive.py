"""Platform-safe process liveness check that never signals the process.

On Windows os.kill(pid, 0) calls TerminateProcess, so it would kill the very
process it means to probe. Windows asks the kernel for the exit code instead;
only POSIX uses signal 0.
"""

import os
import sys

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_MAX_PID = 0xFFFFFFFF


def _as_pid(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        pid = value
    elif isinstance(value, str) and value.strip().isdigit():
        pid = int(value.strip())
    else:
        return None
    return pid if 0 < pid <= _MAX_PID else None


def _windows_alive(pid):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(value):
    """Whether the process with this id is running; unknown or dead is False."""
    pid = _as_pid(value)
    if pid is None:
        return False
    if sys.platform == "win32":
        try:
            return bool(_windows_alive(pid))
        except (OSError, ValueError, TypeError, AttributeError):
            return False
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError, SystemError):
        return False
    return True
