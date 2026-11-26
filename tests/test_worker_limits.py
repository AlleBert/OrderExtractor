import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from order_extractor.worker_limits import MEMORY, WorkerProcess


def linux_probe(directory, external_file):
    blocked = {}
    try:
        with socket.socket():
            blocked["network"] = False
    except PermissionError:
        blocked["network"] = True
    try:
        external_file.read_bytes()
        blocked["filesystem"] = False
    except PermissionError:
        blocked["filesystem"] = True
    try:
        bytearray(MEMORY + 1)
        blocked["memory"] = False
    except MemoryError:
        blocked["memory"] = True
    child = subprocess.run(
        [sys.executable, "-c",
         "import socket\ntry:\n socket.socket()\nexcept PermissionError:\n raise SystemExit(42)"],
        timeout=10, check=False,
    )
    blocked["descendant_network"] = child.returncode == 42
    blocked["environment"] = "OE_SYNTHETIC_SECRET" not in os.environ
    (directory / "report.json").write_text(json.dumps(blocked), encoding="utf-8")


def sleeping_child(directory):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
    temporary = directory / "pid.new"
    temporary.write_text(str(process.pid), encoding="ascii")
    temporary.replace(directory / "pid")
    process.wait(timeout=90)


def active_process(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as wt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        kernel.WaitForSingleObject.restype = wt.DWORD
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Controlli seccomp e Landlock specifici di Linux")
def test_linux_worker_drops_environment_and_network_and_limits_memory(tmp_path, monkeypatch):
    external = tmp_path / "isolated.txt"
    external.write_text("dato sintetico", encoding="utf-8")
    directory = tmp_path / "work"
    directory.mkdir()
    monkeypatch.setenv("OE_SYNTHETIC_SECRET", "synthetic")
    process = WorkerProcess(linux_probe, (directory, external), directory)
    try:
        deadline = time.monotonic() + 10
        while process.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process.is_alive()
        process.finish()
        report = json.loads((directory / "report.json").read_bytes())
        assert report == {"network": True, "filesystem": process.isolation == "linux-files-network",
                          "memory": True, "descendant_network": True, "environment": True}
    finally:
        process.stop()


@pytest.mark.skipif(not (sys.platform.startswith("linux") or os.name == "nt"), reason="Piattaforma desktop non prevista")
def test_cancel_terminates_worker_and_subprocess(tmp_path):
    process = WorkerProcess(sleeping_child, (tmp_path,), tmp_path)
    try:
        path = tmp_path / "pid"
        deadline = time.monotonic() + 8
        while not path.is_file() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        pid = int(path.read_text())
        assert active_process(pid)
        process.stop()
        deadline = time.monotonic() + 3
        while active_process(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not active_process(pid)
        assert not process.is_alive()
    finally:
        process.stop()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="OCR contenuto verificato su Linux")
def test_scanned_pdf_through_contained_worker(tmp_path):
    from order_extractor.local import worker
    from order_extractor.manual import PageMapping, read_mapping
    from order_extractor.worker_protocol import (
        MAX_RESULT,
        Result,
        read_artifact,
        result_protocol,
    )
    from scripts.fixtures import build

    source = tmp_path / "source.pdf"
    build(source, scan=True)
    process = WorkerProcess(worker, (source, tmp_path), tmp_path)
    try:
        deadline = time.monotonic() + 25
        while process.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process.is_alive()
        process.finish()
        result = result_protocol.validate_json(read_artifact(tmp_path / "result.json", MAX_RESULT))
        assert isinstance(result, Result)
        assert result.engines == ["OCR Tesseract"]
        assert [row.article for row in result.rows] == ["00042", "ART003", "A-0009"]
        assert result.mappable_pages == [0]
        mapped = read_mapping(tmp_path, 0, PageMapping(revision=0, top=.16, bottom=.25,
            columns={"article": .14, "lot": .31, "description": .42, "quantity": .70, "unit_price": .79}))
        assert [row.article for row in mapped] == ["00042", "ART003", "A-0009"]
        assert mapped[0].source_spans[0].engine == "OCR Tesseract"
        assert (tmp_path / "0.png").is_file()
        assert not source.exists()
    finally:
        process.stop()


def test_worker_protocol_bounds_and_metadata(tmp_path):
    from order_extractor.worker_protocol import Progress, read_artifact, result_protocol

    path = tmp_path / "work.json"
    path.write_bytes(b"123456")
    with pytest.raises(ValueError, match="dimensione"):
        read_artifact(path, 5)
    with pytest.raises(ValueError):
        Progress.model_validate({"checked": 20, "total": 19})
    with pytest.raises(ValueError):
        result_protocol.validate_python({"page_count": 3, "rows": [], "notices": [], "engines": ["testo PDF"]})
    if os.name == "posix":
        external = tmp_path / "external.txt"
        external.write_text("dati sintetici", encoding="utf-8")
        link = tmp_path / "output.png"
        link.symlink_to(external)
        with pytest.raises(ValueError, match="Collegamento"):
            read_artifact(link, 80)


def test_packaged_worker_entrypoint_rejects_source_invocation(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic")
    attempt = subprocess.run([sys.executable, "-m", "order_extractor", "--pdf-worker", str(tmp_path)],
                             check=False, timeout=10, capture_output=True)
    assert attempt.returncode == 2
    assert source.is_file()
    assert not (tmp_path / "result.json").exists()
