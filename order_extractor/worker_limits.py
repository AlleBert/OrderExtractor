"""Contain a PDF worker and dispose of its subprocesses with the job."""

import errno
import multiprocessing
import os
import signal
import sys
from pathlib import Path

MEMORY = 1536 * 1024**2
CPU_SECONDS = 600
START_TIMEOUT = 8
ENV_NAMES = {
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "LANG", "LC_ALL", "LC_CTYPE",
    "OE_TESSERACT", "OE_OCR_LANGS", "TESSDATA_PREFIX",
}


def limit_linux(directory):
    import resource

    from .linux_sandbox import restrict_files, restrict_network

    for limit, size in ((resource.RLIMIT_AS, MEMORY), (resource.RLIMIT_CPU, CPU_SECONDS),
                        (resource.RLIMIT_FSIZE, 128 * 1024**2), (resource.RLIMIT_CORE, 0)):
        old_soft, old_hard = resource.getrlimit(limit)
        soft = min(size, old_soft) if old_soft != resource.RLIM_INFINITY else size
        hard = min(size, old_hard) if old_hard != resource.RLIM_INFINITY else size
        resource.setrlimit(limit, (soft, hard))
    scope = "linux-files-network"
    try:
        restrict_files(directory)
    except OSError as error:
        if error.errno not in {errno.ENOSYS, errno.EOPNOTSUPP}:
            raise
        scope = "linux-network"
    restrict_network()
    return scope


def contained_worker(entry, args, directory, gate, status):
    linux = sys.platform.startswith("linux")
    try:
        if os.name == "posix":
            os.setsid()
        env = {key: value for key, value in os.environ.items() if key.upper() in ENV_NAMES}
        for key in ("OE_TESSERACT", "TESSDATA_PREFIX"):
            if key in env and (key == "TESSDATA_PREFIX" or "/" in env[key] or "\\" in env[key]):
                env[key] = str(Path(env[key]).resolve())
        os.environ.clear()
        os.environ.update(env)
        os.environ.update(TEMP=str(directory), TMP=str(directory), TMPDIR=str(directory),
                          PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        sys.dont_write_bytecode = True
        os.chdir(directory)
        scope = limit_linux(directory) if linux else "windows-job" if os.name == "nt" else "process-group"
        status.send(scope)
    except OSError:
        status.send(False)
        return
    finally:
        status.close()
    if gate.wait(START_TIMEOUT):
        try:
            entry(*args)
        finally:
            if linux:
                os.killpg(os.getpgrp(), signal.SIGKILL)


class WorkerProcess:
    def __init__(self, entry, args, directory):
        context = multiprocessing.get_context("spawn")
        gate = context.Event()
        ready, sender = context.Pipe(duplex=False)
        self.job = None
        self.group = False
        self.process = context.Process(target=contained_worker, args=(entry, args, directory, gate, sender), daemon=True)
        started = False
        try:
            self.process.start()
            started = True
            sender.close()
            if os.name == "nt":
                from .windows_job import WindowsJob

                self.job = WindowsJob(self.process.sentinel, MEMORY, CPU_SECONDS)
            self.isolation = ready.recv() if ready.poll(START_TIMEOUT) else None
            if not self.isolation:
                raise OSError("Protezione del processo PDF non disponibile.")
            self.group = os.name == "posix"
            gate.set()
        except (EOFError, OSError) as error:
            if started:
                self.stop()
            raise OSError("Protezione del processo PDF non disponibile.") from error
        finally:
            sender.close()
            ready.close()

    def is_alive(self):
        return self.process.is_alive()

    def finish(self):
        self.process.join()
        self._close_job()

    def stop(self):
        self._close_job()
        if self.process.is_alive():
            if self.group:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.process.kill()
        self.process.join(timeout=5)
        if self.process.is_alive():
            raise OSError("Impossibile terminare il processo PDF.")

    def _close_job(self):
        if self.job is not None:
            self.job.close()
            self.job = None
