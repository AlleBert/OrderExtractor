from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from order_extractor.launcher import admin_jwks_url, admin_verifier

ISSUER = "https://auth.example.test"
AUDIENCE = "order-extractor-test"
OWNER = "synthetic-owner"


@pytest.mark.parametrize(("subject", "roles", "amr", "allowed"), [
    (OWNER, ["owner"], ["mfa"], True),
    ("another-account", ["owner"], ["mfa"], False),
    (OWNER, ["admin"], ["mfa"], False),
    (OWNER, ["owner"], ["pwd"], False),
])
def test_private_console_requires_the_configured_owner(monkeypatch, subject, roles, amr, allowed):
    for name, value in {"OE_OIDC_ISSUER": ISSUER, "OE_OIDC_AUDIENCE": AUDIENCE,
                        "OE_OIDC_JWKS": ISSUER + "/jwks", "OE_ADMIN_SUBJECT": OWNER}.items():
        monkeypatch.setenv(name, value)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda *_: SimpleNamespace(key=private.public_key()))
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": subject, "amr": amr, "roles": roles,
                        "iat": 1, "exp": 2_147_483_647}, private, algorithm="RS256")
    verify = admin_verifier()
    if allowed:
        assert verify(token) == (OWNER, {"owner"})
    else:
        with pytest.raises(ValueError):
            verify(token)


@pytest.mark.parametrize(("docker_jwks", "expected_host"), [
    ("0", "https://auth.example.test:10444"),
    ("1", "http://order-extractor-keycloak:8080"),
])
def test_admin_jwks_uses_the_configured_transport(monkeypatch, docker_jwks, expected_host):
    path = "/realms/test/protocol/openid-connect/certs"
    monkeypatch.setenv("OE_OIDC_ISSUER", "https://auth.example.test:10444/realms/test")
    monkeypatch.setenv("OE_OIDC_JWKS", "https://auth.example.test:10444" + path)
    monkeypatch.setenv("OE_OIDC_DOCKER_JWKS", docker_jwks)
    assert admin_jwks_url() == expected_host + path


@pytest.mark.parametrize("jwks_url", [
    "https://auth.example.test/realms/other/protocol/openid-connect/certs",
    "https://another.example.test/realms/test/protocol/openid-connect/certs",
    "http://auth.example.test/realms/test/protocol/openid-connect/certs",
])
def test_docker_jwks_requires_the_canonical_keycloak_endpoint(monkeypatch, jwks_url):
    monkeypatch.setenv("OE_OIDC_ISSUER", "https://auth.example.test/realms/test")
    monkeypatch.setenv("OE_OIDC_JWKS", jwks_url)
    monkeypatch.setenv("OE_OIDC_DOCKER_JWKS", "1")
    with pytest.raises(ValueError):
        admin_jwks_url()


def test_browser_admin_login_binds_pkce_to_tab_and_revokes_session(monkeypatch, tmp_path):
    import base64
    import hashlib
    import time
    from urllib.parse import parse_qs, urlsplit

    import httpx
    from fastapi.testclient import TestClient

    from order_extractor.admin_login import AdminLogin
    from order_extractor.control import create_control, demo_key

    origin = "https://console.example.test:10443"
    issuer = ISSUER + "/realms/test"
    owner_headers = {"Origin": origin}
    env = {"OE_OIDC_ISSUER": issuer, "OE_OIDC_AUDIENCE": AUDIENCE,
           "OE_OIDC_JWKS": issuer + "/protocol/openid-connect/certs", "OE_ADMIN_SUBJECT": OWNER}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda *_: SimpleNamespace(key=private.public_key()))
    verify = admin_verifier()
    login = AdminLogin(origin, issuer, AUDIENCE, "http://keycloak:8080/realms/test/protocol/openid-connect/certs", verify)
    app = create_control(f"sqlite:///{tmp_path / 'browser.sqlite'}", demo_key(), b"x" * 32,
                         admin_auth=verify, admin_login=login)
    claims = {"iss": issuer, "aud": AUDIENCE, "azp": AUDIENCE, "sub": OWNER,
              "amr": ["mfa"], "roles": ["owner"], "iat": int(time.time()), "exp": int(time.time()) + 300}
    exchanged = []

    def exchange(url, *, data, **_):
        assert url == "http://keycloak:8080/realms/test/protocol/openid-connect/token"
        assert data["redirect_uri"] == origin + "/auth/callback"
        exchanged.append(data)
        return httpx.Response(200, json={"access_token": jwt.encode(claims, private, algorithm="RS256")},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", exchange)
    with TestClient(app, base_url=origin) as browser:
        assert browser.get("/auth/status").json() == {"oidc": True}
        assert browser.get("/auth/callback?code=example").status_code == 200
        assert browser.get("/api/v1/admin/customers").status_code == 401
        assert browser.post("/auth/login", json={}).status_code == 403
        start = browser.post("/auth/login", headers=owner_headers, json={})
        assert "set-cookie" not in start.headers
        flow = start.json()
        authorization = urlsplit(flow["url"])
        assert authorization.path == "/realms/test/protocol/openid-connect/auth"
        query = parse_qs(authorization.query)
        assert query["prompt"] == ["login"] and query["code_challenge_method"] == ["S256"]
        assert query["state"] == [flow["state"]] and flow["proof"] not in flow["url"]
        payload = {"state": flow["state"], "proof": flow["proof"], "code": "synthetic"}
        assert browser.post("/auth/exchange", json=payload).status_code == 403
        assert browser.post("/auth/exchange", headers=owner_headers, json={**payload, "proof": "x" * 43}).status_code == 401
        assert not exchanged
        finish = browser.post("/auth/exchange", headers=owner_headers, json=payload)
        assert finish.status_code == 200 and "set-cookie" not in finish.headers
        verifier = exchanged[0]["code_verifier"]
        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert query["code_challenge"] == [expected_challenge]
        assert browser.post("/auth/exchange", headers=owner_headers, json=payload).status_code == 401
        headers = {**owner_headers, "Authorization": "Session " + finish.json()["session"]}
        assert browser.get("/api/v1/admin/customers", headers=headers).status_code == 200
        assert browser.post("/api/v1/admin/customers", headers=headers, json={"name": "Sintetico"}).status_code == 201
        assert browser.post("/auth/logout", headers=headers, json={}).status_code == 204
        assert browser.get("/api/v1/admin/customers", headers=headers).status_code == 401

        for rejected in ({"amr": ["pwd"]}, {"sub": "different-user"}, {"azp": "different-client"}):
            saved = {claim: claims[claim] for claim in rejected}
            claims.update(rejected)
            flow = browser.post("/auth/login", headers=owner_headers, json={}).json()
            payload.update(state=flow["state"], proof=flow["proof"])
            assert browser.post("/auth/exchange", headers=owner_headers, json=payload).status_code == 401
            claims.update(saved)
