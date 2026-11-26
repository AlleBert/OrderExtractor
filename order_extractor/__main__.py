import multiprocessing

from order_extractor.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
