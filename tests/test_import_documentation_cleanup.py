import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _imports_from_module(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def _local_imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_imports: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    local_imports.append(child)
    return local_imports


def test_phase7_planned_unused_imports_are_removed() -> None:
    dashboard_imports = _imports_from_module(PROJECT_ROOT / "src" / "ui" / "dashboard.py")
    case_aggregator_imports = _imports_from_module(
        PROJECT_ROOT / "src" / "core" / "case_aggregator.py"
    )
    dispatcher_imports = _imports_from_module(PROJECT_ROOT / "src" / "core" / "dispatcher.py")

    dashboard_names = {
        alias.name
        for node in dashboard_imports
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    dashboard_datetime_names = {
        alias.name
        for node in dashboard_imports
        if isinstance(node, ast.ImportFrom) and node.module == "datetime"
        for alias in node.names
    }
    case_aggregator_enum_names = {
        alias.name
        for node in case_aggregator_imports
        if isinstance(node, ast.ImportFrom) and node.module == "src.domain.enums"
        for alias in node.names
    }
    dispatcher_workflow_imports = {
        alias.name
        for node in dispatcher_imports
        if isinstance(node, ast.ImportFrom) and node.module == "src.core.workflow_manager"
        for alias in node.names
    }

    assert "os" not in dashboard_names
    assert "json" not in dashboard_names
    assert "timezone" not in dashboard_datetime_names
    assert "timedelta" not in dashboard_datetime_names
    assert "WorkflowStep" not in case_aggregator_enum_names
    assert "scan_existing_cases" not in dispatcher_workflow_imports
    assert "CaseDetectionHandler" not in dispatcher_workflow_imports


def test_phase7_redundant_local_reimports_are_removed() -> None:
    local_imports_by_file = {
        relative_path: _local_imports(PROJECT_ROOT / relative_path)
        for relative_path in (
            Path("src/handlers/execution_handler.py"),
            Path("src/core/dispatcher.py"),
            Path("src/core/worker.py"),
        )
    }

    blocked_imports = {
        ("re", None),
        ("pathlib", "Path"),
        ("src.core.data_integrity_validator", "DataIntegrityValidator"),
        ("src.utils.db_context", "get_db_session"),
    }

    for local_imports in local_imports_by_file.values():
        found = set()
        for node in local_imports:
            if isinstance(node, ast.Import):
                found.update((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.update((node.module, alias.name) for alias in node.names)
        assert found.isdisjoint(blocked_imports)


def test_phase7_stale_paramiko_documentation_is_removed() -> None:
    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    installation_text = (PROJECT_ROOT / "INSTALLATION.md").read_text(encoding="utf-8")

    assert "paramiko" not in readme_text.lower()
    assert "paramiko" not in installation_text.lower()
    assert "dashboard.auto_start" not in readme_text
    assert "dashboard.auto_start" not in installation_text
