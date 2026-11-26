import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    select,
)

from .telemetry import Batch
from .updates import CHANNELS

ISSUER = "order-extractor-control"
AUDIENCE = "order-extractor"
META = MetaData()


def table(name, *columns):
    return Table(name, META, Column("id", String(64), primary_key=True), *columns)


CUSTOMERS = table("customers", Column("name", String(180), nullable=False), Column("status", String(16), nullable=False))
LICENSES = table(
    "licenses", Column("customer_id", ForeignKey("customers.id"), nullable=False, index=True),
    Column("digest", String(64), unique=True, nullable=False), Column("prefix", String(18), nullable=False),
    Column("status", String(16), nullable=False), Column("valid_until", Integer),
    Column("seats", Integer, nullable=False), Column("offline_days", Integer, nullable=False),
    Column("channel", String(16), nullable=False),
)
INSTALLS = table(
    "installations", Column("license_id", ForeignKey("licenses.id"), nullable=False, index=True),
    Column("device_key", String(64), nullable=False), Column("label", String(80), nullable=False),
    Column("status", String(16), nullable=False), Column("digest", String(64), unique=True, nullable=False),
    Column("first_seen", Integer, nullable=False), Column("last_seen", Integer, nullable=False),
    Column("app_version", String(40), nullable=False),
    UniqueConstraint("license_id", "device_key"),
)
CHALLENGES = table("challenges", Column("installation_id", ForeignKey("installations.id"), nullable=False),
                   Column("expires", Integer, nullable=False))
EVENTS = table("audit", Column("actor", String(180), nullable=False), Column("action", String(40), nullable=False),
               Column("target", String(64), nullable=False), Column("recorded", Integer, nullable=False))
DIAGNOSTICS = table("diagnostics", Column("installation_id", ForeignKey("installations.id"), nullable=False, index=True),
                    Column("event_id", String(36), nullable=False), Column("recorded", Integer, nullable=False, index=True),
                    Column("code", String(40), nullable=False), Column("severity", String(16), nullable=False),
                    Column("app_version", String(24), nullable=False), Column("page_count", Integer),
                    Column("row_count", Integer), UniqueConstraint("installation_id", "event_id"))


def uid():
    return str(uuid.uuid4())


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def un64(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def thumbprint(key):
    return b64(hashlib.sha256(('{"crv":"Ed25519","kty":"OKP","x":"' + key + '"}').encode()).digest())


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Activation(Strict):
    serial: str = Field(min_length=12, max_length=128)
    device_key: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    label: str = Field(default="", max_length=80)
    version: str = Field(max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")
    proof: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class Proof(Strict):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")
    version: str = Field(max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")


class CustomerInput(Strict):
    name: str = Field(min_length=1, max_length=180)


class CustomerChange(Strict):
    status: str = Field(pattern="^(active|suspended)$")


class LicenseInput(Strict):
    customer_id: str = Field(max_length=64)
    seats: int = Field(default=1, ge=1, le=1000)
    offline_days: int = Field(default=7, ge=1, le=7)
    valid_until: int | None = Field(default=None, ge=0, le=2147483647)
    channel: str = Field(default="stable", pattern="^(dev|beta|stable)$")


class LicenseChange(Strict):
    status: str = Field(default="active", pattern="^(active|suspended|revoked)$")
    seats: int = Field(ge=1, le=1000)
    offline_days: int = Field(ge=1, le=7)
    valid_until: int | None = Field(ge=0, le=2147483647)
    channel: str = Field(pattern="^(dev|beta|stable)$")


def create_control(database, signing_key, pepper, *, demo_token=None, admin_auth=None, updates_root=None):
    if len(pepper) < 32:
        raise ValueError("OE_PEPPER deve contenere almeno 32 byte casuali.")
    if demo_token is None and admin_auth is None:
        raise ValueError("Configura un identity provider amministrativo.")
    engine = create_engine(database, pool_pre_ping=True)
    META.create_all(engine)
    lock = threading.RLock()
    limiter = defaultdict(deque)
    attempts_lock = threading.Lock()
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.engine = engine
    web = Path(__file__).parent / "web"

    @app.middleware("http")
    async def sanitize(request, call_next):
        request_id = uid()
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        if request.method in {"POST", "PATCH"} and (
            content_length is None or not content_length.isdecimal() or int(content_length) > 16384
        ):
            response = JSONResponse({"code": "payload", "message": "Richiesta non valida.", "request_id": request_id}, 413)
        else:
            response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Request-ID": request_id,
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})
        return response

    @app.exception_handler(HTTPException)
    async def rejected(request, error):
        return JSONResponse({"code": "denied" if error.status_code in {401, 403} else "request",
                             "message": error.detail, "request_id": request.state.request_id}, error.status_code)

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({"code": "invalid", "message": "Dati della richiesta non validi.",
                             "request_id": request.state.request_id}, 422)

    def digest(value):
        return hmac.new(pepper, value.encode(), hashlib.sha256).hexdigest()

    def row(conn, query):
        return conn.execute(query).mappings().first()

    def audit(conn, actor, action, target):
        conn.execute(EVENTS.insert().values(id=uid(), actor=actor, action=action, target=target, recorded=int(time.time())))

    def active(license, customer, now):
        return (license["status"] == customer["status"] == "active"
                and (license["valid_until"] is None or license["valid_until"] > now))

    def lease(license, installation, now):
        expires = now + license["offline_days"] * 86400
        if license["valid_until"] is not None:
            expires = min(expires, license["valid_until"])
        return jwt.encode({
            "iss": ISSUER, "aud": AUDIENCE, "sub": installation["id"], "installation_id": installation["id"],
            "license_id": license["id"], "customer_id": license["customer_id"], "iat": now, "nbf": now,
            "exp": expires, "entitlement_end": license["valid_until"], "features": ["extract"],
            "channel": license["channel"], "policy_version": 1, "cnf": {"jkt": thumbprint(installation["device_key"])},
        }, signing_key, algorithm="EdDSA", headers={"kid": "lease-1", "typ": "JWT"})

    def admin(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if demo_token and secrets.compare_digest(token, demo_token):
            return "demo", {"owner"}
        if token and admin_auth:
            try:
                return admin_auth(token)
            except (jwt.PyJWTError, ValueError, KeyError):
                pass
        raise HTTPException(401, "Autenticazione amministrativa richiesta.")

    def writable(identity):
        if not identity[1] & {"owner", "admin"}:
            raise HTTPException(403, "Permesso amministrativo richiesto.")
        return identity[0]

    @app.get("/")
    def dashboard():
        return FileResponse(web / "admin.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"admin.js", "app.css", "logo.png"}:
            raise HTTPException(404)
        return FileResponse(web / name)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with engine.connect() as conn:
            conn.execute(select(1))
        return live()

    @app.post("/api/v1/license/activate")
    def activate(data: Activation, request: Request):
        host = request.client.host if request.client else "unknown"
        now = int(time.time())
        with attempts_lock:
            if len(limiter) >= 4096 and host not in limiter:
                for key in list(limiter):
                    if limiter[key][-1] <= now - 60:
                        del limiter[key]
                if len(limiter) >= 4096:
                    raise HTTPException(429, "Riprova tra un minuto.")
            attempts = limiter[host]
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 18:
                raise HTTPException(429, "Riprova tra un minuto.")
            attempts.append(now)
        try:
            public = Ed25519PublicKey.from_public_bytes(un64(data.device_key))
            public.verify(un64(data.proof), ("activate:" + data.serial).encode())
        except (ValueError, InvalidSignature) as error:
            raise HTTPException(403, "Attivazione non disponibile.") from error
        with lock, engine.begin() as conn:
            license = row(conn, select(LICENSES).where(LICENSES.c.digest == digest(data.serial)).with_for_update())
            customer = row(conn, select(CUSTOMERS).where(CUSTOMERS.c.id == license["customer_id"])) if license else None
            if not license or not customer or not active(license, customer, now):
                raise HTTPException(403, "Attivazione non disponibile.")
            installation = row(conn, select(INSTALLS).where(INSTALLS.c.license_id == license["id"],
                                                            INSTALLS.c.device_key == data.device_key).with_for_update())
            if installation and installation["status"] not in {"active", "released"}:
                raise HTTPException(403, "Attivazione non disponibile.")
            if installation is None or installation["status"] == "released":
                used = len(conn.execute(select(INSTALLS.c.id).where(
                    INSTALLS.c.license_id == license["id"], INSTALLS.c.status == "active")).all())
                if used >= license["seats"]:
                    raise HTTPException(403, "Attivazione non disponibile. Verifica i posti con l’amministratore.")
            token = secrets.token_urlsafe(32)
            if installation:
                conn.execute(INSTALLS.update().where(INSTALLS.c.id == installation["id"]).values(
                    digest=digest(token), label=data.label, status="active", last_seen=now, app_version=data.version))
                if installation["status"] == "released":
                    audit(conn, installation["id"], "activate", license["id"])
            else:
                installation = dict(id=uid(), license_id=license["id"], device_key=data.device_key, label=data.label,
                                    status="active", digest=digest(token), first_seen=now, last_seen=now,
                                    app_version=data.version)
                conn.execute(INSTALLS.insert().values(**installation))
                audit(conn, installation["id"], "activate", license["id"])
            return {"lease": lease(license, installation, now), "token": token}

    def device(conn, authorization):
        if not authorization.startswith("Bearer ") or not 40 <= len(authorization) <= 80:
            raise HTTPException(401, "Postazione non riconosciuta.")
        installation = row(conn, select(INSTALLS).where(INSTALLS.c.digest == digest(authorization[7:])).with_for_update())
        if not installation:
            raise HTTPException(401, "Postazione non riconosciuta.")
        return installation

    @app.get("/api/v1/updates/{channel}/{kind}/{name}")
    def update_asset(channel: str, kind: str, name: str, authorization: str = Header(default="")):
        if updates_root is None or channel not in CHANNELS:
            raise HTTPException(404, "Risorsa non disponibile.")
        patterns = {
            "metadata": r"(?:[1-9][0-9]{0,9}\.)?(?:root|timestamp|snapshot|targets)\.json",
            "targets": r"(?:[a-f0-9]{64}\.)?(?:installer\.exe|desktop\.zip)",
        }
        if kind not in patterns or not re.fullmatch(patterns[kind], name):
            raise HTTPException(404, "Risorsa non disponibile.")
        with engine.begin() as conn:
            installation = device(conn, authorization)
            license = row(conn, select(LICENSES).where(LICENSES.c.id == installation["license_id"]))
            customer = row(conn, select(CUSTOMERS).where(CUSTOMERS.c.id == license["customer_id"]))
            if (installation["status"] != "active" or license["channel"] != channel
                    or not active(license, customer, int(time.time()))):
                raise HTTPException(403, "Canale aggiornamenti non autorizzato.")
        root = Path(updates_root).resolve()
        asset_path = (root / channel / kind / name).resolve()
        if not asset_path.is_relative_to(root / channel / kind) or not asset_path.is_file():
            raise HTTPException(404, "Risorsa non disponibile.")
        return FileResponse(asset_path, media_type="application/octet-stream")

    @app.post("/api/v1/license/challenge")
    def challenge(authorization: str = Header(default="")):
        now = int(time.time())
        with lock, engine.begin() as conn:
            installation = device(conn, authorization)
            conn.execute(CHALLENGES.delete().where(CHALLENGES.c.installation_id == installation["id"]))
            token = secrets.token_urlsafe(32)
            conn.execute(CHALLENGES.insert().values(id=token, expires=now + 120, installation_id=installation["id"]))
            return {"challenge": token}

    def consume_proof(conn, installation, data, action, now):
        statement = CHALLENGES.delete().where(CHALLENGES.c.id == data.challenge,
            CHALLENGES.c.installation_id == installation["id"], CHALLENGES.c.expires >= now)
        public = Ed25519PublicKey.from_public_bytes(un64(installation["device_key"]))
        try:
            public.verify(un64(data.signature), (action + ":" + data.challenge).encode())
        except (InvalidSignature, ValueError) as error:
            raise HTTPException(403, "Verifica della postazione non riuscita.") from error
        if conn.execute(statement).rowcount != 1:
            raise HTTPException(403, "Challenge scaduta o già utilizzata.")

    def signed_inactive(installation, now, status, challenge):
        return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": installation["id"],
            "iat": now, "nbf": now, "exp": now + 120, "status": status, "challenge": challenge,
            "cnf": {"jkt": thumbprint(installation["device_key"])}},
            signing_key, algorithm="EdDSA", headers={"kid": "lease-1", "typ": "JWT"})

    @app.post("/api/v1/license/validate")
    def renew(data: Proof, authorization: str = Header(default="")):
        with lock, engine.begin() as conn:
            now = int(time.time())
            installation = device(conn, authorization)
            consume_proof(conn, installation, data, "renew", now)
            license = row(conn, select(LICENSES).where(LICENSES.c.id == installation["license_id"]))
            customer = row(conn, select(CUSTOMERS).where(CUSTOMERS.c.id == license["customer_id"]))
            if installation["status"] != "active" or not active(license, customer, now):
                state = "released" if installation["status"] == "released" else "inactive"
                return {"revocation": signed_inactive(installation, now, state, data.challenge)}
            conn.execute(INSTALLS.update().where(INSTALLS.c.id == installation["id"]).values(last_seen=now,
                                                                                           app_version=data.version))
            return {"lease": lease(license, installation, now)}

    @app.post("/api/v1/license/deactivate")
    def deactivate(data: Proof, authorization: str = Header(default="")):
        with lock, engine.begin() as conn:
            now = int(time.time())
            installation = device(conn, authorization)
            consume_proof(conn, installation, data, "deactivate", now)
            if installation["status"] == "active":
                conn.execute(INSTALLS.update().where(INSTALLS.c.id == installation["id"]).values(
                    status="released", last_seen=now, app_version=data.version))
                audit(conn, installation["id"], "installation.release", installation["id"])
            return {"release": signed_inactive(installation, now, "released", data.challenge)}

    @app.post("/api/v1/telemetry/events")
    def diagnostics(data: Batch, authorization: str = Header(default="")):
        now = int(time.time())
        retention = now - 30 * 86400
        with lock, engine.begin() as conn:
            installation = device(conn, authorization)
            license = row(conn, select(LICENSES).where(LICENSES.c.id == installation["license_id"]))
            customer = row(conn, select(CUSTOMERS).where(CUSTOMERS.c.id == license["customer_id"]))
            if installation["status"] != "active" or not active(license, customer, now):
                raise HTTPException(403, "Postazione inattiva.")
            conn.execute(DIAGNOSTICS.delete().where(DIAGNOSTICS.c.recorded < retention))
            for event in data.events:
                recorded = int(event.timestamp.timestamp())
                if not retention <= recorded <= now + 300:
                    raise HTTPException(422, "Data evento fuori intervallo.")
                identifier = str(event.event_id)
                previous = select(DIAGNOSTICS.c.id).where(DIAGNOSTICS.c.event_id == identifier,
                    DIAGNOSTICS.c.installation_id == installation["id"])
                if not row(conn, previous):
                    conn.execute(DIAGNOSTICS.insert().values(id=uid(), installation_id=installation["id"],
                        event_id=identifier, recorded=recorded, code=event.event_code, severity=event.severity,
                        app_version=event.app_version, page_count=event.page_count, row_count=event.row_count))
        return {"ack": [str(event.event_id) for event in data.events]}

    @app.get("/api/v1/admin/{resource}")
    def listing(resource: str, identity=Depends(admin)):
        if not identity[1] & {"owner", "admin", "support", "release-manager"}:
            raise HTTPException(403, "Ruolo non sufficiente.")
        tables = {"customers": CUSTOMERS, "licenses": LICENSES, "installations": INSTALLS,
                  "audit": EVENTS, "events": DIAGNOSTICS}
        selected = tables.get(resource)
        if selected is None:
            raise HTTPException(404)
        if "release-manager" in identity[1] and not identity[1] & {"owner", "admin", "support"}:
            raise HTTPException(403, "Ruolo non sufficiente.")
        visible = [column for column in selected.c if column.name not in {"digest", "device_key"}]
        ordering = selected.c.recorded.desc() if "recorded" in selected.c else selected.c.id
        with engine.connect() as conn:
            query = select(*visible).order_by(ordering).limit(250)
            if selected is DIAGNOSTICS:
                query = query.where(DIAGNOSTICS.c.recorded >= int(time.time()) - 30 * 86400)
            return [dict(item) for item in conn.execute(query).mappings()]

    @app.post("/api/v1/admin/customers", status_code=201)
    def create_customer(data: CustomerInput, identity=Depends(admin)):
        actor = writable(identity)
        if not data.name.strip():
            raise HTTPException(422, "La ragione sociale è obbligatoria.")
        customer = dict(id=uid(), name=data.name.strip(), status="active")
        with engine.begin() as conn:
            conn.execute(CUSTOMERS.insert().values(**customer))
            audit(conn, actor, "customer.create", customer["id"])
        return customer

    @app.patch("/api/v1/admin/customers/{identifier}")
    def change_customer(identifier: str, data: CustomerChange, identity=Depends(admin)):
        actor = writable(identity)
        with engine.begin() as conn:
            if conn.execute(CUSTOMERS.update().where(CUSTOMERS.c.id == identifier).values(status=data.status)).rowcount != 1:
                raise HTTPException(404)
            audit(conn, actor, "customer." + data.status, identifier)
        return Response(status_code=204)

    @app.post("/api/v1/admin/licenses", status_code=201)
    def create_license(data: LicenseInput, identity=Depends(admin)):
        actor = writable(identity)
        serial = "OE-" + base64.b32encode(secrets.token_bytes(20)).decode()
        with engine.begin() as conn:
            if not row(conn, select(CUSTOMERS.c.id).where(CUSTOMERS.c.id == data.customer_id)):
                raise HTTPException(404, "Cliente non trovato.")
            identifier = uid()
            conn.execute(LICENSES.insert().values(id=identifier, digest=digest(serial), prefix=serial[:11],
                                                 status="active", **data.model_dump()))
            audit(conn, actor, "license.create", identifier)
        return {"id": identifier, "serial": serial}

    @app.patch("/api/v1/admin/licenses/{identifier}")
    def change_license(identifier: str, data: LicenseChange, identity=Depends(admin)):
        actor = writable(identity)
        with lock, engine.begin() as conn:
            if conn.execute(LICENSES.update().where(LICENSES.c.id == identifier).values(**data.model_dump())).rowcount != 1:
                raise HTTPException(404)
            audit(conn, actor, "license." + data.status, identifier)
        return Response(status_code=204)

    @app.post("/api/v1/admin/installations/{identifier}/revoke")
    def revoke(identifier: str, identity=Depends(admin)):
        actor = writable(identity)
        with lock, engine.begin() as conn:
            if conn.execute(INSTALLS.update().where(INSTALLS.c.id == identifier).values(status="revoked")).rowcount != 1:
                raise HTTPException(404)
            audit(conn, actor, "installation.revoke", identifier)
        return Response(status_code=204)

    return app


def demo_key():
    return Ed25519PrivateKey.generate()
