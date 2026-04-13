from pathlib import Path
import sys


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


def test_ptn_checker_wrapper_runs_in_isolated_subprocess_when_src_package_already_loaded(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "src").mkdir()
    (ptn_checker_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (ptn_checker_path / "src" / "analysis_context.py").write_text(
        "def load_plan_and_machine_config(*_args, **_kwargs):\n"
        "    return {}\n"
        "def parse_ptn_with_optional_mu_correction(*_args, **_kwargs):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    for module_name in (
        "calculator",
        "point_gamma_workflow",
        "report_generator",
        "report_csv_exporter",
        "config_loader",
        "planrange_parser",
    ):
        (ptn_checker_path / "src" / f"{module_name}.py").write_text(
            "def calculate_differences_for_layer(*_args, **_kwargs):\n"
            "    return {}\n"
            "def calculate_point_gamma_for_layer(*_args, **_kwargs):\n"
            "    return {}\n"
            "def generate_report(*_args, **_kwargs):\n"
            "    return None\n"
            "def export_report_csv(*_args, **_kwargs):\n"
            "    return None\n"
            "def parse_yaml_config(*_args, **_kwargs):\n"
            "    return {}\n"
            "def parse_planrange_for_directory(*_args, **_kwargs):\n"
            "    return {}\n",
            encoding="utf-8",
        )
    (ptn_checker_path / "main.py").write_text(
        "from src.analysis_context import load_plan_and_machine_config\n"
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    load_plan_and_machine_config()\n"
        "    return {'status': 'ok', 'output_dir': output_dir}\n",
        encoding="utf-8",
    )

    case_path = tmp_path / "case"
    case_path.mkdir()
    log_dir = case_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = case_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    import src  # Ensure communicator's src package is already present in-process.
    assert "src" in sys.modules

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
        raise AssertionError(f"Expected subprocess-isolated PTN analysis success, got {result!r}")
