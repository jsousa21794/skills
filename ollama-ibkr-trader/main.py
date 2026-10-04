"""Ponto de entrada (também usado pelo PyInstaller)."""

import sys

from trader.app import main

if __name__ == "__main__":
    sys.exit(main())
