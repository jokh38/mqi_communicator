"""Process lifecycle helpers for child supervision."""

import ctypes
import platform
import signal


def set_parent_death_signal(signum: int = signal.SIGTERM) -> None:
    """Request a Linux parent-death signal for the current process."""
    if platform.system() != "Linux":
        return

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    pr_set_pdeathsig = 1
    result = libc.prctl(pr_set_pdeathsig, signum, 0, 0, 0)
    if result != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "prctl(PR_SET_PDEATHSIG) failed")
