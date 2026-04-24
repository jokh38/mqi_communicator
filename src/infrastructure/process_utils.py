"""Shared process tree inspection and termination helpers."""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time


def terminate_process_tree(pid: int, timeout: float = 5.0, root_first: bool = False) -> bool:
    """Terminate a process and its descendants."""
    if platform.system() == "Windows":
        return terminate_process_tree_windows(pid)
    return terminate_process_tree_unix(pid, timeout=timeout, root_first=root_first)


def terminate_process_tree_windows(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def terminate_process_tree_unix(pid: int, timeout: float = 5.0, root_first: bool = False) -> bool:
    descendants = collect_descendant_pids(pid)
    ordered_pids = descendants + [pid]
    signal_order = list(reversed(ordered_pids)) if root_first else ordered_pids

    for target_pid in signal_order:
        try:
            os.kill(target_pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except Exception:
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(not pid_exists(target_pid) for target_pid in ordered_pids):
            return True
        time.sleep(0.1)

    for target_pid in signal_order:
        try:
            os.kill(target_pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except Exception:
            return False

    time.sleep(0.1)
    return all(not pid_exists(target_pid) for target_pid in ordered_pids)


def collect_descendant_pids(pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []

    children_by_parent: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        child = int(parts[0])
        parent = int(parts[1])
        children_by_parent.setdefault(parent, []).append(child)

    descendants: list[int] = []
    stack = list(children_by_parent.get(pid, []))
    while stack:
        child_pid = stack.pop()
        descendants.append(child_pid)
        stack.extend(children_by_parent.get(child_pid, []))
    return descendants


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def get_process_command(pid: int) -> str:
    if platform.system() == "Windows":
        return get_process_command_windows(pid)

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            check=False,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    return result.stdout.strip()


def get_process_command_windows(pid: int) -> str:
    try:
        result = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/value",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""

    for line in result.stdout.splitlines():
        if line.startswith("CommandLine="):
            return line.split("=", 1)[1].strip()
    return ""
