"""Wrapper around the in-repo PTN checker project."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
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
            run_analysis = self._load_run_analysis()
            analysis_data = run_analysis(str(log_dir), str(Path(dcm_file)), str(output_dir))
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
