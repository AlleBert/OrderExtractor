import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import (
    Metadata,
    MetaFile,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)

from order_extractor.control import create_control, demo_key
from order_extractor.local import create_local
from order_extractor.updates import UpdateError, Updates
from tests.test_control import device_request, new_serial

BASE = "https://updates.example/api/v1/updates"
ARTIFACT = b"synthetic update payload"
ROLE_NAMES = ("root", "timestamp", "snapshot", "targets")
ORIGIN = "http://127.0.0.1:8105"


def signed_role(role, signer):
    metadata = Metadata(role)
    metadata.sign(signer)
    return metadata.to_bytes()


@pytest.fixture
def repository():
    keys = {role: CryptoSigner.generate_ed25519() for role in ROLE_NAMES}
    now = datetime.now(timezone.utc)
    root = Root(expires=now + timedelta(days=90))
    for role, signer in keys.items():
        root.add_key(signer.public_key, role)
    trusted = signed_role(root, keys["root"])

    def metadata(version=1, *, expired=False, release="0.2.0", channel="stable", wrong_key=False,
                 asset="installer.exe"):
        until = now + timedelta(days=5)
        target = TargetFile.from_data(asset, ARTIFACT, ["sha256"])
        target.unrecognized_fields["custom"] = {
            "version": release, "channel": channel, "product": "OrderExtractor",
            "platform": "windows-x64", "notes": "Aggiornamento sintetico",
        }
        targets = signed_role(Targets(version=version, expires=until, targets={asset: target}),
                              CryptoSigner.generate_ed25519() if wrong_key else keys["targets"])
        snapshot = signed_role(Snapshot(version=version, expires=until, meta={
            "targets.json": MetaFile.from_data(version, targets, ["sha256"]),
        }), keys["snapshot"])
        timestamp = signed_role(Timestamp(version=version,
            expires=now - timedelta(days=1) if expired else until,
            snapshot_meta=MetaFile.from_data(version, snapshot, ["sha256"])), keys["timestamp"])
        return {"metadata/timestamp.json": timestamp, f"metadata/{version}.snapshot.json": snapshot,
                f"metadata/{version}.targets.json": targets,
                f"targets/{target.hashes['sha256']}.{asset}": ARTIFACT}
    return trusted, metadata


def transport(data, *, redirect=False):
    def respond(request):
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        assert str(request.url).startswith(BASE + "/stable/")
        name = request.url.path.split("/stable/", 1)[-1]
        if redirect:
            return httpx.Response(307, headers={"Location": "https://external.example/"})
        if name in data:
            return httpx.Response(200, stream=httpx.ByteStream(data[name]))
        return httpx.Response(404)
    return httpx.MockTransport(respond)


def test_signed_release_download_and_rollback_detection(tmp_path, repository):
    root, build = repository
    engine = Updates(tmp_path / "cache", BASE, root)
    signed = transport(build(version=2))
    assert engine.get("stable", "synthetic-token", transport=signed)["version"] == "0.2.0"
    package = tmp_path / "package.exe"
    engine.get("stable", "synthetic-token", path=package, expected="0.2.0", transport=signed)
    assert package.read_bytes() == ARTIFACT
    with pytest.raises(UpdateError, match="firmato"):
        engine.get("stable", "synthetic-token", transport=transport(build(version=1)))


@pytest.mark.parametrize("variant", ["expired", "wrong_key", "channel", "redirect", "tampered"])
def test_reject_unsafe_or_invalid_update(tmp_path, repository, variant):
    root, build = repository
    engine = Updates(tmp_path / "cache", BASE, root)
    data = build(expired=variant == "expired", wrong_key=variant == "wrong_key",
                 channel="beta" if variant == "channel" else "stable")
    if variant == "tampered":
        package = next(name for name in data if name.startswith("targets/"))
        data[package] = b"X" * len(ARTIFACT)
    file = tmp_path / "download.exe"
    with pytest.raises(UpdateError):
        engine.get("stable", "synthetic-token", path=file, expected="0.2.0",
                   transport=transport(data, redirect=variant == "redirect"))
    assert not file.exists()


def test_no_older_or_unconfirmed_version(tmp_path, repository):
    root, build = repository
    engine = Updates(tmp_path / "cache", BASE, root)
    assert engine.get("stable", "synthetic-token", transport=transport(build(release="0.1.0"))) is None
    with pytest.raises(UpdateError, match="versione è cambiata"):
        engine.get("stable", "synthetic-token", path=tmp_path / "installer.exe", expected="0.3.0",
                   transport=transport(build(version=2)))


def test_control_limits_channel_to_active_installation(tmp_path, repository):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _, build = repository
    files = build()
    root = tmp_path / "repo"
    for channel in ("stable", "beta"):
        for relative, contents in files.items():
            file = root / channel / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(contents)
    admin_token = secrets.token_urlsafe(32)
    central = create_control("sqlite:///" + str(tmp_path / "central.db"), demo_key(), secrets.token_bytes(32),
                             demo_token=admin_token, updates_root=root)
    admin = {"Authorization": f"Bearer {admin_token}"}
    with TestClient(central) as client:
        serial = new_serial(client, admin)["serial"]
        device = client.post("/api/v1/license/activate", json=device_request(Ed25519PrivateKey.generate(), serial)).json()
        headers = {"Authorization": "Bearer " + device["token"]}
        path = "/api/v1/updates/stable/metadata/timestamp.json"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).content == files["metadata/timestamp.json"]
        assert client.get(path.replace("stable", "beta"), headers=headers).status_code == 403
        assert client.get(path.replace("timestamp.json", "unrelated.pem"), headers=headers).status_code == 404
        install = client.get("/api/v1/admin/installations", headers=admin).json()[0]["id"]
        client.post(f"/api/v1/admin/installations/{install}/revoke", json={}, headers=admin).raise_for_status()
        assert client.get(path, headers=headers).status_code == 403


def test_local_confirmation_and_one_time_download(tmp_path, repository):
    root, build = repository
    updates = Updates(tmp_path / "cache", BASE, root)

    class TestUpdates:
        def get(self, *args, **kwargs):
            return updates.get(*args, transport=transport(build()), **kwargs)

    class License:
        def update_access(self):
            return "stable", "synthetic-token"

    app = create_local(tmp_path / "local", ORIGIN, "bootstrap", License(), updates=TestUpdates())
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/open?ticket=bootstrap", follow_redirects=False).status_code == 303
        assert client.post("/api/update/check", json={}).status_code == 403
        headers = {"Origin": ORIGIN}
        result = client.post("/api/update/check", json={}, headers=headers)
        assert result.json()["release"]["version"] == "0.2.0"
        result = client.post("/api/update/prepare", json={"version": "0.2.0"}, headers=headers)
        download = result.json()["download"]
        assert client.get(download).content == ARTIFACT
        assert client.get(download).status_code == 404


def test_installed_client_schedules_only_selected_signed_release(tmp_path, repository):
    import json

    from order_extractor.supervisor import REQUEST

    root, build = repository
    updates = Updates(tmp_path / "cache", BASE, root)

    class TestUpdates:
        def get(self, *args, **kwargs):
            return updates.get(*args, transport=transport(build(asset="desktop.zip")), **kwargs)

    class License:
        def update_access(self):
            return "stable", "synthetic-token"

    installation = tmp_path / "installed"
    installation.mkdir()
    app = create_local(tmp_path / "local", ORIGIN, "bootstrap", License(), updates=TestUpdates(),
                       installation=installation)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/open?ticket=bootstrap", follow_redirects=False).status_code == 303
        headers = {"Origin": ORIGIN}
        release = client.post("/api/update/check", json={}, headers=headers).json()["release"]
        assert release["version"] == "0.2.0"
        assert client.post("/api/update/schedule", json={"version": "0.3.0"}, headers=headers).status_code == 409
        assert not (installation / REQUEST).exists()
        accepted = client.post("/api/update/schedule", json={"version": "0.2.0"}, headers=headers)
        assert accepted.json() == {"scheduled": True}
        assert json.loads((installation / REQUEST).read_bytes())["version"] == "0.2.0"
