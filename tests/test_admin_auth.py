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
