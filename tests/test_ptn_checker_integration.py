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
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")
    output_dir = tmp_path / "output"

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=output_dir,
    )

    assert result.success is True
    assert result.status_code == "SUCCESS"
    assert result.analysis_data == {"status": "ok"}
    assert result.report_path is None


def test_ptn_checker_wrapper_uses_report_path_returned_by_subprocess(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "from pathlib import Path\n"
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    output = Path(output_dir)\n"
        "    output.mkdir(parents=True, exist_ok=True)\n"
        "    report_path = output / 'beam_2.pdf'\n"
        "    report_path.write_text('pdf', encoding='utf-8')\n"
        "    return {'Beam 2': {'layers': []}, '_report_path': report_path}\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "aaa_wrong.pdf").write_text("wrong", encoding="utf-8")

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=output_dir,
    )

    assert result.success is True
    assert result.report_path == output_dir / "beam_2.pdf"
    assert result.analysis_data == {
        "Beam 2": {"layers": []},
        "_report_path": str(output_dir / "beam_2.pdf"),
    }


def test_ptn_checker_wrapper_preserves_all_report_paths_returned_by_subprocess(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "from pathlib import Path\n"
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    output = Path(output_dir)\n"
        "    output.mkdir(parents=True, exist_ok=True)\n"
        "    summary = output / 'beam_2_summary.pdf'\n"
        "    detail = output / 'beam_2_detail.pdf'\n"
        "    summary.write_text('pdf', encoding='utf-8')\n"
        "    detail.write_text('pdf', encoding='utf-8')\n"
        "    return {\n"
        "        'Beam 2': {'layers': []},\n"
        "        '_report_path': summary,\n"
        "        '_report_paths': [summary, detail],\n"
        "    }\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")
    output_dir = tmp_path / "output"

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=output_dir,
    )

    assert result.success is True
    assert result.report_path == output_dir / "beam_2_summary.pdf"
    assert result.report_paths == [
        output_dir / "beam_2_summary.pdf",
        output_dir / "beam_2_detail.pdf",
    ]


def test_ptn_checker_wrapper_reports_missing_dicom(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    integration = PtnCheckerIntegration(ptn_checker_path=tmp_path / "ptn_checker")

    result = integration.run_analysis(
        log_dir=tmp_path / "logs",
        dcm_file=None,
        output_dir=tmp_path / "output",
    )

    assert result.success is False
    assert result.status_code == "FAILED_NO_DICOM"


def test_ptn_checker_wrapper_reports_missing_ptn_files(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=tmp_path / "output",
    )

    assert result.success is False
    assert result.status_code == "FAILED_NO_PTN"


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

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    import src

    assert "src" in sys.modules

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=tmp_path / "output",
    )

    assert result.success is True


def test_ptn_checker_wrapper_handles_numpy_like_results_and_noisy_stdout(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "class FakeScalar:\n"
        "    def __init__(self, value):\n"
        "        self._value = value\n"
        "    def item(self):\n"
        "        return self._value\n"
        "\n"
        "class FakeArray:\n"
        "    def __init__(self, values):\n"
        "        self._values = values\n"
        "    def tolist(self):\n"
        "        return self._values\n"
        "\n"
        "def run_analysis(log_dir, dcm_file, output_dir):\n"
        "    print('PTN file count (15) does not match expected layer count (16)')\n"
        "    print('No more PTN files to process for layer 30 of beam 5G000:TX.')\n"
        "    return {\n"
        "        'status': 'ok',\n"
        "        'layers': FakeArray([{'gamma_mean': FakeScalar(0.8)}]),\n"
        "    }\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=tmp_path / "output",
    )

    assert result.success is True
    assert result.analysis_data == {"status": "ok", "layers": [{"gamma_mean": 0.8}]}


def test_ptn_checker_wrapper_passes_derived_report_name_to_subprocess(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "def derive_report_name(log_dir):\n"
        "    return f'derived::{log_dir}'\n"
        "\n"
        "def run_analysis(log_dir, dcm_file, output_dir, report_name=None):\n"
        "    return {'report_name': report_name}\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=tmp_path / "output",
    )

    assert result.success is True
    assert result.analysis_data == {"report_name": f"derived::{log_dir}"}


def test_ptn_checker_wrapper_strips_large_raw_arrays_but_keeps_gamma_metrics(tmp_path: Path) -> None:
    from src.integrations.ptn_checker import PtnCheckerIntegration

    ptn_checker_path = tmp_path / "ptn_checker"
    ptn_checker_path.mkdir()
    (ptn_checker_path / "main.py").write_text(
        "class FakeArray:\n"
        "    def __init__(self, values):\n"
        "        self._values = values\n"
        "        self.shape = (len(values),)\n"
        "    def tolist(self):\n"
        "        return self._values\n"
        "\n"
        "def derive_report_name(log_dir):\n"
        "    return 'PTN_report_case_2026-04-22'\n"
        "\n"
        "def run_analysis(log_dir, dcm_file, output_dir, report_name=None):\n"
        "    return {\n"
        "        'Beam 1': {\n"
        "            'layers': [\n"
        "                {\n"
        "                    'results': {\n"
        "                        'pass_rate': 0.97,\n"
        "                        'gamma_mean': 0.42,\n"
        "                        'gamma_max': 1.12,\n"
        "                        'evaluated_point_count': 120,\n"
        "                        'gamma_values': FakeArray(list(range(200))),\n"
        "                        'diff_x': FakeArray(list(range(200))),\n"
        "                    }\n"
        "                }\n"
        "            ]\n"
        "        },\n"
        "        '_report_path': 'summary.pdf',\n"
        "    }\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "delivered.ptn").write_text("stable", encoding="utf-8")
    dcm_file = tmp_path / "RP.test.dcm"
    dcm_file.write_text("dicom", encoding="utf-8")

    integration = PtnCheckerIntegration(ptn_checker_path=ptn_checker_path)

    result = integration.run_analysis(
        log_dir=log_dir,
        dcm_file=dcm_file,
        output_dir=tmp_path / "output",
    )

    assert result.success is True
    assert result.analysis_data == {
        "Beam 1": {
            "layers": [
                {
                    "results": {
                        "pass_rate": 0.97,
                        "gamma_mean": 0.42,
                        "gamma_max": 1.12,
                        "evaluated_point_count": 120,
                    }
                }
            ]
        },
        "_report_path": "summary.pdf",
    }
