from pathlib import Path


def test_ptn_checker_wrapper_returns_success_result(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "from pathlib import Path\n"
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    Path(output_dir).mkdir(parents=True, exist_ok=True)\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    case_path = tmp_path / "case"
    case_path.mkdir()
    log_dir = case_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = case_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(
        ptn_checker_path=ptn_checker_path,
        output_subdir="ptn_output",
    )

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        case_path=case_path,
    )

    if result.success is not True:
        raise AssertionError(f"Expected success result, got {result!r}")
    if result.status_code != "SUCCESS":
        raise AssertionError(f"Expected SUCCESS status code, got {result.status_code!r}")


def test_ptn_checker_wrapper_reports_missing_dicom() -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    integration = PtnCheckerIntegration(
        ptn_checker_path=Path("/tmp/ptn_checker"),
        output_subdir="ptn_output",
    )

    result = integration.run_analysis(
        log_dir=Path("/tmp/logs"),
        dcm_file=None,
        case_path=Path("/tmp/case"),
    )

    if result.success is not False:
        raise AssertionError(f"Expected missing DICOM to fail, got {result!r}")
    if result.status_code != "FAILED_NO_DICOM":
        raise AssertionError(f"Expected FAILED_NO_DICOM status code, got {result.status_code!r}")


def test_ptn_checker_wrapper_reports_missing_ptn_files(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    case_path = tmp_path / "case"
    case_path.mkdir()
    log_dir = case_path / "logs"
    log_dir.mkdir()
    dcm_file = case_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(
        ptn_checker_path=ptn_checker_path,
        output_subdir="ptn_output",
    )

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        case_path=case_path,
    )

    if result.success is not False:
        raise AssertionError(f"Expected missing PTN files to fail, got {result!r}")
    if result.status_code != "FAILED_NO_PTN":
        raise AssertionError(f"Expected FAILED_NO_PTN status code, got {result.status_code!r}")
