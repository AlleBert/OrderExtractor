"""Side-by-side release activation and recovery for the desktop supervisor."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PureWindowsPath

from filelock import FileLock

from .updates import MAX_INSTALLER, RELEASE

EXECUTABLE = "OrderExtractor.exe"
MAX_UNPACKED = 2 * 1024**3
MAX_FILES = 40_000
TABLE_DATA = "local.sqlite"
LICENCE = "license.bin"
WINDOWS_DEVICE = re.compile(r"^(?:AUX|CON|NUL|PRN|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)", re.I)
WINDOWS_COMPONENT = re.compile(r'^[^<>:"/\\|?*\x00-\x1f]+$')


class SwitchError(ValueError):
    pass


def release_key(version):
    if not isinstance(version, str) or re.fullmatch(RELEASE, version) is None:
        raise SwitchError("Versione locale non valida.")
    return tuple(map(int, version.split(".")))


def durable_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(value, separators=(",", ":")).encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def checked_paths(package):
    seen = set()
    total = 0
    entries = package.infolist()
    if not entries or len(entries) > MAX_FILES:
        raise SwitchError("Numero di file del pacchetto oltre i limiti.")
    for entry in entries:
        name = entry.orig_filename
        components = name.removesuffix("/").split("/")
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (name != entry.filename or any(not WINDOWS_COMPONENT.fullmatch(part) or part in {".", ".."}
                or part.endswith((" ", ".")) or WINDOWS_DEVICE.match(part.split(".", 1)[0].rstrip())
                for part in components)
                or mode not in {0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG}
                or entry.flag_bits & 1):
            raise SwitchError("Percorso o tipo di file del pacchetto non consentito.")
        key = tuple(part.casefold() for part in components)
        if key in seen:
            raise SwitchError("Percorso duplicato nel pacchetto.")
        seen.add(key)
        total += entry.file_size
        if total > MAX_UNPACKED:
            raise SwitchError("Dimensione estratta oltre il limite.")
        yield entry, components


def extract_verified(archive, destination, digest):
    with archive.open("rb") as source:
        if not 0 < os.fstat(source.fileno()).st_size <= MAX_INSTALLER:
            raise SwitchError("Dimensione pacchetto non valida.")
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise SwitchError("Il pacchetto è cambiato dopo la verifica.")
        source.seek(0)
        with zipfile.ZipFile(source) as package:
            entries = list(checked_paths(package))
            needed = sum(entry.file_size for entry, _ in entries)
            if shutil.disk_usage(destination).free < needed + 16 * 1024**2:
                raise SwitchError("Spazio insufficiente per estrarre il pacchetto.")
            for entry, parts in entries:
                relative = PureWindowsPath(*parts)
                path = destination.joinpath(*relative.parts)
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as incoming, path.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
    if not (destination / EXECUTABLE).is_file():
        raise SwitchError("Eseguibile mancante nel pacchetto.")


class VersionStore:
    """Keep known payloads under a dedicated directory managed by the installer."""

    def __init__(self, installation, data, *, data_subdir=None):
        self.home = Path(installation)
        self.data = Path(data)
        self.data_subdir = data_subdir
        self.versions = self.home / "versions"
        self.pointer = self.home / "current.json"
        self.journal = self.home / "pending.json"
        self.staging = self.home / "pending-content"
        self.lock = FileLock(str(self.home / "update.lock"), timeout=0)

    def executable(self, version):
        release_key(version)
        return self.versions / version / EXECUTABLE

    def current(self):
        version = json.loads(self.pointer.read_bytes())["version"]
        if not self.executable(version).is_file():
            raise SwitchError("La versione attiva non è presente.")
        return version

    def recover(self):
        """Restore the known version when a previous switch was interrupted."""
        with self.lock:
            return self._recover_locked()

    def _recover_locked(self):
        if not self.journal.exists():
            if self.staging.exists():
                shutil.rmtree(self.staging, ignore_errors=True)
            return False
        journal = json.loads(self.journal.read_bytes())
        previous = journal["previous"]
        candidate = journal["candidate"]
        if release_key(candidate) <= release_key(previous) or not self.executable(previous).is_file():
            raise SwitchError("Impossibile identificare la versione precedente.")
        durable_json(self.pointer, {"version": previous})
        release = self.executable(candidate).parent
        if release.exists():
            shutil.rmtree(release, ignore_errors=True)
            sync_directory(self.versions)
        self.journal.unlink()
        sync_directory(self.home)
        if self.staging.exists():
            shutil.rmtree(self.staging, ignore_errors=True)
        return True

    def _snapshot(self, target):
        source = self.data
        if self.data_subdir:
            source = source / self.data_subdir
            target = target / self.data_subdir
        target.mkdir(parents=True)
        database = source / TABLE_DATA
        if database.is_file():
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as origin:
                with closing(sqlite3.connect(target / TABLE_DATA)) as copy:
                    origin.backup(copy)
        licence = source / LICENCE
        if licence.is_file():
            shutil.copyfile(licence, target / LICENCE)

    def apply(self, version, archive, digest, probe):
        """Activate a verified release only once its probe passes on copied data."""
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise SwitchError("Digest del pacchetto non valido.")
        with self.lock:
            self._recover_locked()
            previous = self.current()
            if release_key(version) <= release_key(previous):
                raise SwitchError("La versione proposta non è più recente.")
            target = self.executable(version).parent
            if target.exists() or self.staging.exists():
                raise SwitchError("La directory della nuova versione è già occupata.")
            durable_json(self.journal, {"previous": previous, "candidate": version})
            try:
                self.staging.mkdir(mode=0o700)
                payload = self.staging / "payload"
                payload.mkdir()
                extract_verified(Path(archive), payload, digest)
                snapshot = self.staging / "data"
                self._snapshot(snapshot)
                os.replace(payload, target)
                sync_directory(self.versions)
                if not probe(self.executable(version), snapshot, version):
                    raise SwitchError("La nuova versione non ha superato il controllo di avvio.")
                durable_json(self.pointer, {"version": version})
                self.journal.unlink()
                sync_directory(self.home)
            except Exception:
                self._recover_locked()
                raise
            shutil.rmtree(self.staging, ignore_errors=True)
            return self.executable(version)
