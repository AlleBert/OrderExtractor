"""Start the selected desktop payload and apply a previously confirmed update."""

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from filelock import FileLock, Timeout
from platformdirs import user_data_dir

from . import VERSION
from .license import LicenseClient
from .preview_updates import GitHubUpdates
from .updates import Updates
from .version_store import SwitchError, VersionStore, durable_json, release_key

REQUEST = "requested-update.json"
OUTCOME = "last-update.json"
SUPERVISOR = "OrderExtractorLauncher.exe"


def packaged_installation():
    if getattr(sys, "frozen", False):
        version_dir = Path(sys.executable).resolve().parent
        if version_dir.name == VERSION and version_dir.parent.name == "versions":
            home = version_dir.parent.parent
            if (home / SUPERVISOR).is_file():
                return home
    return None


def probe(executable, data, version):
    try:
        checked = subprocess.run([executable, "--data-dir", str(data), "--probe-version", version],
                                 cwd=executable.parent, timeout=60, check=False)
        return checked.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def trusted_publisher(executable, thumbprint):
    if os.name != "nt" or not isinstance(thumbprint, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", thumbprint):
        return False
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = (
        "$sig = Get-AuthenticodeSignature -LiteralPath $env:OE_VERIFIED_EXE; "
        "if ($sig.Status -ne 'Valid' -or $null -eq $sig.SignerCertificate) { exit 1 }; "
        "[Console]::WriteLine($sig.SignerCertificate.Thumbprint)"
    )
    try:
        result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "OE_VERIFIED_EXE": str(executable)}, capture_output=True,
            timeout=25, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode == 0 and result.stdout.strip().upper() == thumbprint.upper().encode("ascii")
    except (OSError, subprocess.TimeoutExpired):
        return False


def installed_config(store, version):
    resources = store.executable(version).parent / "_internal" / "order_extractor"
    config = json.loads((resources / "desktop-config.json").read_text(encoding="utf-8-sig"))
    return resources, config


def download_update(store, previous, version, package):
    resources, config = installed_config(store, previous)
    github_updates = config.get("github_updates")
    if github_updates and config.get("local_preview"):
        return GitHubUpdates(previous, store.home, preview=True).get(path=package, expected=version)
    if not github_updates and not config.get("enable_updates"):
        raise SwitchError("Gli aggiornamenti sono disabilitati.")
    public = serialization.load_pem_public_key((resources / "lease-public.pem").read_bytes())
    license = LicenseClient(store.data, config["control_url"], public)
    if github_updates:
        if not license.status()["active"]:
            raise SwitchError("Licenza non attiva.")
        return GitHubUpdates(previous, store.home, preview=False).get(path=package, expected=version)
    channel, credential = license.update_access()
    updates = Updates(store.data / "updates", config["control_url"].rstrip("/") + "/api/v1/updates",
                      (resources / "update-root.json").read_bytes())
    result = updates.get(channel, credential, path=package, expected=version, package="desktop.zip")
    return result["sha256"]


def apply_pending(store, *, fetch=download_update, health=None):
    recovered = store.recover()
    if recovered:
        durable_json(store.home / OUTCOME, {"success": False,
                     "message": "Aggiornamento interrotto. È stata ripristinata la versione precedente."})
    request = store.home / REQUEST
    if not request.is_file():
        return
    try:
        try:
            selection = json.loads(request.read_bytes())
        finally:
            request.unlink(missing_ok=True)
        version = selection["version"]
        now = time.time()
        if not now - 86_400 <= selection["requested"] <= now + 60:
            raise SwitchError("La conferma dell’aggiornamento è scaduta.")
        previous = store.current()
        if release_key(version) <= release_key(previous):
            raise SwitchError("La versione proposta non è più recente.")
        if health is None:
            _, config = installed_config(store, previous)
            if config.get("github_updates"):
                health = probe  # The archive was verified against GitHub build provenance before extraction.
            else:
                thumbprint = config["publisher_thumbprint"]

                def health(executable, data, candidate):
                    return trusted_publisher(executable, thumbprint) and probe(executable, data, candidate)

        with tempfile.TemporaryDirectory(dir=store.home, prefix="update-download-") as directory:
            package = Path(directory) / "desktop.zip"
            digest = fetch(store, previous, version, package)
            store.apply(version, package, digest, health)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, Timeout, sqlite3.Error, zipfile.BadZipFile):
        durable_json(store.home / OUTCOME, {"success": False,
                     "message": "Aggiornamento non riuscito. È rimasta attiva la versione precedente."})
    else:
        durable_json(store.home / OUTCOME, {"success": True, "message": f"Versione {version} installata."})


def main():
    home = Path(sys.executable).resolve().parent
    preview = (home / "_internal" / "order-extractor-preview").is_file()
    beta = (home / "_internal" / "order-extractor-beta").is_file()
    product = "OrderExtractorPreview" if preview else "OrderExtractorBeta" if beta else "OrderExtractor"
    data = Path(user_data_dir(product, appauthor=False))
    store = VersionStore(home, data, data_subdir="demo" if preview else None)
    try:
        with FileLock(str(home / "launch.lock"), timeout=0):
            with FileLock(str(home / "payload.lock"), timeout=0):
                if not store.pointer.exists() and not store.journal.exists():
                    if not store.executable(VERSION).is_file():
                        raise SwitchError("Versione iniziale mancante.")
                    durable_json(store.pointer, {"version": VERSION})
                apply_pending(store)
                current = store.current()
                if release_key(VERSION) > release_key(current) and store.executable(VERSION).is_file():
                    _, config = installed_config(store, VERSION)
                    if config.get("local_preview") or config.get("github_updates") or beta and config.get("local_ai"):
                        durable_json(store.pointer, {"version": VERSION})
                        current = VERSION
                executable = store.executable(current)
            return subprocess.run([executable], cwd=executable.parent, check=False).returncode
    except Timeout:
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error):
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, "Avvio non riuscito. Verifica l’installazione di Order Extractor.",
                                            "Order Extractor", 0x10)
        return 1
