"""Find and verify desktop updates from the public GitHub Preview channel."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from .updates import MAX_INSTALLER
from .version_store import SwitchError, release_key

REPOSITORY = "AlleBert/OrderExtractor"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases"
TAG = re.compile(r"v([0-9.]{5,20})-preview")
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_METADATA = 2 * 1024 * 1024
PROVENANCE = "desktop-provenance.sigstore.jsonl"


def verifier_path(installation):
    return Path(installation) / "_internal" / "github-cli" / "gh.exe"


def read_asset(client, url, limit, consume, *, hosts):
    deadline = time.monotonic() + 180
    for _ in range(5):
        if url.scheme != "https" or url.host not in hosts or url.userinfo or url.port is not None:
            raise SwitchError("Indirizzo di aggiornamento non consentito.")
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.is_redirect:
                url = response.url.join(response.headers["location"])
                continue
            response.raise_for_status()
            size = 0
            for chunk in response.iter_bytes(128 * 1024):
                size += len(chunk)
                if size > limit or time.monotonic() > deadline:
                    raise SwitchError("Download di aggiornamento oltre i limiti.")
                consume(chunk)
            if not size:
                raise SwitchError("Download di aggiornamento vuoto.")
            return size
    raise SwitchError("Troppi reindirizzamenti per l’aggiornamento.")


def release_from(item, current):
    if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not True:
        return None
    tag = item.get("tag_name")
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    if not match:
        return None
    version = match[1]
    try:
        if release_key(version) <= release_key(current):
            return None
    except SwitchError:
        return None
    names = [f"desktop-{version}-x64.zip", PROVENANCE]
    sizes = {}
    assets = item.get("assets")
    for asset in assets if isinstance(assets, list) else ():
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if name not in names:
            continue
        size = asset.get("size")
        limit = MAX_INSTALLER if name == names[0] else MAX_METADATA
        if name in sizes or asset.get("state") != "uploaded" or type(size) is not int or not 0 < size <= limit:
            return None
        sizes[name] = size
    if len(sizes) != 2:
        return None
    return {"version": version, "channel": "GitHub Preview", "size": sizes[names[0]],
            "notes": "", "tag": tag, "files": sizes}


class PreviewUpdates:
    def __init__(self, current, installation, *, transport=None):
        self.current = current
        self.verifier = verifier_path(installation)
        self.transport = transport

    def get(self, *, path=None, expected=None):
        try:
            with httpx.Client(timeout=15, transport=self.transport) as client:
                listing = bytearray()
                url = httpx.URL(RELEASES, params={"per_page": 30})
                read_asset(client, url, MAX_METADATA, listing.extend, hosts={"api.github.com"})
                items = json.loads(listing)
                if not isinstance(items, list):
                    raise SwitchError("Elenco delle anteprime non valido.")
                releases = [release for item in items if (release := release_from(item, self.current))]
                if expected is None:
                    return max(releases, key=lambda release: release_key(release["version"]), default=None)
                release = next((item for item in releases if item["version"] == expected), None)
                if release is None or path is None:
                    raise SwitchError("Anteprima richiesta non più disponibile.")
                return self._download(client, release, Path(path))
        except (httpx.HTTPError, KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError) as error:
            raise SwitchError("Impossibile scaricare o verificare l’anteprima GitHub.") from error

    def _download(self, client, release, directory_file):
        checksum = hashlib.sha256()
        for name, length in release["files"].items():
            archive = name.endswith(".zip")
            destination = directory_file if archive else directory_file.with_name(PROVENANCE)
            url = httpx.URL(f"https://github.com/{REPOSITORY}/releases/download/{release['tag']}/{name}")
            with destination.open("xb") as output:
                def save(chunk):
                    output.write(chunk)
                    if archive:
                        checksum.update(chunk)

                if read_asset(client, url, length, save, hosts=ASSET_HOSTS) != length:
                    raise SwitchError("Dimensione del pacchetto di aggiornamento non valida.")
        digest = checksum.hexdigest()
        self._verify(directory_file, release["tag"], f"desktop-{release['version']}-x64.zip", digest)
        return digest

    def _verify(self, path, tag, filename, digest):
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
                       if key in os.environ}
        environment.update(LOCALAPPDATA=str(path.parent), USERPROFILE=str(path.parent),
                           GH_CONFIG_DIR=str(path.parent / "gh-config"),
                           XDG_CACHE_HOME=str(path.parent / "gh-cache"))
        result = subprocess.run(
            [self.verifier, "attestation", "verify", path, "--bundle", path.with_name(PROVENANCE),
             "--repo", REPOSITORY, "--signer-workflow", f"{REPOSITORY}/.github/workflows/tests.yml",
             "--source-ref", f"refs/tags/{tag}", "--deny-self-hosted-runners", "--format", "json"],
            cwd=path.parent, env=environment, capture_output=True, check=False, timeout=60,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if result.returncode != 0:
            raise SwitchError("Attestazione GitHub non valida.")
        verified = json.loads(result.stdout)
        if not any(subject == {"name": filename, "digest": {"sha256": digest}}
                   for entry in verified for subject in entry["verificationResult"]["statement"]["subject"]):
            raise SwitchError("L’attestazione non identifica il pacchetto desktop atteso.")
