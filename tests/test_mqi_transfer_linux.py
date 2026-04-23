import importlib.util
import io
import struct
import zipfile
from pathlib import Path
from unittest.mock import MagicMock


def _load_transfer_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "mqi_transfer" / "Linux" / "mqi_transfer.py"
    )
    spec = importlib.util.spec_from_file_location("mqi_transfer_linux", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _build_archive_bytes() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("1.2.840.10008.1.2.3/RP.test.dcm", "")
        zf.writestr("1.2.840.10008.1.2.3/2026031923483900/PlanInfo.txt", "ok")
    return archive.getvalue()


def test_handle_transfer_stages_temp_files_outside_watched_output_tree(tmp_path):
    transfer = _load_transfer_module()
    archive_bytes = _build_archive_bytes()
    digest = transfer.hashlib.sha256(archive_bytes).digest()
    header = struct.pack(">Q", len(archive_bytes)) + digest

    output_root = tmp_path / "watched"
    output_root.mkdir()
    expected_staging_dir = tmp_path / ".mqi_transfer_tmp"

    conn = MagicMock()
    conn.recv.side_effect = [archive_bytes]
    created_tmp_dirs = []
    created_extract_dirs = []

    original_mkstemp = transfer.tempfile.mkstemp
    original_mkdtemp = transfer.tempfile.mkdtemp

    def capture_mkstemp(*args, **kwargs):
        created_tmp_dirs.append(Path(kwargs["dir"]))
        return original_mkstemp(*args, **kwargs)

    def capture_mkdtemp(*args, **kwargs):
        created_extract_dirs.append(Path(kwargs["dir"]))
        return original_mkdtemp(*args, **kwargs)

    transfer.recv_exact = lambda _conn, _n: header
    transfer.tempfile.mkstemp = capture_mkstemp
    transfer.tempfile.mkdtemp = capture_mkdtemp
    try:
        transfer.handle_transfer(
            conn=conn,
            addr=("127.0.0.1", 5000),
            output_root=str(output_root),
            ip_to_group={"127.0.0.1": "G1"},
            cleanup_temp=True,
            max_archive_bytes=len(archive_bytes) + 1024,
        )
    finally:
        transfer.tempfile.mkstemp = original_mkstemp
        transfer.tempfile.mkdtemp = original_mkdtemp

    assert created_tmp_dirs == [expected_staging_dir]
    assert created_extract_dirs == [expected_staging_dir]
    assert (output_root / "G1" / "1.2.840.10008.1.2.3" / "RP.test.dcm").exists()
    conn.sendall.assert_called_once_with(b"\x01")
