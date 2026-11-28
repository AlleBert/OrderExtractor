import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import VERSION
from .core import NumberStyle, Revision, export, validate
from .local_ai import (
    MODEL,
    check_model,
    infer_document,
    infer_page,
    pull_model,
    read_ai_page,
)
from .manual import (
    ColumnRules,
    PageMapping,
    confirmed_header,
    read_mapping,
    replace_page,
)
from .supervisor import OUTCOME, REQUEST
from .table_mapping import numeric
from .updates import RELEASE
from .version_store import durable_json
from .worker_limits import WorkerProcess
from .worker_protocol import (
    MAX_PREVIEW,
    MAX_RESULT,
    Failure,
    Progress,
    read_artifact,
    result_protocol,
)

MAX_UPLOAD = 50 * 1024 * 1024
ROOT = Path(__file__).parent / "web"
COOKIE = "oe_session"


def worker(path, directory, rapid_ocr=False, text_only=False):
    from .extraction import extract

    path = Path(path)
    directory = Path(directory)

    def write(name, data):
        temporary = directory / (name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(directory / (name + ".json"))

    try:
        def progress(index, total):
            write("progress", {"checked": index, "total": total})

        result = extract(path, directory, progress, rapid_ocr=rapid_ocr, text_only=text_only)
        write("result", result)
    except ValueError as error:
        write("result", {"error": str(error)[:500]})
    except Exception:
        write("result", {"error": "L’elaborazione del PDF è terminata con un errore."})
    finally:
        path.unlink(missing_ok=True)


class LocalStore:
    def __init__(self, base):
        self.path = base / "local.sqlite"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, hash TEXT NOT NULL, created TEXT NOT NULL,
                    pages INTEGER NOT NULL, rows INTEGER NOT NULL, status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_hash ON history(hash);
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, style TEXT NOT NULL, currency TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_layouts (
                    layout_key TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS profile_layouts_profile ON profile_layouts(profile_id);
                CREATE TABLE IF NOT EXISTS profile_columns (
                    header_key TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    rules TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS profile_columns_profile ON profile_columns(profile_id);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, created INTEGER NOT NULL, payload TEXT NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = sqlite3.Row
        return db

    def query(self, sql, params=()):
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def profile_for_layout(self, layout):
        if not layout:
            return None
        rows = self.query("""SELECT p.* FROM profiles p
                             JOIN profile_layouts l ON p.id=l.profile_id
                             WHERE l.layout_key=?""", (layout,))
        return rows[0] if rows else None

    def column_rules(self, keys):
        keys = sorted(set(keys))
        rules = {}
        for offset in range(0, len(keys), 500):
            chunk = keys[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            matches = self.query(f"""SELECT c.header_key, c.profile_id, c.rules, p.name FROM profile_columns c
                                   JOIN profiles p ON p.id=c.profile_id WHERE c.header_key IN ({placeholders})""", chunk)
            for match in matches:
                try:
                    rules[match["header_key"]] = dict(profile_id=match["profile_id"], name=match["name"],
                                                       **ColumnRules.model_validate_json(match["rules"]).model_dump())
                except ValueError:
                    continue
        return rules

    def save_profile(self, profile, layout=None, manuals=None):
        name = profile.name.strip()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id,style,currency FROM profiles WHERE name=?", (name,)).fetchone()
            entry = db.execute("""INSERT INTO profiles VALUES (?,?,?,?) ON CONFLICT(name)
                                  DO UPDATE SET style=excluded.style,currency=excluded.currency
                                  RETURNING id""",
                               (str(uuid.uuid4()), name, profile.number_style, profile.currency)).fetchone()
            if previous and (previous["style"], previous["currency"]) != (profile.number_style, profile.currency):
                db.execute("DELETE FROM profile_layouts WHERE profile_id=?", (entry["id"],))
            if layout:
                db.execute("""INSERT INTO profile_layouts VALUES (?,?) ON CONFLICT(layout_key)
                              DO UPDATE SET profile_id=excluded.profile_id""", (layout, entry["id"]))
            if manuals:
                db.executemany("""INSERT INTO profile_columns VALUES (?,?,?) ON CONFLICT(header_key)
                                DO UPDATE SET profile_id=excluded.profile_id,rules=excluded.rules""",
                               [(key, entry["id"], rules.model_dump_json()) for key, rules in manuals.items()])

    def diagnostics_enabled(self):
        return bool(self.query("SELECT 1 FROM settings WHERE key='diagnostics' AND value='on'"))

    def event(self, code, severity, *, page_count=None, row_count=None):
        now = int(time.time())
        identifier = str(uuid.uuid4())
        payload = dict(event_id=identifier, event_code=code, severity=severity, app_version=VERSION,
            timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat(), page_count=page_count, row_count=row_count)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO outbox SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM settings WHERE key='diagnostics' AND value='on')",
                       (identifier, now, json.dumps(payload)))
            db.execute("DELETE FROM outbox WHERE created<? OR id IN (SELECT id FROM outbox ORDER BY created DESC LIMIT -1 OFFSET 10000)",
                       (now - 14 * 86400,))


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    number_style: NumberStyle
    currency: str = Field(pattern="^(EUR|USD|GBP|CHF)?$")
    layout_token: str | None = Field(default=None, max_length=64)


class Activation(BaseModel):
    serial: str = Field(min_length=12, max_length=128)
    label: str = Field(default="", max_length=80)


class Privacy(BaseModel):
    diagnostics: bool


class UpdateSelection(BaseModel):
    version: str = Field(pattern=f"^{RELEASE}$")


class AISelection(BaseModel):
    revision: int = Field(ge=0)


class Job:
    def __init__(self, path, token, document_hash, duplicate, temp, rapid_ocr=False, local_ai=False, diagnostics=False):
        self.token = token
        self.document_hash = document_hash
        self.duplicate = duplicate
        self.temp = temp
        self.directory = Path(temp.name)
        self.status = "running"
        self.started = time.monotonic()
        self.changed = self.started
        self.page = None
        self.data = Revision(revision=0, rows=[])
        self.result = {}
        self.approved = None
        self.header_anchors = []
        self.manual_rules = {}
        self.ai_documents = {}
        self.local_ai = local_ai
        self.ai_future = None
        self.cancel_ai = threading.Event()
        self.ai_progress = None
        self.trace = {} if diagnostics else None
        self.unresolved_pages = {}
        if os.name == "nt" and getattr(sys, "frozen", False):
            from .windows_appcontainer import AppContainerProcess

            self.process = AppContainerProcess(Path(sys.executable), ["--pdf-worker", str(self.directory)], self.directory)
        else:
            self.process = WorkerProcess(worker, (path, self.directory, rapid_ocr, local_ai), self.directory)

    def refresh(self, store):
        if self.status != "running":
            return
        if self.ai_future is not None:
            if not self.ai_future.done():
                return
            try:
                rows = self.ai_future.result()
            except Exception as error:
                self.status = "failed"
                self.result = {"error": str(error) if isinstance(error, ValueError) else "Elaborazione AI non riuscita."}
                self.temp.cleanup()
                return
            self.finish_review(rows, store)
            return
        elapsed = time.monotonic() - self.started
        progress = self.progress()
        if progress and self.page != progress["checked"]:
            self.page = progress["checked"]
            self.changed = time.monotonic()
        if self.process.is_alive():
            if elapsed > 900 or time.monotonic() - self.changed > 40:
                self.stop()
                self.status = "failed"
                self.result = {"error": "Tempo massimo di elaborazione superato."}
            return
        self.process.finish()
        try:
            payload = result_protocol.validate_json(read_artifact(self.directory / "result.json", MAX_RESULT))
        except (OSError, ValueError):
            payload = Failure(error="Il processo PDF si è arrestato o ha prodotto dati non validi.")
        if isinstance(payload, Failure):
            self.result = payload.model_dump()
            self.status = "failed"
            store.event("PDF_INVALID", "warning")
            self.temp.cleanup()
            return
        self.header_anchors = payload.header_anchors
        self.result = payload.model_dump(exclude={"rows", "header_anchors"})
        if self.local_ai:
            self.ai_future = Future()
            self.ai_progress = {"phase": "layout", "checked": 0, "total": payload.page_count, "chars": 0}

            def progress(page, total, phase, chars):
                self.ai_progress = {"phase": phase, "checked": page, "total": total, "chars": chars}

            def report(entry):
                if self.trace is not None:
                    self.trace[entry["page"]] = entry

            def analyze():
                if self.ai_future.set_running_or_notify_cancel():
                    try:
                        rows = infer_document(
                            self.directory, self.result, "auto", progress, cancel=self.cancel_ai.is_set,
                            report=report, unresolved=self.unresolved_pages.__setitem__,
                        )
                    except Exception as error:
                        self.ai_future.set_exception(error)
                    else:
                        self.ai_future.set_result(rows)

            threading.Thread(target=analyze, daemon=True).start()
            return
        self.finish_review(payload.rows, store)

    def finish_review(self, rows, store):
        self.status = "needs_review"
        self.ai_progress = None
        currencies = {row.currency for row in rows if row.currency}
        self.data = Revision(revision=0, rows=rows, number_style="en" if self.local_ai else "auto",
                             currency=next(iter(currencies)) if self.local_ai and len(currencies) == 1 else "")
        store.query(
            "INSERT INTO history VALUES (?, ?, datetime('now'), ?, ?, ?)",
            (self.token, self.document_hash, self.result["page_count"], len(self.data.rows), self.status),
        )
        if not self.data.rows:
            store.event("COLUMN_UNMAPPED", "warning", page_count=self.result["page_count"], row_count=0)

    def progress(self):
        try:
            return Progress.model_validate_json(read_artifact(self.directory / "progress.json", 512)).model_dump()
        except (OSError, ValueError):
            return {}

    def stop(self):
        self.cancel_ai.set()
        self.process.stop()
        self.temp.cleanup()

    def confirmed_manual_rules(self):
        pages = {span.page_index for row in self.data.rows for span in row.source_spans}
        by_header = {}
        conflicting = set()
        for page, (key, rules) in self.manual_rules.items():
            if page not in pages:
                continue
            if key in by_header and by_header[key] != rules:
                conflicting.add(key)
            by_header[key] = rules
        return {key: rules for key, rules in by_header.items() if key not in conflicting}

    def mapping_suggestions(self, store):
        if self.status != "needs_review" or not self.header_anchors:
            return []
        stored = store.column_rules(anchor.key for anchor in self.header_anchors)
        by_page = {}
        already_mapped = set(self.result.get("mapped_pages", []))
        for anchor in self.header_anchors:
            if anchor.key in stored and anchor.bottom < .995 and anchor.page not in already_mapped:
                by_page.setdefault(anchor.page, []).append(dict(page=anchor.page, top=anchor.bottom + .002,
                                                                **stored[anchor.key]))
        return [matches[0] for matches in by_page.values() if len(matches) == 1]

    def view(self, store):
        layout = self.result.get("layout_key")
        metadata = {key: value for key, value in self.result.items() if key != "layout_key"}
        return dict(token=self.token, status=self.status, duplicate=self.duplicate,
                    progress={**(self.ai_progress or self.progress()), "elapsed": round(time.monotonic() - self.started)}, **metadata, **self.data.model_dump(),
                    layout_profile=store.profile_for_layout(layout),
                    can_learn_layout=self.status == "exported" and bool(layout or self.confirmed_manual_rules()),
                    mapping_suggestions=self.mapping_suggestions(store),
                    diagnostics=self.trace is not None,
                    unresolved=[{"page": page, "reason": reason} for page, reason in self.unresolved_pages.items()]
                    if self.status != "running" else [],
                    issues=validate(self.data) if self.status in {"needs_review", "approved", "exported"} else [])


def create_local(base, origin, bootstrap, licensing, sample=None, updates=None, installation=None, *,
                 background=True, startup_ready=None, github_updates=None, local_ai=False, rapid_ocr=False):
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = LocalStore(base)
    guard = threading.RLock()
    privacy_lock = threading.RLock()
    update_lock = threading.Lock()
    ai_lock = threading.Lock()
    setup_lock = threading.Lock()
    setup = {"running": False, "completed": 0, "total": 0}
    session = secrets.token_urlsafe(32)
    job = None
    staged = None

    def clear_staged():
        nonlocal staged
        if staged is not None:
            staged[4].cleanup()
            staged = None

    @asynccontextmanager
    async def lifespan(app):
        if not background:
            yield
            return

        def maintain():
            while not stopped.wait(1):
                with guard:
                    if job:
                        job.refresh(store)
                if updates is not None and update_lock.acquire(blocking=False):
                    try:
                        if staged and staged[3] <= time.monotonic():
                            clear_staged()
                    finally:
                        update_lock.release()

        def send_diagnostics():
            delay = 15
            while not stopped.wait(delay):
                with privacy_lock:
                    if not store.diagnostics_enabled():
                        continue
                    store.query("DELETE FROM outbox WHERE created<?", (int(time.time()) - 14 * 86400,))
                    pending = store.query("SELECT id,payload FROM outbox ORDER BY created LIMIT 40")
                    if not pending:
                        continue
                    try:
                        accepted = set(licensing.send_events([json.loads(row["payload"]) for row in pending]))
                    except ValueError:
                        delay = min(delay * 2, 1800)
                        continue
                    delay = 15
                    with closing(store.connect()) as db, db:
                        db.executemany("DELETE FROM outbox WHERE id=?", [(row["id"],) for row in pending if row["id"] in accepted])

        stopped = threading.Event()
        monitor = threading.Thread(target=maintain, daemon=True)
        sender = threading.Thread(target=send_diagnostics, daemon=True)
        monitor.start()
        sender.start()
        try:
            yield
        finally:
            stopped.set()
            monitor.join()
            sender.join(8)
            with guard:
                if job:
                    job.stop()
            with update_lock:
                clear_staged()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def secure(request, call_next):
        if request.headers.get("host") != origin.removeprefix("http://"):
            return Response(status_code=421)
        if request.url.path != "/open" and not secrets.compare_digest(request.cookies.get(COOKIE, ""), session):
            return Response("Apri Order Extractor dal launcher.", status_code=403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("origin") != origin:
            return Response(status_code=403)
        if (startup_ready is not None and request.method not in {"GET", "HEAD"}
                and request.url.path != "/api/startup/ready"):
            return Response(status_code=403)
        declared = request.headers.get("content-length", "0")
        limit = MAX_UPLOAD + 65536 if request.url.path == "/api/jobs" else 12 * 1024 * 1024
        if not declared.isdecimal() or int(declared) > limit:
            return JSONResponse({"detail": "Dimensione massima della richiesta superata."}, status_code=413)
        if request.method in {"POST", "PUT", "PATCH"} and "content-length" not in request.headers:
            return Response(status_code=411)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })
        return response

    @app.get("/open")
    def open_session(ticket: str):
        nonlocal bootstrap
        with guard:
            if not bootstrap or not secrets.compare_digest(ticket, bootstrap):
                raise HTTPException(403)
            bootstrap = None
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict")
        return response

    @app.get("/")
    def home():
        return FileResponse(ROOT / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "app.css", "logo.png"}:
            raise HTTPException(404)
        return FileResponse(ROOT / name)

    @app.get("/sample.pdf")
    def example():
        if sample is None:
            raise HTTPException(404)
        return FileResponse(sample, media_type="application/pdf", filename="ordine-demo.pdf")

    @app.get("/api/status")
    def status():
        feedback = None
        if installation is not None:
            try:
                feedback = json.loads((installation / OUTCOME).read_bytes())["message"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return {"version": VERSION, "license": licensing.status(), "sample": sample is not None,
                "diagnostics": store.diagnostics_enabled(), "updates": updates is not None or github_updates is not None,
                "startup_check": startup_ready is not None, "local_ai": local_ai, "preview_updates": bool(github_updates and github_updates.preview),
                "managed_updates": installation is not None, "update_feedback": feedback,
                "update_pending": installation is not None and (installation / REQUEST).is_file()}

    @app.post("/api/startup/ready")
    def confirm_frontend():
        if startup_ready is None:
            raise HTTPException(404)
        startup_ready.set()
        return {"ready": True}

    def update_access():
        if updates is None:
            raise HTTPException(404, "Aggiornamenti non configurati.")
        try:
            return licensing.update_access()
        except ValueError as error:
            raise HTTPException(403, str(error)) from error

    def available_update(package):
        if github_updates is not None:
            if not licensing.status().get("active"):
                raise HTTPException(403, "Licenza non attiva.")
            return github_updates.get()
        channel, credential = update_access()
        return updates.get(channel, credential, package=package)

    @app.post("/api/update/check")
    def check_update():
        try:
            package = "desktop.zip" if installation is not None else "installer.exe"
            return {"release": available_update(package)}
        except ValueError as error:
            raise HTTPException(502, str(error)) from error

    @app.post("/api/update/schedule")
    def schedule_update(selection: UpdateSelection):
        if installation is None:
            raise HTTPException(404, "Installazione integrata non disponibile.")
        try:
            release = available_update("desktop.zip")
        except ValueError as error:
            raise HTTPException(502, str(error)) from error
        if release is None or release["version"] != selection.version:
            raise HTTPException(409, "Controlla di nuovo gli aggiornamenti prima di confermare.")
        with guard:
            if job:
                job.refresh(store)
                if job.status not in {"failed", "cancelled", "exported"}:
                    raise HTTPException(409, "Completa o chiudi l’ordine aperto prima di aggiornare.")
            try:
                durable_json(installation / REQUEST, {"version": selection.version, "requested": time.time()})
            except OSError as error:
                raise HTTPException(500, "Impossibile salvare la conferma di aggiornamento.") from error
        return {"scheduled": True}

    @app.post("/api/update/prepare")
    def prepare_update(selection: UpdateSelection):
        nonlocal staged
        channel, credential = update_access()
        if not update_lock.acquire(blocking=False):
            raise HTTPException(409, "Scaricamento aggiornamento in corso.")
        temporary = None
        try:
            clear_staged()
            temporary = tempfile.TemporaryDirectory(prefix="oe-update-")
            updates.get(channel, credential, path=Path(temporary.name) / "installer.exe", expected=selection.version)
            token = secrets.token_urlsafe(24)
            staged = (token, selection.version, channel, time.monotonic() + 300, temporary)
            temporary = None
            return {"download": "/api/update/download/" + token}
        except (ValueError, OSError) as error:
            raise HTTPException(502, "Download verificato non riuscito." if isinstance(error, OSError) else str(error)) from error
        finally:
            if temporary is not None:
                temporary.cleanup()
            update_lock.release()

    @app.get("/api/update/download/{token}")
    def download_update(token: str):
        nonlocal staged
        channel, _ = update_access()
        with update_lock:
            if (not staged or staged[2] != channel or staged[3] <= time.monotonic()
                    or not secrets.compare_digest(token, staged[0])):
                raise HTTPException(404, "Download scaduto. Prepara di nuovo il pacchetto.")
            _, version, _, _, temporary = staged
            staged = None
        return FileResponse(Path(temporary.name) / "installer.exe", media_type="application/octet-stream",
                            filename=f"OrderExtractor-{version}-x64.exe", background=BackgroundTask(temporary.cleanup))

    @app.put("/api/privacy")
    def privacy(data: Privacy):
        with privacy_lock, closing(store.connect()) as db, db:
            db.execute("INSERT INTO settings VALUES ('diagnostics', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ("on" if data.diagnostics else "off",))
            if not data.diagnostics:
                db.execute("DELETE FROM outbox")
        return {"diagnostics": data.diagnostics}

    @app.post("/api/activate")
    def activate(data: Activation):
        try:
            with privacy_lock:
                result = licensing.activate(data.serial.strip(), data.label.strip())
                store.query("DELETE FROM outbox")
                return result
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/license/refresh")
    def refresh_license():
        try:
            with privacy_lock:
                result = licensing.refresh()
                if result["state"] == "released":
                    store.query("DELETE FROM outbox")
                return result
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/license/deactivate")
    def deactivate_license():
        with guard:
            if job:
                job.refresh(store)
                if job.status not in {"failed", "cancelled", "exported"}:
                    raise HTTPException(409, "Completa o chiudi l’ordine aperto prima di disattivare la postazione.")
            try:
                with privacy_lock:
                    result = licensing.deactivate()
                    store.query("DELETE FROM outbox")
                    return result
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            except OSError as error:
                raise HTTPException(503, "Impossibile salvare lo stato della licenza locale.") from error

    @app.get("/api/history")
    def history():
        return store.query("SELECT id,created,pages,rows,status FROM history ORDER BY created DESC LIMIT 50")

    @app.get("/api/profiles")
    def profiles():
        return store.query("""SELECT p.*, (SELECT count(*) FROM profile_layouts l WHERE l.profile_id=p.id) AS layouts,
                              (SELECT count(*) FROM profile_columns c WHERE c.profile_id=p.id) AS column_layouts
                              FROM profiles p ORDER BY name""")

    @app.post("/api/profiles")
    def save_profile(data: Profile):
        if not data.name.strip():
            raise HTTPException(422, "Inserisci il nome del profilo.")
        with guard:
            layout = None
            manuals = None
            if data.layout_token:
                current = find(data.layout_token)
                layout = current.result.get("layout_key")
                manuals = current.confirmed_manual_rules()
                if (current.status != "exported" or not (layout or manuals)
                        or (current.data.number_style, current.data.currency) != (data.number_style, data.currency)):
                    raise HTTPException(409, "Collega il profilo dopo un’esportazione confermata con gli stessi valori.")
            store.save_profile(data, layout, manuals)
        return profiles()

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: str):
        store.query("DELETE FROM profiles WHERE id=?", (profile_id,))
        return profiles()

    @app.get("/api/jobs/current")
    def current_job():
        with guard:
            if job is None:
                return None
            job.refresh(store)
            return job.view(store)

    @app.post("/api/jobs")
    def create_job(file: Annotated[UploadFile, File()], diagnostics: Annotated[bool, Form()] = False):
        nonlocal job
        with guard:
            if not licensing.status()["active"]:
                raise HTTPException(403, "Attiva una licenza valida prima di elaborare un PDF.")
            if job and job.status not in {"failed", "cancelled", "exported"}:
                raise HTTPException(409, "Chiudi il documento aperto prima di caricarne un altro.")
            temp = tempfile.TemporaryDirectory(prefix="oe-job-")
            path = Path(temp.name) / "source.pdf"
            digest = hashlib.sha256()
            size = 0
            try:
                if not file.file.read(8).startswith(b"%PDF-"):
                    raise HTTPException(415, "Il file non contiene una firma PDF valida.")
                file.file.seek(0)
                with path.open("wb") as output:
                    while block := file.file.read(1024 * 1024):
                        size += len(block)
                        if size > MAX_UPLOAD:
                            raise HTTPException(413, "Il PDF supera 50 MiB.")
                        digest.update(block)
                        output.write(block)
                document_hash = digest.hexdigest()
                duplicate = bool(store.query("SELECT 1 FROM history WHERE hash=? LIMIT 1", (document_hash,)))
                if job:
                    job.stop()
                job = Job(path, str(uuid.uuid4()), document_hash, duplicate, temp, rapid_ocr=rapid_ocr, local_ai=local_ai, diagnostics=local_ai and diagnostics)
                return job.view(store)
            except OSError as error:
                temp.cleanup()
                raise HTTPException(503, "Impossibile avviare il processo PDF protetto.") from error
            except BaseException:
                temp.cleanup()
                raise

    def find(token):
        if job is None or job.token != token:
            raise HTTPException(404)
        job.refresh(store)
        return job

    @app.get("/api/jobs/{token}")
    def get_job(token: str):
        with guard:
            return find(token).view(store)

    @app.delete("/api/jobs/{token}")
    def close_job(token: str):
        nonlocal job
        with guard:
            current = find(token)
            current.stop()
            if current.status in {"needs_review", "approved"}:
                store.query("UPDATE history SET status='cancelled' WHERE id=?", (token,))
            job = None
            return {"closed": True}

    @app.get("/api/jobs/{token}/pages/{index}")
    def preview(token: str, index: int):
        with guard:
            current = find(token)
            if not 0 <= index < current.result.get("page_count", 0):
                raise HTTPException(404)
            try:
                image = read_artifact(current.directory / f"{index}.png", MAX_PREVIEW)
            except (OSError, ValueError) as error:
                raise HTTPException(404, "Anteprima non disponibile.") from error
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise HTTPException(404, "Anteprima non disponibile.")
            return Response(image, media_type="image/png")

    def updated(token, data):
        current = find(token)
        if current.status not in {"needs_review", "approved", "exported"}:
            raise HTTPException(409, "Il documento non è pronto per la revisione.")
        if current.data.revision != data.revision:
            raise HTTPException(409, "Questa revisione è superata. Ricarica il documento.")
        return current

    @app.put("/api/jobs/{token}/draft")
    def save(token: str, data: Revision):
        with guard:
            current = updated(token, data)
            current.approved = None
            current.data = data.model_copy(update={"revision": data.revision + 1, "reviewed": False})
            current.status = "needs_review"
            store.query("UPDATE history SET status=?,rows=? WHERE id=?", (current.status, len(data.rows), token))
            return current.view(store)

    @app.put("/api/jobs/{token}/pages/{index}/mapping")
    def map_table(token: str, index: int, mapping: PageMapping):
        with guard:
            current = updated(token, mapping)
            if index not in current.result.get("mappable_pages", []):
                raise HTTPException(404, "Testo della pagina non disponibile.")
            try:
                page_rows = read_mapping(current.directory, index, mapping)
                if local_ai:
                    for row in page_rows:
                        for field in ("quantity", "unit_price"):
                            raw = getattr(row, field)
                            value = numeric(raw, "auto")
                            if value is not None:
                                setattr(row, field, value.replace(".", ",") if current.data.number_style == "it" else value)
                rows = replace_page(current.data.rows, index, page_rows)
            except OSError as error:
                raise HTTPException(404, "Testo della pagina non disponibile.") from error
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            store.query("UPDATE history SET status='needs_review',rows=? WHERE id=?", (len(rows), token))
            current.data = current.data.model_copy(update={"rows": rows, "revision": mapping.revision + 1, "reviewed": False})
            current.approved = None
            current.status = "needs_review"
            current.result["layout_key"] = None
            missing = f"Pagina {index + 1}: nessuna nuova riga individuata."
            current.result["notices"] = [notice for notice in current.result["notices"] if notice != missing]
            current.result["mapped_pages"] = sorted(set(current.result.get("mapped_pages", [])) | {index})
            current.unresolved_pages.pop(index, None)
            if current.trace is not None and index + 1 in current.trace:
                current.trace[index + 1]["manual_rows"] = len(page_rows)
            current.ai_documents.pop(index, None)
            current.result["ai_pages"] = sorted(current.ai_documents)
            key = confirmed_header(current.header_anchors, index, mapping)
            current.manual_rules.pop(index, None)
            if key:
                current.manual_rules[index] = (key, ColumnRules(right=mapping.right, columns=mapping.columns))
            return current.view(store)

    @app.get("/api/local-ai/status")
    def ai_status():
        if not local_ai:
            raise HTTPException(404)
        with setup_lock:
            state = dict(setup)
        if state["running"]:
            return {**state, "model": MODEL, "ready": False, "stage": "downloading"}
        try:
            return {**state, **check_model(), "stage": "ready"}
        except ValueError:
            from .local_ai import ollama_request

            try:
                ollama_request("GET", "/api/version")
            except ValueError:
                stage = "engine"
            else:
                stage = "model"
            return {**state, "model": MODEL, "ready": False, "stage": stage}

    @app.post("/api/local-ai/install-model")
    def ai_install_model():
        if not local_ai:
            raise HTTPException(404)
        with setup_lock:
            if not setup["running"]:
                setup.update(running=True, completed=0, total=0, error=None)

                def progress(completed, total):
                    with setup_lock:
                        setup.update(completed=completed, total=total)

                def download():
                    error = None
                    try:
                        pull_model(progress)
                    except Exception:
                        error = "Download non riuscito. Avvia Ollama e verifica la connessione Internet."
                    finally:
                        with setup_lock:
                            setup.update(running=False, error=error)

                threading.Thread(target=download, daemon=True).start()
        return {"running": True}

    @app.post("/api/jobs/{token}/pages/{index}/ai")
    def retry_with_ai(token: str, index: int, selection: AISelection):
        if not local_ai:
            raise HTTPException(404)
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(409, "È già in corso un’analisi con AI locale.")
        try:
            with guard:
                current = updated(token, selection)
                if not licensing.status()["active"]:
                    raise HTTPException(403, "Serve una licenza valida.")
                if index not in current.result.get("mappable_pages", []):
                    raise HTTPException(404, "Testo della pagina non disponibile.")
                try:
                    cached = read_ai_page(current.directory, index)
                except (OSError, ValueError) as error:
                    raise HTTPException(422, "Testo della pagina non disponibile.") from error
                style = current.data.number_style
            try:
                debug = {}
                proposed, document = infer_page(cached, index, "auto", debug=debug)
                if style == "it":
                    for row in proposed:
                        row.quantity, row.unit_price = row.quantity.replace(".", ","), row.unit_price.replace(".", ",")
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if not proposed:
                raise HTTPException(422, "L’AI locale non ha trovato righe articolo nella pagina.")
            with guard:
                current = updated(token, selection)
                if not licensing.status()["active"]:
                    raise HTTPException(403, "Serve una licenza valida.")
                try:
                    rows = replace_page(current.data.rows, index, proposed)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from error
                store.query("UPDATE history SET status='needs_review',rows=? WHERE id=?", (len(rows), token))
                current.data = current.data.model_copy(update={"rows": rows, "revision": selection.revision + 1,
                                                               "reviewed": False})
                current.approved = None
                current.status = "needs_review"
                current.result["layout_key"] = None
                missing = f"Pagina {index + 1}: nessuna nuova riga individuata."
                current.result["notices"] = [notice for notice in current.result["notices"] if notice != missing]
                current.result["mapped_pages"] = [page for page in current.result.get("mapped_pages", []) if page != index]
                current.manual_rules.pop(index, None)
                current.unresolved_pages.pop(index, None)
                if current.trace is not None:
                    current.trace[index + 1] = debug
                current.ai_documents[index] = document
                current.result["ai_pages"] = sorted(current.ai_documents)
                return current.view(store)
        finally:
            ai_lock.release()

    @app.get("/api/jobs/{token}/pages/{index}/ai.json")
    def ai_json(token: str, index: int):
        if not local_ai:
            raise HTTPException(404)
        with guard:
            document = find(token).ai_documents.get(index)
            if document is None:
                raise HTTPException(404)
            return JSONResponse(document.model_dump(), headers={
                "Content-Disposition": f'attachment; filename="righe-pagina-{index + 1}.json"'
            })

    @app.get("/api/jobs/{token}/diagnostics")
    def extraction_diagnostics(token: str, summary: bool = False, download: bool = False):
        with guard:
            current = find(token)
            if current.trace is None:
                raise HTTPException(404)
            pages = [entry for _, entry in sorted(current.trace.copy().items())]
            common = {"version": VERSION, "status": current.status,
                      "ollama_calls": sum(entry.get("ollama", {}).get("called", False) for entry in pages)}
            if summary:
                pages = [{key: entry[key] for key in ("page", "engine", "mode", "issues", "elapsed_ms") if key in entry}
                         | {"rows": entry.get("row_count", 0),
                            "ollama": {key: entry["ollama"][key] for key in ("eval_count", "prompt_eval_count", "elapsed_ms")
                                       if key in entry.get("ollama", {})}} for entry in pages]
            headers = {"Content-Disposition": 'attachment; filename="diagnostica-estrazione.json"'} if download else {}
            return JSONResponse({**common, "pages": pages}, headers=headers)

    @app.post("/api/jobs/{token}/approve")
    def approve(token: str, data: Revision):
        with guard:
            current = updated(token, data)
            if not licensing.status()["active"]:
                raise HTTPException(403, "Serve una licenza valida per approvare un nuovo CSV.")
            if current.unresolved_pages:
                raise HTTPException(422, "Conferma le colonne delle pagine segnalate prima di esportare.")
            if not data.reviewed or data.model_dump(exclude={"reviewed"}) != current.data.model_dump(exclude={"reviewed"}):
                raise HTTPException(409, "Salva la revisione e confermala prima di esportare.")
            try:
                payload = export(data)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            current.status = "approved"
            current.approved = payload
            store.query("UPDATE history SET status=? WHERE id=?", (current.status, token))
            return current.view(store)

    @app.get("/api/jobs/{token}/csv")
    def download(token: str):
        with guard:
            current = find(token)
            if current.status not in {"approved", "exported"} or current.approved is None:
                raise HTTPException(409, "Il CSV richiede la conferma dell’operatore.")
            if current.status == "approved":
                store.event("EXPORT_SUCCESS", "info", page_count=current.result["page_count"],
                            row_count=len(current.data.rows))
            current.status = "exported"
            store.query("UPDATE history SET status=? WHERE id=?", (current.status, token))
            return Response(current.approved, media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="ordine-{token[:8]}.csv"'})

    return app
