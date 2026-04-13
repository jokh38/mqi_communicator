"""Wrapper around the in-repo PTN checker project."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any, Dict, Optional


@dataclass
class PtnCheckerResult:
    success: bool
    status_code: str
    error_message: Optional[str] = None
    output_dir: Optional[Path] = None
    analysis_data: Optional[Dict[str, Any]] = None
    report_path: Optional[Path] = None


class PtnCheckerIntegration:
    """Load and invoke the external PTN checker without modifying that project."""

    def __init__(self, ptn_checker_path: Path, output_subdir: str) -> None:
        self.ptn_checker_path = Path(ptn_checker_path)
        self.output_subdir = output_subdir

    def run_analysis(
        self,
        log_dir: Path,
        dcm_file: Optional[Path],
        case_path: Path,
    ) -> PtnCheckerResult:
        log_dir = Path(log_dir)
        case_path = Path(case_path)
        output_dir = case_path / self.output_subdir

        if dcm_file is None:
            return PtnCheckerResult(
                success=False,
                status_code="FAILED_NO_DICOM",
                error_message="No RTPLAN DICOM file available for PTN analysis",
                output_dir=output_dir,
            )

        if not any(log_dir.rglob("*.ptn")):
            return PtnCheckerResult(
                success=False,
                status_code="FAILED_NO_PTN",
                error_message=f"No PTN files found under {log_dir}",
                output_dir=output_dir,
            )

        try:
            analysis_data = self._run_analysis_in_subprocess(
                log_dir=log_dir,
                dcm_file=Path(dcm_file),
                output_dir=output_dir,
            )
            report_path = self._find_report_path(output_dir)
            return PtnCheckerResult(
                success=True,
                status_code="SUCCESS",
                output_dir=output_dir,
                analysis_data=analysis_data if isinstance(analysis_data, dict) else None,
                report_path=report_path,
            )
        except FileNotFoundError as exc:
            message = str(exc)
            status_code = "FAILED_NO_PTN" if ".ptn" in message.lower() else "FAILED_EXCEPTION"
            return PtnCheckerResult(
                success=False,
                status_code=status_code,
                error_message=message,
                output_dir=output_dir,
                report_path=self._find_report_path(output_dir),
            )
        except Exception as exc:
            return PtnCheckerResult(
                success=False,
                status_code="FAILED_EXCEPTION",
                error_message=str(exc),
                output_dir=output_dir,
                report_path=self._find_report_path(output_dir),
            )

    def _run_analysis_in_subprocess(
        self,
        log_dir: Path,
        dcm_file: Path,
        output_dir: Path,
    ) -> Optional[Dict[str, Any]]:
        module_path = self.ptn_checker_path / "main.py"
        if not module_path.exists():
            raise FileNotFoundError(f"PTN checker entrypoint not found: {module_path}")

        bootstrap = textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path

            repo_root = Path(sys.argv[1])
            log_dir = sys.argv[2]
            dcm_file = sys.argv[3]
            output_dir = sys.argv[4]

            sys.path.insert(0, str(repo_root))

            from main import run_analysis

            def _json_default(value):
                if hasattr(value, "tolist"):
                    return value.tolist()
                if isinstance(value, Path):
                    return str(value)
                return str(value)

            result = run_analysis(log_dir, dcm_file, output_dir)
            print(json.dumps(result, default=_json_default))
            """
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                bootstrap,
                str(self.ptn_checker_path),
                str(log_dir),
                str(dcm_file),
                str(output_dir),
            ],
            cwd=str(self.ptn_checker_path),
            capture_output=True,
            text=True,
            check=False,
        )

        if completed.returncode != 0:
            error_message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(error_message or "PTN checker subprocess failed")

        payload = completed.stdout.strip()
        if not payload:
            return None

        return json.loads(payload)

    def _load_run_analysis(self):
        module_path = self.ptn_checker_path / "main.py"
        if not module_path.exists():
            raise FileNotFoundError(f"PTN checker entrypoint not found: {module_path}")

        spec = importlib.util.spec_from_file_location("external_ptn_checker_main", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load PTN checker module from {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        run_analysis = getattr(module, "run_analysis", None)
        if run_analysis is None:
            raise AttributeError(f"run_analysis not found in {module_path}")
        return run_analysis

    def _find_report_path(self, output_dir: Path) -> Optional[Path]:
        if not output_dir.exists():
            return None
        pdfs = sorted(output_dir.glob("*.pdf"))
        return pdfs[0] if pdfs else None
