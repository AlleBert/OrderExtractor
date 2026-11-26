import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field
from tuf.api import exceptions
from tuf.ngclient import Updater
from tuf.ngclient.fetcher import FetcherInterface

from . import VERSION

CHANNELS = {"stable", "beta", "dev"}
MAX_INSTALLER = 512 * 1024 * 1024
RELEASE = r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})"
PACKAGE_NAMES = {"installer.exe", "desktop.zip"}


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(pattern=f"^{RELEASE}$")
    channel: str
    product: str
    platform: str
    notes: str = Field(max_length=2000)


class UpdateError(ValueError):
    pass


class SameOriginFetcher(FetcherInterface):
    def __init__(self, base, credential, http):
        self.prefix = base + "/"
        self.http = http
        self.credential = credential
        self.deadline = time.monotonic() + 180

    def _fetch(self, url):
        if not url.startswith(self.prefix):
            raise exceptions.DownloadError("Destinazione aggiornamento non autorizzata.")
        with self.http.stream("GET", url, headers={
            "Authorization": "Bearer " + self.credential, "Accept-Encoding": "identity",
        }) as response:
            if response.status_code != 200:
                raise exceptions.DownloadHTTPError("Risorsa aggiornamento non disponibile.", response.status_code)
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise exceptions.DownloadError("Codifica download non supportata.")
            for chunk in response.iter_raw():
                if time.monotonic() > self.deadline:
                    raise exceptions.DownloadError("Tempo massimo download superato.")
                yield chunk


class Updates:
    def __init__(self, cache, base, root):
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment):
            raise ValueError("Il repository aggiornamenti richiede HTTPS.")
        self.base = base.rstrip("/")
        self.root = root
        self.cache = Path(cache)

    def get(self, channel, credential, *, path=None, expected=None, package="installer.exe", transport=None):
        if channel not in CHANNELS or not credential:
            raise UpdateError("Serve una licenza attiva per questa operazione.")
        if package not in PACKAGE_NAMES:
            raise UpdateError("Tipo di pacchetto non supportato.")
        self.cache.mkdir(mode=0o700, exist_ok=True, parents=True)
        folder = self.cache / channel
        repository = f"{self.base}/{channel}"
        try:
            with FileLock(str(self.cache / f"{channel}.lock"), timeout=2):
                folder.mkdir(mode=0o700, exist_ok=True)
                with httpx.Client(transport=transport, follow_redirects=False, timeout=15) as http:
                    updater = Updater(str(folder), repository + "/metadata/",
                        target_base_url=repository + "/targets/",
                        bootstrap=self.root, fetcher=SameOriginFetcher(repository, credential, http))
                    target = updater.get_targetinfo(package)
                    if target is None:
                        if path is not None:
                            raise UpdateError("La versione selezionata non è più disponibile.")
                        return None
                    release = Release.model_validate(target.custom)
                    if (release.product != "OrderExtractor" or release.platform != "windows-x64"
                            or release.channel != channel or not 0 < target.length <= MAX_INSTALLER
                            or not re.fullmatch(r"[a-f0-9]{64}", target.hashes.get("sha256", ""))):
                        raise UpdateError("Il pacchetto firmato è incompatibile.")
                    if tuple(map(int, release.version.split("."))) <= tuple(map(int, VERSION.split("."))):
                        if path is not None:
                            raise UpdateError("Il pacchetto non è più recente della versione installata.")
                        return None
                    if path is not None:
                        if release.version != expected:
                            raise UpdateError("La versione è cambiata. Controlla gli aggiornamenti di nuovo.")
                        scratch = path.parent
                        # TUF verifies a temporary copy before writing the caller's file.
                        if shutil.disk_usage(scratch).free < target.length * 2 + 16 * 1024 * 1024:
                            raise UpdateError("Spazio insufficiente per verificare il pacchetto.")
                        updater.download_target(target, str(path))
                    return {**release.model_dump(), "size": target.length, "sha256": target.hashes["sha256"]}
        except UpdateError:
            raise
        except Timeout as error:
            raise UpdateError("È già in corso una verifica degli aggiornamenti.") from error
        except (exceptions.RepositoryError, exceptions.DownloadError, ValueError, OSError) as error:
            raise UpdateError("Impossibile verificare l’aggiornamento firmato.") from error
