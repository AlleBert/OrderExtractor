import hashlib
import sqlite3
import stat
import zipfile
from contextlib import closing

import pytest

from order_extractor import version_store
from order_extractor.version_store import (
    EXECUTABLE,
    SwitchError,
    VersionStore,
    durable_json,
)


def make_archive(path, *entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(EXECUTABLE, b"synthetic candidate")
        for name, content in entries:
            output.writestr(name, content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def store(tmp_path):
    home = tmp_path / "installed"
    data = tmp_path / "data"
    data.mkdir()
    installation = VersionStore(home, data)
    old = installation.executable("0.1.0")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"previous verified payload")
    durable_json(installation.pointer, {"version": "0.1.0"})
    (data / "license.bin").write_bytes(b"synthetic protected lease")
    with closing(sqlite3.connect(data / "local.sqlite")) as db, db:
        db.execute("CREATE TABLE profiles (name TEXT)")
        db.execute("INSERT INTO profiles VALUES ('profilo di test')")
    return installation


def test_probes_a_snapshot_then_activates(tmp_path, store):
    path, digest = make_archive(tmp_path / "new.zip", ("runtime/core.dll", b"runtime"))

    def probe(executable, snapshot, version):
        assert executable.read_bytes() == b"synthetic candidate"
        assert version == "0.2.0"
        assert store.current() == "0.1.0"
        assert (snapshot / "license.bin").read_bytes() == b"synthetic protected lease"
        with closing(sqlite3.connect(snapshot / "local.sqlite")) as db, db:
            assert db.execute("SELECT name FROM profiles").fetchone() == ("profilo di test",)
            db.execute("DELETE FROM profiles")
        return True

    assert store.apply("0.2.0", path, digest, probe) == store.executable("0.2.0")
    assert store.current() == "0.2.0"
    assert store.executable("0.1.0").is_file()
    with closing(sqlite3.connect(store.data / "local.sqlite")) as db:
        assert db.execute("SELECT name FROM profiles").fetchone() == ("profilo di test",)
    assert not store.recover()
    with pytest.raises(SwitchError, match="più recente"):
        store.apply("0.1.0", path, digest, probe)


def test_probe_failure_restores_previous_release(tmp_path, store):
    path, digest = make_archive(tmp_path / "new.zip")
    with pytest.raises(SwitchError, match="controllo di avvio"):
        store.apply("0.2.0", path, digest, lambda *_: False)
    assert store.current() == "0.1.0"
    assert not store.executable("0.2.0").exists()
    assert not store.journal.exists()


def test_interrupted_switch_is_recovered_on_next_start(tmp_path, store, monkeypatch):
    class Interrupted(BaseException):
        pass

    path, digest = make_archive(tmp_path / "new.zip")

    def interrupt(destination, data):
        durable_json(destination, data)
        if destination == store.pointer and data["version"] == "0.2.0":
            raise Interrupted

    monkeypatch.setattr(version_store, "durable_json", interrupt)
    with pytest.raises(Interrupted):
        store.apply("0.2.0", path, digest, lambda *_: True)
    assert store.current() == "0.2.0"
    assert store.recover()
    assert store.current() == "0.1.0"
    assert not store.recover()
    assert not store.staging.exists()


@pytest.mark.parametrize("name", [
    "../outside.txt", "unsafe\\outside.txt", "C:/outside.txt", "file:stream", "AUX.txt",
    "folder./bad.txt", "orderextractor.EXE",
])
def test_reject_unsafe_archive_paths(tmp_path, store, name):
    path, digest = make_archive(tmp_path / "new.zip", (name, b"inert test data"))
    with pytest.raises(SwitchError, match="(?i)percorso"):
        store.apply("0.2.0", path, digest, lambda *_: pytest.fail("probe called"))
    assert store.current() == "0.1.0"


def test_reject_archive_links_and_modified_package(tmp_path, store):
    link = zipfile.ZipInfo("relative-link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    path, digest = make_archive(tmp_path / "new.zip", (link, b"outside.txt"))
    with pytest.raises(SwitchError, match="non consentito"):
        store.apply("0.2.0", path, digest, lambda *_: pytest.fail("probe called"))
    with pytest.raises(SwitchError, match="cambiato"):
        store.apply("0.2.0", path, "a" * 64, lambda *_: pytest.fail("probe called"))


def test_confirmed_request_is_consumed_and_failure_reported(tmp_path, store):
    import json
    import time

    from order_extractor.supervisor import OUTCOME, REQUEST, apply_pending

    request = store.home / REQUEST
    durable_json(request, {"version": "0.2.0", "requested": time.time()})

    def fetch(state, previous, version, package):
        assert state is store
        assert previous == "0.1.0" and version == "0.2.0"
        return make_archive(package)[1]

    apply_pending(store, fetch=fetch, health=lambda *_: False)
    assert not request.exists()
    assert store.current() == "0.1.0"
    assert not json.loads((store.home / OUTCOME).read_bytes())["success"]
    durable_json(request, {"version": "0.2.0", "requested": time.time()})
    apply_pending(store, fetch=fetch, health=lambda *_: True)
    assert store.current() == "0.2.0"
    assert json.loads((store.home / OUTCOME).read_bytes())["success"]

    request.write_bytes(b"interrupted write")
    apply_pending(store, fetch=fetch, health=lambda *_: pytest.fail("probe called"))
    assert not request.exists()
    assert store.current() == "0.2.0"
    assert not json.loads((store.home / OUTCOME).read_bytes())["success"]


def test_locked_temporary_cache_does_not_undo_working_release(tmp_path, store, monkeypatch):
    path, digest = make_archive(tmp_path / "new.zip")
    delete = version_store.shutil.rmtree

    def keep_cache(directory, *, ignore_errors=False):
        if directory == store.staging:
            assert ignore_errors
            return
        return delete(directory, ignore_errors=ignore_errors)

    monkeypatch.setattr(version_store.shutil, "rmtree", keep_cache)
    assert store.apply("0.2.0", path, digest, lambda *_: True) == store.executable("0.2.0")
    assert store.staging.is_dir()
    assert store.current() == "0.2.0"
    assert not store.journal.exists()
    assert not store.recover()
    assert store.current() == "0.2.0"
