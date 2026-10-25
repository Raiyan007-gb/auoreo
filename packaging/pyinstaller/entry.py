"""PyInstaller entry point for the onefile `auoreo` binary."""

import multiprocessing

from auoreo.cli.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # `serve --workers N` spawns worker processes
    main()
