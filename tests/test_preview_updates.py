import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from order_extractor.preview_updates import PROVENANCE, REPOSITORY, PreviewUpdates
from order_extractor.version_store import SwitchError


def release(version, *, draft=False, prerelease=True):
    return {"tag_name": f"v{version}-preview", "draft": draft, "prerelease": prerelease, "assets": [
        {"name": f"desktop-{version}-x64.zip", "state": "uploaded", "size": 7},
        {"name": PROVENANCE, "state": "uploaded", "size": 6},
    ]}


def test_picks_newest_complete_preview(tmp_path):
    releases = [release("0.1.2"), release("0.1.9"), release("0.1.10"), release("0.2.0", draft=True),
                release("1.0.0", prerelease=False), {**release("2.0.0"), "assets": []}]
    updater = PreviewUpdates("0.1.3", tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=releases)))
    assert updater.get()["version"] == "0.1.10"


@pytest.mark.parametrize("attested_name", ["desktop-0.1.4-x64.zip", "unrelated.zip"])
def test_verified_download_binds_digest_and_release(tmp_path, monkeypatch, attested_name):
    package = b"desktop"
    digest = hashlib.sha256(package).hexdigest()

    def remote(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=[release("0.1.4")])
        if request.url.host == "github.com" and request.url.path.endswith(".zip"):
            return httpx.Response(302, headers={"Location": "https://release-assets.githubusercontent.com/blob"})
        if request.url.host == "release-assets.githubusercontent.com":
            return httpx.Response(200, content=package)
        return httpx.Response(200, content=b"bundle")

    def verifier(args, **options):
        assert args[args.index("--repo") + 1] == REPOSITORY
        assert args[args.index("--source-ref") + 1] == "refs/tags/v0.1.4-preview"
        assert args[args.index("--signer-workflow") + 1] == f"{REPOSITORY}/.github/workflows/tests.yml"
        assert "--deny-self-hosted-runners" in args
        assert "GH_TOKEN" not in options["env"]
        assert tmp_path.joinpath(PROVENANCE).read_bytes() == b"bundle"
        subject = {"name": attested_name, "digest": {"sha256": digest}}
        response = [{"verificationResult": {"statement": {"subject": [subject]}}}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(response))

    monkeypatch.setattr("order_extractor.preview_updates.subprocess.run", verifier)
    updater = PreviewUpdates("0.1.3", tmp_path, transport=httpx.MockTransport(remote))
    target = tmp_path / "desktop.zip"
    if attested_name == "unrelated.zip":
        with pytest.raises(SwitchError, match="non identifica"):
            updater.get(path=target, expected="0.1.4")
    else:
        assert updater.get(path=target, expected="0.1.4") == digest
        assert target.read_bytes() == package


def test_rejects_redirect_outside_github(tmp_path):
    def remote(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=[release("0.1.4")])
        assert request.url.host == "github.com"
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/file"})

    updater = PreviewUpdates("0.1.3", tmp_path, transport=httpx.MockTransport(remote))
    with pytest.raises(SwitchError, match="Indirizzo"):
        updater.get(path=tmp_path / "desktop.zip", expected="0.1.4")
