# -*- mode: python ; coding: utf-8 -*-
"""Spec do PyInstaller: executável único com GUI (sem consola).

Uso:  pyinstaller trader.spec
Saída: dist/OllamaIBKRTrader(.exe)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("customtkinter", "ib_async", "eventkit"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("trader")
datas += [("data/seed_lessons.json", "data")]
hiddenimports += ["nest_asyncio", "tzdata", "zoneinfo", "sqlite3", "requests", "darkdetect", "yfinance"]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "IPython", "notebook", "PyQt5", "PySide2", "torch", "transformers", "chronos"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OllamaIBKRTrader",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # True para ver tracebacks durante o desenvolvimento
    icon=None,
)
