import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import VERSION
from .control import AUDIENCE, ISSUER, b64, thumbprint, un64


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def protect(data, encrypt):
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    destination = Blob()
    action = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    action.restype = wintypes.BOOL
    if not action(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(destination.data, ctypes.c_void_p))


class LicenseClient:
    def __init__(self, base, url, public_key, *, loopback=False):
        address = urlparse(url)
        local = loopback and address.scheme == "http" and address.hostname == "127.0.0.1"
        if (address.scheme != "https" and not local) or not address.hostname or address.username or address.query or address.fragment:
            raise ValueError("Il control plane richiede un URL HTTPS.")
        self.path = base / "license.bin"
        self.url = url.rstrip("/") + "/api/v1"
        self.public_key = public_key
        self.lock = threading.RLock()
        self.read_error = False
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.clock_mono = time.monotonic()
        try:
            self.state = json.loads(protect(self.path.read_bytes(), False)) if self.path.is_file() else {}
            self.key = Ed25519PrivateKey.from_private_bytes(un64(self.state["key"])) if self.state else Ed25519PrivateKey.generate()
        except (OSError, ValueError, KeyError, TypeError):
            self.state = {}
            self.key = Ed25519PrivateKey.generate()
            self.read_error = True
        self.anchor = 0
        self.jkt = thumbprint(b64(self.key.public_key().public_bytes_raw()))

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        self.state["key"] = b64(self.key.private_bytes_raw())
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(protect(json.dumps(self.state).encode(), True))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        self.read_error = False

    def claims(self, token, expected=None, *, validate_time=True):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "lease-1" or header.get("typ") != "JWT":
            raise jwt.InvalidTokenError("Chiave sconosciuta.")
        claims = jwt.decode(token, self.public_key, algorithms=["EdDSA"], issuer=ISSUER, audience=AUDIENCE,
                            leeway=30, options={"require": ["iss", "aud", "exp", "iat", "nbf", "sub", "cnf"],
                                               "verify_exp": validate_time, "verify_nbf": validate_time,
                                               "verify_iat": validate_time})
        if claims["cnf"] != {"jkt": self.jkt} or expected is not None and claims["sub"] != expected:
            raise jwt.InvalidTokenError("Licenza assegnata a un’altra postazione.")
        return claims

    def status(self):
        with self.lock:
            token = self.state.get("lease")
            if token is None:
                if self.state.get("released"):
                    return {"active": False, "state": "released",
                            "message": "Postazione disattivata. Usa il seriale sulla postazione di destinazione."}
                return {"active": False, "state": "unactivated", "message":
                        "Licenza locale illeggibile. Contatta l’amministratore." if self.read_error else "Inserisci il seriale della licenza."}
            try:
                claims = self.claims(token, validate_time=False)
                now = int(time.time())
                floor = max(claims["iat"], self.anchor + time.monotonic() - self.clock_mono if self.anchor else 0)
                if self.state.get("transfer_pending"):
                    state, message = "transfer_pending", "Disattivazione da confermare. Verifica online o ripeti la disattivazione."
                elif self.state.get("revoked"):
                    state, message = "revoked", "Licenza sospesa, scaduta o revocata dal server."
                elif now + 120 < floor:
                    state, message = "clock", "Verifica l’orologio e rinnova la licenza online."
                elif max(floor - 5, now) >= claims["exp"]:
                    state, message = "expired", "Validità offline scaduta. Verifica la licenza online."
                elif claims.get("policy_version") != 1 or "extract" not in claims.get("features", []):
                    state, message = "invalid", "Questa licenza non consente l’elaborazione."
                else:
                    state, message = "active", "Licenza valida"
                return {"active": state == "active", "state": state, "message": message,
                        "expires": claims["exp"], "verified": claims["iat"], "channel": claims.get("channel")}
            except (jwt.PyJWTError, KeyError, ValueError, TypeError):
                return {"active": False, "state": "invalid", "message": "La firma della licenza non è valida."}

    def request(self, endpoint, body, token=None, *, scope="/license"):
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            with httpx.Client(timeout=7, follow_redirects=False, trust_env=not self.url.startswith("http://")) as client:
                response = client.post(self.url + scope + endpoint, json=body, headers=headers)
            if response.status_code == 429:
                raise ValueError("Troppi tentativi. Riprova tra un minuto.")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise ValueError("Richiesta rifiutata dal servizio licenze.") from error
        except (httpx.RequestError, TypeError) as error:
            raise ValueError("Servizio licenze non raggiungibile. Riprova quando sei online.") from error

    def accept(self, lease, expected=None):
        try:
            claims = self.claims(lease, expected)
            if (claims.get("policy_version") != 1 or claims["exp"] - claims["iat"] > 7 * 86400
                or claims["exp"] <= claims["iat"] or claims.get("installation_id") != claims["sub"]
                or not claims.get("license_id") or not claims.get("customer_id")
                or claims.get("channel") not in {"dev", "beta", "stable"}
                or (claims.get("entitlement_end") is not None and claims["exp"] > claims["entitlement_end"])):
                raise jwt.InvalidTokenError("Lease incoerente.")
        except (jwt.PyJWTError, TypeError, KeyError) as error:
            raise ValueError("La risposta del servizio licenze non è valida.") from error
        self.anchor = claims["iat"]
        self.clock_mono = time.monotonic()
        self.state.update(lease=lease, revoked=False, installation=claims["sub"])
        self.state.pop("transfer_pending", None)
        self.state.pop("released", None)

    def activate(self, serial, label):
        with self.lock:
            if self.state.get("transfer_pending"):
                raise ValueError("Completa la disattivazione in sospeso prima di attivare una licenza.")
            data = self.request("/activate", {"serial": serial,
                "label": label, "device_key": b64(self.key.public_key().public_bytes_raw()),
                "version": VERSION, "proof": b64(self.key.sign(("activate:" + serial).encode()))})
            self.accept(data["lease"])
            self.state["token"] = data["token"]
            self.save()
            return self.status()

    def prove(self, token, action):
        nonce = self.request("/challenge", {}, token).get("challenge")
        if not isinstance(nonce, str) or len(nonce) != 43:
            raise ValueError("Challenge della postazione non valida.")
        signature = self.key.sign((action + ":" + nonce).encode())
        return {"challenge": nonce, "version": VERSION, "signature": b64(signature)}

    def accept_inactive(self, receipt, nonce, *, release_only=False):
        try:
            claims = self.claims(receipt, self.state["installation"])
            status = claims.get("status")
            if status == "released" and claims.get("challenge") == nonce:
                self.state = {"released": True}
            elif status == "inactive" and not release_only and claims.get("challenge", nonce) == nonce:
                self.state["revoked"] = True
                self.state.pop("transfer_pending", None)
            else:
                raise jwt.InvalidTokenError("Stato non valido.")
        except (jwt.PyJWTError, TypeError, KeyError) as error:
            raise ValueError("Conferma firmata della postazione non valida.") from error

    def refresh(self):
        with self.lock:
            token = self.state.get("token")
            if not token:
                raise ValueError("Attiva prima una licenza.")
            proof = self.prove(token, "renew")
            data = self.request("/validate", proof, token)
            if data.get("revocation"):
                self.accept_inactive(data["revocation"], proof["challenge"])
            else:
                self.accept(data["lease"], self.state["installation"])
            self.save()
            return self.status()

    def deactivate(self):
        with self.lock:
            token = self.state.get("token")
            if not token:
                if self.state.get("released"):
                    return self.status()
                raise ValueError("Questa postazione non è attivata.")
            proof = self.prove(token, "deactivate")
            self.state["transfer_pending"] = True
            self.save()
            data = self.request("/deactivate", proof, token)
            self.accept_inactive(data.get("release"), proof["challenge"], release_only=True)
            self.save()
            return self.status()

    def send_events(self, events):
        with self.lock:
            token = self.state.get("token")
            if not token:
                return []
            return self.request("/events", {"events": events}, token, scope="/telemetry")["ack"]

    def update_access(self):
        with self.lock:
            status = self.status()
            if not status["active"] or not self.state.get("token"):
                raise ValueError("Serve una licenza attiva per verificare gli aggiornamenti.")
            return status["channel"], self.state["token"]
