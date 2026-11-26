import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="AppContainer richiede Windows")

PROBE = """
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from order_extractor.windows_appcontainer import validate_token

validate_token()
secret, port, output, *mode = sys.argv[1:]
try:
    Path(secret).read_bytes()
except PermissionError:
    files = True
else:
    files = False
try:
    with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
        network = False
except OSError:
    network = True
report = {"files": files, "network": network, "environment": "OE_SYNTHETIC_SECRET" not in os.environ}
if not mode:
    child = str(Path(output).with_name("child.json"))
    subprocess.run([sys.executable, __file__, secret, port, child, "child"], check=True, timeout=10)
    report["child"] = json.loads(Path(child).read_bytes())
Path(output).write_text(json.dumps(report), encoding="utf-8")
"""


def private_runtime(directory):
    directory.mkdir()
    source = Path(sys.base_prefix)
    for name in ("DLLs", "Lib"):
        shutil.copytree(source / name, directory / name, ignore=shutil.ignore_patterns(
            "site-packages", "__pycache__", "test", "tests",
        ))
    for file in source.iterdir():
        if file.is_file() and file.suffix.lower() in {".dll", ".exe"}:
            shutil.copyfile(file, directory / file.name)
    app = directory / "order_extractor"
    app.mkdir()
    package = Path(__file__).resolve().parents[1] / "order_extractor"
    for name in ("__init__.py", "worker_limits.py", "windows_appcontainer.py", "windows_job.py"):
        shutil.copyfile(package / name, app / name)
    (directory / "probe.py").write_text(PROBE, encoding="utf-8")
    return directory / "python.exe"


def test_appcontainer_denies_private_file_and_loopback(tmp_path, monkeypatch):
    from order_extractor.windows_appcontainer import AppContainerProcess, acl

    executable = private_runtime(tmp_path / "runtime")
    work = tmp_path / "work"
    work.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    # Keep the test runner's existing access and remove broad package groups.
    acl(private, "/inheritance:d")
    acl(private, "/remove:g", "*S-1-15-2-1", "*S-1-15-2-2")
    secret = private / "document.txt"
    secret.write_text("marcatore sintetico", encoding="utf-8")
    report = work / "report.json"
    monkeypatch.setenv("OE_SYNTHETIC_SECRET", "synthetic")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            connected, _ = listener.accept()
            connected.close()
        process = AppContainerProcess(executable, [str(executable.parent / "probe.py"),
                                      str(secret), str(port), str(report)], work)
        try:
            deadline = time.monotonic() + 20
            while process.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert not process.is_alive()
            process.finish()
            restricted = {"files": True, "network": True, "environment": True}
            assert json.loads(report.read_bytes()) == {**restricted, "child": restricted}
        finally:
            process.stop()
