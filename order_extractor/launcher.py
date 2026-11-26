import argparse
import json
import multiprocessing
import os
import random
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from filelock import FileLock, Timeout
from platformdirs import user_data_dir

from . import VERSION
from .control import create_control, demo_key, un64
from .license import LicenseClient
from .local import create_local
from .supervisor import packaged_installation
from .updates import Updates


def run_pdf_worker(arguments):
    if (os.name != "nt" or not getattr(sys, "frozen", False) or not arguments
            or arguments[1:] not in ([], ["--trace-probe"])):
        raise SystemExit(2)
    from .local import worker
    from .windows_appcontainer import validate_token

    try:
        validate_token()
        # The parent resolves the working directory before granting the container access.
        # Re-resolving a Win32 path here can require inspecting protected parent folders.
        directory = Path.cwd()
        worker(directory / "source.pdf", directory)
    except OSError as error:
        if arguments[1:] == ["--trace-probe"]:
            raise
        raise SystemExit(1) from error


def check_packaged_worker():
    from .windows_appcontainer import AppContainerProcess
    from .worker_protocol import MAX_RESULT, Result, read_artifact, result_protocol

    fixture = Path(__file__).with_name("worker-probe.pdf")
    with TemporaryDirectory(prefix="oe-probe-") as name:
        work = Path(name)
        shutil.copyfile(fixture, work / "source.pdf")
        process = AppContainerProcess(Path(sys.executable), ["--pdf-worker", str(work), "--trace-probe"], work)
        try:
            deadline = time.monotonic() + 20
            while process.is_alive() and time.monotonic() < deadline:
                time.sleep(.1)
            if process.is_alive():
                raise RuntimeError("Tempo massimo di verifica del worker PDF superato.")
            process.finish()
            result = result_protocol.validate_json(read_artifact(work / "result.json", MAX_RESULT))
            if (not isinstance(result, Result) or result.engines != ["OCR Tesseract"]
                    or [row.article for row in result.rows] != ["00042", "ART003", "A-0009"]):
                raise RuntimeError("Il worker PDF del pacchetto non supera la prova sintetica.")
        finally:
            process.stop()
            diagnostic = work / "probe.log"
            if diagnostic.is_file():
                print(read_artifact(diagnostic, 16384).decode("utf-8", errors="replace"), flush=True)


def check_webview(url, ready, storage):
    import webview

    confirmed = False
    window = webview.create_window("Order Extractor", url, width=840, height=640, hidden=True)

    def close_trial():
        nonlocal confirmed
        confirmed = ready.wait(16)
        window.destroy()

    webview.start(close_trial, gui="edgechromium", private_mode=True, storage_path=str(storage))
    if not confirmed or webview.renderer != "edgechromium":
        raise RuntimeError("WebView2 non ha confermato il caricamento dell’applicazione.")


def serve(app_factory, host="127.0.0.1", port=0):
    sock = socket.socket()
    sock.bind((host, port))
    origin = f"http://{host}:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app_factory(origin), log_level="warning", access_log=False, timeout_graceful_shutdown=4))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            return origin, server, thread
        if not thread.is_alive():
            break
        time.sleep(.02)
    raise RuntimeError("Il servizio locale non si è avviato.")


def admin_verifier():
    issuer, audience, jwks_url = (os.environ[name] for name in ("OE_OIDC_ISSUER", "OE_OIDC_AUDIENCE", "OE_OIDC_JWKS"))
    if not issuer.startswith("https://") or not jwks_url.startswith("https://"):
        raise ValueError("Issuer e JWKS devono usare HTTPS.")
    keys = jwt.PyJWKClient(jwks_url, timeout=5)

    def verify(token):
        key = keys.get_signing_key_from_jwt(token)
        data = jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                          issuer=issuer, audience=audience, options={"require": ["exp", "sub", "iat"]})
        if not isinstance(data.get("amr"), list) or "mfa" not in data["amr"]:
            raise ValueError("MFA richiesta.")
        if not isinstance(data.get("roles"), list) or not all(isinstance(role, str) for role in data["roles"]):
            raise ValueError("Ruoli non validi.")
        return data["sub"][:180], set(data["roles"])
    return verify


def main():
    if sys.argv[1:2] == ["--pdf-worker"]:
        return run_pdf_worker(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Order Extractor")
    parser.add_argument("mode", choices=("client", "demo", "server"), nargs="?", default="client")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--webview", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--probe-version", help=argparse.SUPPRESS)
    args = parser.parse_args()
    packaged = getattr(sys, "frozen", False)
    if packaged and args.mode != "client":
        parser.error("Questo pacchetto contiene il client desktop.")
    if args.probe_version and (not packaged or args.probe_version != VERSION or not args.data_dir):
        parser.error("Controllo avvio non compatibile.")
    resources = Path(__file__).parent
    local_preview = False
    if packaged:
        config = json.loads((resources / "desktop-config.json").read_text(encoding="utf-8-sig"))
        local_preview = config.get("local_preview", False)
        runtime = resources / "ocr"
        os.environ["OE_TESSERACT"] = str(runtime / "tesseract.exe")
        os.environ["TESSDATA_PREFIX"] = str(runtime / "tessdata")
        os.environ["OE_OCR_LANGS"] = config.get("ocr_languages", "eng")
    product = "OrderExtractorPreview" if local_preview else "OrderExtractor"
    base = args.data_dir or Path(user_data_dir(product, appauthor=False))
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    handles = []
    stop = threading.Event()
    updates = None
    demo_dir = demo_control = None
    installation = packaged_installation() if not args.probe_version else None
    payload_lock = FileLock(str(installation / "payload.lock"), timeout=0) if installation is not None else None
    try:
        if payload_lock is not None:
            payload_lock.acquire()
        if args.mode == "server":
            key = serialization.load_pem_private_key(Path(os.environ["OE_SIGNING_KEY"]).read_bytes(), password=None)
            app = create_control(os.environ["OE_DATABASE"], key, un64(os.environ["OE_PEPPER"]),
                                 admin_auth=admin_verifier(), updates_root=os.environ.get("OE_UPDATES_ROOT"))
            uvicorn.run(app, host=os.environ.get("OE_BIND", "127.0.0.1"), port=args.port or 8100, access_log=False)
            return
        if args.mode == "demo" or local_preview:
            demo_dir = TemporaryDirectory(prefix="oe-demo-")
            demo_root = Path(demo_dir.name)
            key = demo_key()
            admin = secrets.token_urlsafe(32)
            demo_control = create_control(
                "sqlite:///" + (demo_root / "central.sqlite").as_posix(),
                key, secrets.token_bytes(32), demo_token=admin,
            )
            control_url, server, thread = serve(lambda _: demo_control)
            handles.append((server, thread))
            headers = {"Authorization": "Bearer " + admin}
            with httpx.Client(timeout=5, trust_env=False) as setup:
                customer = setup.post(control_url + "/api/v1/admin/customers", headers=headers,
                                      json={"name": "Cliente dimostrativo"})
                customer.raise_for_status()
                serial = setup.post(control_url + "/api/v1/admin/licenses", headers=headers,
                                      json={"customer_id": customer.json()["id"], "seats": 3, "channel": "dev"})
                serial.raise_for_status()
            serial_number = serial.json()["serial"]
            licensing = LicenseClient(base / "demo", control_url, key.public_key(), loopback=True)
            if licensing.path.is_file():
                licensing.path.unlink()
                licensing = LicenseClient(base / "demo", control_url, key.public_key(), loopback=True)
            if local_preview:
                licensing.activate(serial_number, "Anteprima locale")
            else:
                print("Seriale demo:", serial_number, flush=True)
                print("Dashboard demo:", control_url, flush=True)
                print("Token admin demo:", admin, flush=True)
            base = base / "demo"
            sample = Path(__file__).with_name("sample.pdf")
        else:
            if packaged:
                url = config["control_url"]
                public_file = resources / "lease-public.pem"
                update_root = resources / "update-root.json" if config.get("enable_updates") else None
            else:
                url = os.environ.get("OE_CONTROL_URL")
                public_file = os.environ.get("OE_LEASE_PUBLIC_KEY")
                update_root = os.environ.get("OE_UPDATE_TRUSTED_ROOT")
            if not url or not public_file:
                parser.error("Configura OE_CONTROL_URL e OE_LEASE_PUBLIC_KEY oppure avvia la modalità demo.")
            key = serialization.load_pem_public_key(Path(public_file).read_bytes())
            licensing = LicenseClient(base, url, key)
            if update_root:
                updates = Updates(base / "updates", url.rstrip("/") + "/api/v1/updates", Path(update_root).read_bytes())
            sample = None

        def renew():
            delay = 30
            while not stop.wait(delay):
                status = licensing.status()
                if status.get("verified") and (time.time() - status["verified"] > 24 * 3600 or status["state"] == "clock"):
                    try:
                        licensing.refresh()
                    except ValueError:
                        delay = min(3600, delay * 2) + random.uniform(0, 15)
                        continue
                delay = 900 + random.uniform(0, 120)

        if not args.probe_version:
            threading.Thread(target=renew, daemon=True).start()
        startup_ready = threading.Event() if args.probe_version else None
        ticket = secrets.token_urlsafe(36)
        origin, server, thread = serve(
            lambda origin: create_local(base, origin, ticket, licensing, sample, updates, installation,
                                        background=not args.probe_version, startup_ready=startup_ready), port=args.port
        )
        handles.append((server, thread))
        url = f"{origin}/open?ticket={ticket}"
        if args.probe_version:
            from .extraction import pdfium

            print("Verifica runtime OCR e PDF", flush=True)
            languages = subprocess.run([os.environ["OE_TESSERACT"], "--list-langs"], check=True,
                                       capture_output=True, timeout=10).stdout.splitlines()
            if not {b"ita", b"eng"}.issubset(languages):
                raise RuntimeError("Modelli OCR richiesti non disponibili.")
            if not pdfium.PDFIUM_INFO:
                raise RuntimeError("Runtime PDF assente.")
            with closing(sqlite3.connect(base / "local.sqlite")) as db:
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Controllo SQLite non riuscito.")
            print("Verifica worker AppContainer", flush=True)
            check_packaged_worker()
            print("Verifica WebView2", flush=True)
            check_webview(url, startup_ready, base / "webview-probe")
            return
        if (args.webview or packaged) and not args.no_browser:
            import webview
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["ALLOW_FILE_URLS"] = False
            webview.create_window("Order Extractor", url, width=1440, height=930, min_size=(800, 640))
            webview.start(gui="edgechromium")
        else:
            if args.no_browser:
                print("Apri nel browser:", url, flush=True)
            else:
                webbrowser.open(url)
            while thread.is_alive():
                thread.join(.5)
    except (KeyboardInterrupt, Timeout):
        if args.probe_version:
            raise
    finally:
        stop.set()
        for server, thread in reversed(handles):
            server.should_exit = True
            thread.join(6)
        if demo_control is not None:
            demo_control.state.engine.dispose()
        if demo_dir is not None:
            demo_dir.cleanup()
        if payload_lock is not None:
            payload_lock.release()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
