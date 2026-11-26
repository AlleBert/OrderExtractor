import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


class AuthorizationCode(BaseModel):
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proof: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(min_length=1, max_length=4096)


class AdminLogin:
    def __init__(self, origin, issuer, client_id, jwks_url, verify):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or origin != f"https://{parsed.netloc}"):
            raise ValueError("OE_ADMIN_ORIGIN deve essere l’origine HTTPS esatta della console.")
        endpoint = "/protocol/openid-connect/"
        if not jwks_url.endswith(endpoint + "certs"):
            raise ValueError("Il login della console richiede il JWKS di un realm Keycloak.")
        self.origin = origin
        self.redirect_uri = origin + "/auth/callback"
        self.authorization_url = issuer + endpoint + "auth"
        self.token_url = jwks_url.removesuffix(endpoint + "certs") + endpoint + "token"
        self.client_id = client_id
        self.verify = verify
        self.pending = {}
        self.sessions = {}
        self.lock = threading.Lock()

    def remember(self, store, key, value, expires):
        now = time.time()
        for identifier, (deadline, _) in list(store.items()):
            if deadline <= now:
                del store[identifier]
        if len(store) >= 128:
            raise HTTPException(503, "Riprova tra qualche minuto.")
        store[key] = (expires, value)

    def start(self, request):
        self.require_origin(request)
        state = secrets.token_urlsafe(32)
        proof = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.lock:
            self.remember(self.pending, state, (proof, verifier), time.time() + 300)
        query = urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri,
                           "response_type": "code", "scope": "openid", "state": state,
                           "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login"})
        return {"url": f"{self.authorization_url}?{query}", "state": state, "proof": proof}

    def exchange(self, request, data):
        self.require_origin(request)
        denied = HTTPException(401, "Accesso non riuscito. Riprova con Authenticator.")
        with self.lock:
            transaction = self.pending.get(data.state)
            if not transaction or transaction[0] <= time.time():
                raise denied
            proof, verifier = transaction[1]
            if not secrets.compare_digest(proof, data.proof):
                raise denied
            del self.pending[data.state]
        try:
            result = httpx.post(self.token_url, timeout=8, trust_env=False, data={
                "grant_type": "authorization_code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
                "code": data.code, "code_verifier": verifier,
            })
            result.raise_for_status()
            token = result.json()["access_token"]
            identity = self.verify(token)
            claims = jwt.decode(token, options={"verify_signature": False})
            if claims.get("azp") != self.client_id:
                raise denied
            duration = min(int(claims["exp"] - time.time()), 900)
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError) as error:
            raise denied from error
        if duration <= 0:
            raise denied
        session = secrets.token_urlsafe(32)
        with self.lock:
            self.remember(self.sessions, session, identity, time.time() + duration)
        return {"session": session}

    def require_origin(self, request):
        if request.headers.get("origin") != self.origin:
            raise HTTPException(403, "Origine della richiesta non autorizzata.")

    def identity(self, authorization):
        key = authorization.removeprefix("Session ")
        if key == authorization or len(key) != 43:
            return None
        with self.lock:
            session = self.sessions.get(key)
            if session and session[0] <= time.time():
                del self.sessions[key]
                session = None
        return session[1] if session else None

    def logout(self, request, authorization):
        self.require_origin(request)
        if authorization.startswith("Session "):
            with self.lock:
                self.sessions.pop(authorization.removeprefix("Session "), None)
        return Response(status_code=204)
