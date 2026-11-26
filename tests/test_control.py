import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from order_extractor.control import b64, create_control
from order_extractor.license import LicenseClient


@pytest.fixture
def central(tmp_path):
    database = os.environ.get("OE_TEST_POSTGRES", "sqlite:///" + (tmp_path / "central.sqlite").as_posix())
    key = Ed25519PrivateKey.generate()
    owner = secrets.token_urlsafe(32)
    pepper = secrets.token_bytes(32)
    app = create_control(database, key, pepper, demo_token=owner)

    def admin_auth(token):
        if token == "test-support":
            return "test-support", {"support"}
        raise ValueError

    alternate = create_control(database, key, pepper, admin_auth=admin_auth)
    yield app, alternate, key, {"Authorization": "Bearer " + owner}
    app.state.engine.dispose()
    alternate.state.engine.dispose()


def new_serial(client, admin, seats=1, valid_until=None):
    customer = client.post("/api/v1/admin/customers", headers=admin, json={"name": "Sintetico"}).json()
    return client.post("/api/v1/admin/licenses", headers=admin, json={
        "customer_id": customer["id"], "seats": seats, "valid_until": valid_until,
    }).json()


def device_request(key, serial):
    return {"serial": serial, "label": "Test", "device_key": b64(key.public_key().public_bytes_raw()),
            "version": "0.1.0", "proof": b64(key.sign(("activate:" + serial).encode()))}


def test_concurrent_seats_and_device_retry(central):
    app, _, _, admin = central
    with TestClient(app) as client:
        issued = new_serial(client, admin)
        serial = issued["serial"]
        requests = [device_request(Ed25519PrivateKey.generate(), serial) for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda data: client.post("/api/v1/license/activate", json=data), requests))
        assert sorted(answer.status_code for answer in responses) == [200] + [403] * 5
        accepted = next(i for i, answer in enumerate(responses) if answer.status_code == 200)
        retry = client.post("/api/v1/license/activate", json=requests[accepted])
        assert retry.status_code == 200
        installs = [record for record in client.get("/api/v1/admin/installations", headers=admin).json()
                    if record["license_id"] == issued["id"]]
        assert len(installs) == 1
        assert "device_key" not in installs[0] and "digest" not in installs[0]


def test_lease_offline_expiration_proof_and_revocation(central, tmp_path, monkeypatch):
    app, _, key, admin = central
    with TestClient(app) as service:
        issued = new_serial(service, admin, valid_until=int(time.time()) + 120)
        licensing = LicenseClient(tmp_path / "client", "http://127.0.0.1:1234", key.public_key(), loopback=True)

        def send(path, body, token=None):
            result = service.post("/api/v1/license" + path, json=body, headers={
                "Authorization": "Bearer " + token} if token else {})
            result.raise_for_status()
            return result.json()

        monkeypatch.setattr(licensing, "request", send)
        lease = licensing.activate(issued["serial"], "test revoca")
        assert lease["active"]
        assert lease["expires"] < time.time() + 130
        offline = LicenseClient(tmp_path / "client", "http://127.0.0.1:1234", key.public_key(), loopback=True)
        assert offline.status()["active"]
        offline.state["lease"] = offline.state["lease"][:-10] + "x" * 10
        assert offline.status()["state"] == "invalid"
        token = licensing.state["token"]
        nonce = send("/challenge", {}, token)["challenge"]
        request = {"challenge": nonce, "version": "0.1.0",
                   "signature": b64(licensing.key.sign(("renew:" + nonce).encode()))}
        assert "lease" in send("/validate", request, token)
        with pytest.raises(httpx.HTTPStatusError) as repeated:
            send("/validate", request, token)
        assert repeated.value.response.status_code == 403
        result = service.post(f"/api/v1/admin/installations/{licensing.state['installation']}/revoke", headers=admin, json={})
        assert result.status_code == 204
        assert licensing.refresh()["state"] == "revoked"
        assert LicenseClient(tmp_path / "client", "http://127.0.0.1:1234", key.public_key(), loopback=True).status()["state"] == "revoked"


def test_support_read_only_and_no_access_without_token(central):
    app, alternate, _, admin = central
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/customers").status_code == 401
        assert new_serial(client, admin)["serial"].startswith("OE-")
    with TestClient(alternate) as client:
        headers = {"Authorization": "Bearer test-support"}
        assert client.get("/api/v1/admin/customers", headers=headers).status_code == 200
        assert client.post("/api/v1/admin/customers", headers=headers, json={"name": "Impedito"}).status_code == 403


def test_diagnostics_allowlist_and_deduplication(central):
    app, _, _, admin = central
    with TestClient(app) as client:
        serial = new_serial(client, admin)["serial"]
        data = device_request(Ed25519PrivateKey.generate(), serial)
        token = client.post("/api/v1/license/activate", json=data).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        endpoint = "/api/v1/telemetry/events"
        identifier = str(uuid.uuid4())
        event = {"event_id": identifier, "timestamp": datetime.now(timezone.utc).isoformat(),
                 "event_code": "EXPORT_SUCCESS", "severity": "info", "app_version": "0.1.0",
                 "page_count": 1, "row_count": 3}
        private = {**event, "raw_text": "Contenuto che deve restare locale"}
        result = client.post(endpoint, headers=headers, json={"events": [private]})
        assert result.status_code == 422
        assert "Contenuto che deve restare locale" not in result.text
        for _ in range(2):
            assert client.post(endpoint, headers=headers, json={"events": [event]}).json() == {"ack": [identifier]}
        listing = client.get("/api/v1/admin/events", headers=admin).json()
        selected = [item for item in listing if item["event_id"] == identifier]
        assert len(selected) == 1
        assert selected[0]["page_count"] == 1


def attach_service(licensing, server, monkeypatch, *, lost_reply=False, offline_request=False):
    def send(path, body, token=None):
        nonlocal lost_reply, offline_request
        if path == "/deactivate" and offline_request:
            offline_request = False
            raise ValueError("Richiesta non consegnata.")
        result = server.post("/api/v1/license" + path, json=body,
                             headers={"Authorization": "Bearer " + token} if token else {})
        result.raise_for_status()
        if path == "/deactivate" and lost_reply:
            lost_reply = False
            raise ValueError("Risposta persa.")
        return result.json()
    monkeypatch.setattr(licensing, "request", send)


def test_release_lost_reply_and_transfer_one_seat(central, tmp_path, monkeypatch):
    app, _, key, admin = central
    endpoint = "http://127.0.0.1:1234"
    original = tmp_path / "source"
    source = LicenseClient(original, endpoint, key.public_key(), loopback=True)
    other = LicenseClient(tmp_path / "target", endpoint, key.public_key(), loopback=True)
    with TestClient(app) as server:
        issued = new_serial(server, admin)
        serial = issued["serial"]
        attach_service(source, server, monkeypatch, lost_reply=True)
        attach_service(other, server, monkeypatch)
        assert source.activate(serial, "Partenza")["active"]
        old_token = source.state["token"]
        old_headers = {"Authorization": "Bearer " + old_token}
        installation = source.state["installation"]
        with pytest.raises(httpx.HTTPStatusError) as full:
            other.activate(serial, "Arrivo")
        assert full.value.response.status_code == 403
        wrong_proof = source.prove(old_token, "renew")
        assert server.post("/api/v1/license/deactivate", headers=old_headers, json=wrong_proof).status_code == 403
        with pytest.raises(ValueError, match="Risposta persa"):
            source.deactivate()
        resumed = LicenseClient(original, endpoint, key.public_key(), loopback=True)
        attach_service(resumed, server, monkeypatch)
        assert resumed.status()["state"] == "transfer_pending"
        with pytest.raises(ValueError, match="Completa"):
            resumed.activate(serial, "Partenza")
        assert other.activate(serial, "Arrivo")["active"]
        assert resumed.deactivate()["state"] == "released"
        assert "token" not in resumed.state
        assert LicenseClient(original, endpoint, key.public_key(), loopback=True).status()["state"] == "released"
        audit = server.get("/api/v1/admin/audit", headers=admin).json()
        assert sum(event["target"] == installation and event["action"] == "installation.release" for event in audit) == 1
        with pytest.raises(httpx.HTTPStatusError) as still_full:
            resumed.activate(serial, "Partenza")
        assert still_full.value.response.status_code == 403
        assert other.deactivate()["state"] == "released"
        assert resumed.activate(serial, "Partenza")["active"]
        assert server.post("/api/v1/license/challenge", headers=old_headers, json={}).status_code == 401


@pytest.mark.parametrize("server_received", [True, False])
def test_refresh_resolves_interrupted_release(central, tmp_path, monkeypatch, server_received):
    app, _, key, admin = central
    source = LicenseClient(tmp_path / "source", "http://127.0.0.1:1234", key.public_key(), loopback=True)
    with TestClient(app) as server:
        attach_service(source, server, monkeypatch, lost_reply=server_received, offline_request=not server_received)
        assert source.activate(new_serial(server, admin)["serial"], "Prova")["active"]
        with pytest.raises(ValueError, match="persa|consegnata"):
            source.deactivate()
        assert source.status()["state"] == "transfer_pending"
        assert source.refresh()["state"] == ("released" if server_received else "active")


@pytest.mark.skipif(not os.environ.get("OE_TEST_POSTGRES"), reason="Richiede lock PostgreSQL tra due istanze")
def test_two_control_instances_never_overallocate_released_seat(central):
    app, alternate, _, admin = central
    with TestClient(app) as primary, TestClient(alternate) as secondary:
        issued = new_serial(primary, admin)
        serial = issued["serial"]
        key = Ed25519PrivateKey.generate()
        initial = primary.post("/api/v1/license/activate", json=device_request(key, serial)).json()
        headers = {"Authorization": "Bearer " + initial["token"]}
        nonce = primary.post("/api/v1/license/challenge", headers=headers, json={}).json()["challenge"]
        signed = {"challenge": nonce, "version": "0.1.0",
                  "signature": b64(key.sign(("deactivate:" + nonce).encode()))}
        requests = [device_request(Ed25519PrivateKey.generate(), serial) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            release = pool.submit(primary.post, "/api/v1/license/deactivate", headers=headers, json=signed)
            tries = [pool.submit(service.post, "/api/v1/license/activate", json=request)
                     for service, request in zip((primary, secondary), requests, strict=True)]
            assert release.result().status_code == 200
            statuses = [result.result().status_code for result in tries]
        assert all(code in {200, 403} for code in statuses) and statuses.count(200) <= 1
        if statuses.count(200) == 0:
            assert secondary.post("/api/v1/license/activate", json=requests[0]).status_code == 200
        installations = primary.get("/api/v1/admin/installations", headers=admin).json()
        assert sum(item["license_id"] == issued["id"] and item["status"] == "active" for item in installations) == 1
