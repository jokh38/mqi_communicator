"""Registry and cleanup helpers for MQI main-process lifecycle."""

import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import time
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Optional


class ProcessRegistry:
    """Tracks the active MQI main process and reclaims stale prior instances."""

    def __init__(
        self,
        repo_root: Path,
        config_path: Optional[Path],
        logger: Any,
        runtime_file: Optional[Path] = None,
    ):
        self.repo_root = repo_root.resolve()
        self.config_path = config_path.resolve() if config_path else None
        self.logger = logger
        self.runtime_file = (
            runtime_file or self.repo_root / ".runtime" / "main_process.json"
        )
        self.main_script = (self.repo_root / "main.py").resolve()

    def reclaim_previous_instance(self, current_pid: Optional[int] = None) -> None:
        """Terminate prior matching MQI instances from the same repo/config."""
        current_pid = current_pid or os.getpid()
        reclaimed_pids = set()

        metadata = self._read_runtime_metadata()
        if metadata:
            metadata_pid = metadata.get("pid")
            if (
                isinstance(metadata_pid, int)
                and metadata_pid != current_pid
                and self._metadata_matches_instance(metadata)
            ):
                if self._pid_matches_instance(metadata_pid):
                    self._reclaim_pid(metadata_pid, "runtime metadata")
                    reclaimed_pids.add(metadata_pid)

        for candidate in self._find_matching_process_candidates(
            current_pid=current_pid
        ):
            pid = candidate.get("pid")
            if not isinstance(pid, int) or pid == current_pid or pid in reclaimed_pids:
                continue
            if self._candidate_matches_instance(candidate):
                self._reclaim_pid(pid, "process scan")

    def register_current_process(self, current_pid: Optional[int] = None) -> None:
        """Persist metadata for the active MQI main process."""
        current_pid = current_pid or os.getpid()
        metadata = {
            "pid": current_pid,
            "repo_root": str(self.repo_root),
            "config_path": str(self.config_path) if self.config_path else None,
            "command": self._get_process_command(current_pid) or " ".join(sys.argv),
            "recorded_at": time.time(),
        }
        self.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_file.write_text(json.dumps(metadata, indent=2))

    def unregister_current_process(self, current_pid: Optional[int] = None) -> None:
        """Remove runtime metadata when it still belongs to the current process."""
        current_pid = current_pid or os.getpid()
        metadata = self._read_runtime_metadata()
        if (
            metadata
            and metadata.get("pid") == current_pid
            and self.runtime_file.exists()
        ):
            self.runtime_file.unlink()

    def _reclaim_pid(self, pid: int, source: str) -> None:
        if self.logger:
            self.logger.warning(
                "Reclaiming stale MQI process before startup",
                {
                    "pid": pid,
                    "source": source,
                },
            )

        if not self._terminate_process_tree(pid):
            if self.logger:
                self.logger.error(
                    "Failed to terminate stale MQI process",
                    {"pid": pid, "source": source},
                )
            return

        if not self._wait_for_exit(pid):
            if self.logger:
                self.logger.error(
                    "Timed out waiting for stale MQI process to exit",
                    {"pid": pid, "source": source},
                )

    def _metadata_matches_instance(self, metadata: dict[str, Any]) -> bool:
        metadata_repo = metadata.get("repo_root")
        if not metadata_repo:
            return False

        try:
            resolved_repo = Path(metadata_repo).resolve()
        except Exception:
            return False

        if resolved_repo != self.repo_root:
            return False

        return (
            self._normalize_config_path(metadata.get("config_path")) == self.config_path
        )

    def _pid_matches_instance(self, pid: int) -> bool:
        candidate = {
            "pid": pid,
            "command": self._get_process_command(pid),
            "cwd": self._get_process_cwd(pid),
        }
        return self._candidate_matches_instance(candidate)

    def _candidate_matches_instance(self, candidate: dict[str, Any]) -> bool:
        command = candidate.get("command") or ""
        if not command or "main.py" not in command:
            return False

        # Check if the process is actually a Python process, not a shell wrapper
        tokens = command.split()
        if not tokens:
            return False
        first_token = Path(tokens[0]).name
        if (
            not first_token.startswith("python")
            and not first_token.endswith("python3")
            and not first_token.endswith("python")
        ):
            return False

        cwd = self._normalize_path(candidate.get("cwd"))
        script_path = self._extract_main_script_path(command, cwd)

        repo_matches = cwd == self.repo_root or script_path == self.main_script
        if not repo_matches:
            return False

        candidate_config = self._extract_config_path(command, cwd)
        return candidate_config == self.config_path

    def _read_runtime_metadata(self) -> Optional[dict[str, Any]]:
        if not self.runtime_file.exists():
            return None

        try:
            return json.loads(self.runtime_file.read_text())
        except JSONDecodeError:
            return None
        except OSError:
            return None

    def _find_matching_process_candidates(
        self, current_pid: Optional[int] = None
    ) -> list[dict[str, Any]]:
        current_pid = current_pid or os.getpid()
        if platform.system() == "Windows":
            return self._find_process_candidates_windows(current_pid)
        return self._find_process_candidates_unix(current_pid)

    def _find_process_candidates_unix(self, current_pid: int) -> list[dict[str, Any]]:
        try:
            result = self._run_command(
                ["ps", "-eo", "pid=,args="],
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return []

        candidates = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=1)
            if not parts or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid == current_pid:
                continue
            command = parts[1] if len(parts) > 1 else ""
            if "main.py" not in command:
                continue
            candidates.append(
                {
                    "pid": pid,
                    "command": command,
                    "cwd": self._get_process_cwd(pid),
                }
            )
        return candidates

    def _find_process_candidates_windows(
        self, current_pid: int
    ) -> list[dict[str, Any]]:
        try:
            result = self._run_command(
                [
                    "wmic",
                    "process",
                    "where",
                    "name='python.exe' or name='pythonw.exe'",
                    "get",
                    "ProcessId,CommandLine",
                    "/format:csv",
                ],
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return []

        candidates = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or "main.py" not in stripped:
                continue
            parts = [part.strip() for part in stripped.split(",") if part.strip()]
            if not parts or not parts[-1].isdigit():
                continue
            pid = int(parts[-1])
            if pid == current_pid:
                continue
            command = ",".join(parts[1:-1]) if len(parts) > 2 else ""
            candidates.append({"pid": pid, "command": command, "cwd": None})
        return candidates

    def _extract_main_script_path(
        self, command: str, cwd: Optional[Path]
    ) -> Optional[Path]:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None

        for token in tokens:
            if token.endswith("main.py"):
                return self._resolve_command_path(token, cwd)
        return None

    def _extract_config_path(self, command: str, cwd: Optional[Path]) -> Optional[Path]:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None

        for idx, token in enumerate(tokens):
            if token == "--config" and idx + 1 < len(tokens):
                return self._resolve_command_path(tokens[idx + 1], cwd)

        for idx, token in enumerate(tokens):
            if token.endswith("main.py") and idx + 1 < len(tokens):
                next_token = tokens[idx + 1]
                if not next_token.startswith("-"):
                    return self._resolve_command_path(next_token, cwd)
        return None

    def _resolve_command_path(self, value: str, cwd: Optional[Path]) -> Optional[Path]:
        path = Path(value)
        if not path.is_absolute():
            if cwd is None:
                return None
            path = cwd / path
        return self._normalize_path(path)

    def _normalize_config_path(self, value: Optional[str]) -> Optional[Path]:
        if value is None:
            return None
        return self._normalize_path(value)

    def _normalize_path(self, value: Any) -> Optional[Path]:
        if value in (None, ""):
            return None
        try:
            return Path(value).resolve()
        except Exception:
            return None

    def _terminate_process_tree(self, pid: int, timeout: float = 5.0) -> bool:
        if platform.system() == "Windows":
            return self._terminate_process_tree_windows(pid)
        return self._terminate_process_tree_unix(pid, timeout=timeout)

    def _terminate_process_tree_windows(self, pid: int) -> bool:
        try:
            self._run_command(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return False
        return True

    def _terminate_process_tree_unix(self, pid: int, timeout: float = 5.0) -> bool:
        descendants = self._collect_descendant_pids(pid)
        ordered_pids = descendants + [pid]

        for target_pid in ordered_pids:
            try:
                os.kill(target_pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except Exception:
                return False

        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(not self._pid_exists(target_pid) for target_pid in ordered_pids):
                return True
            time.sleep(0.1)

        for target_pid in ordered_pids:
            try:
                os.kill(target_pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except Exception:
                return False
        return True

    def _collect_descendant_pids(self, pid: int) -> list[int]:
        try:
            result = self._run_command(
                ["ps", "-eo", "pid=,ppid="],
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

        descendants = []
        stack = list(children_by_parent.get(pid, []))
        while stack:
            child_pid = stack.pop()
            descendants.append(child_pid)
            stack.extend(children_by_parent.get(child_pid, []))
        return descendants

    def _wait_for_exit(self, pid: int, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._pid_exists(pid):
                return True
            time.sleep(0.1)
        return not self._pid_exists(pid)

    def _pid_exists(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except Exception:
            return True
        return True

    def _get_process_command(self, pid: int) -> str:
        if platform.system() == "Windows":
            return self._get_process_command_windows(pid)

        try:
            result = self._run_command(
                ["ps", "-p", str(pid), "-o", "args="],
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return ""
        return result.stdout.strip()

    def _get_process_command_windows(self, pid: int) -> str:
        try:
            result = self._run_command(
                [
                    "wmic",
                    "process",
                    "where",
                    f"ProcessId={pid}",
                    "get",
                    "CommandLine",
                    "/value",
                ],
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return ""

        for line in result.stdout.splitlines():
            if line.startswith("CommandLine="):
                return line.split("=", 1)[1].strip()
        return ""

    def _get_process_cwd(self, pid: int) -> Optional[Path]:
        if platform.system() == "Windows":
            return None

        proc_cwd = Path(os.sep) / "proc" / str(pid) / "cwd"
        try:
            return proc_cwd.resolve()
        except Exception:
            return None

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a process-inspection command with strict failure handling."""
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
