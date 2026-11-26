import multiprocessing
import os
import sys
import traceback

if __name__ == "__main__":
    # The Windows GUI bootloader does not attach standard streams.
    probing = "--probe-version" in sys.argv
    log = os.environ.get("OE_PROBE_LOG") if probing else None
    if sys.stdout is None:
        sys.stdout = open(log or os.devnull, "w", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    multiprocessing.freeze_support()
    try:
        from order_extractor.launcher import main

        main()
    except Exception:
        if not probing:
            raise
        traceback.print_exc()
        raise SystemExit(1)
