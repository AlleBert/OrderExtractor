import multiprocessing
import os
import sys

from order_extractor.launcher import main

if __name__ == "__main__":
    # The Windows GUI bootloader does not attach standard streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    multiprocessing.freeze_support()
    main()
