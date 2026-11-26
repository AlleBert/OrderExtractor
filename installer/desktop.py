import io
import multiprocessing
import os
import sys
import traceback
from pathlib import Path

if __name__ == "__main__":
    # The Windows GUI bootloader does not attach standard streams.
    probing = "--probe-version" in sys.argv
    worker = sys.argv[1:2] == ["--pdf-worker"]
    log = os.environ.get("OE_PROBE_LOG") if probing else None
    if worker and len(sys.argv) == 4 and sys.argv[3] == "--trace-probe":
        log = Path(sys.argv[2]) / "probe.log"
    try:
        if sys.stdout is None:
            sys.stdout = open(log, "w", encoding="utf-8", buffering=1) if log else io.StringIO()
        if sys.stderr is None:
            sys.stderr = sys.stdout
        if log:
            print("Avvio del pacchetto Python", flush=True)
        multiprocessing.freeze_support()
        from order_extractor.launcher import main

        main()
    except Exception:
        if not (probing or worker):
            raise
        traceback.print_exc()
        raise SystemExit(1)
