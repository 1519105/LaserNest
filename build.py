"""Sestavení samostatné aplikace (PyInstaller).

    pip install -r requirements.txt pyinstaller
    python build.py

Výsledek: dist/LaserNest.exe (Windows) nebo dist/LaserNest (Linux).
"""
import os
import sys

import PyInstaller.__main__

sep = ";" if sys.platform.startswith("win") else ":"
excl = ["tkinter", "matplotlib", "pandas", "scipy", "IPython", "pytest",
        "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
        "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtOpenGL"]
args = ["main.py", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "LaserNest",
        "--icon", os.path.join("assets", "icon.ico"),
        "--add-data", f"{os.path.join('assets', 'icon.png')}{sep}assets",
        "--add-data", f"{os.path.join('assets', 'fonts')}{sep}{os.path.join('assets', 'fonts')}"]
for m in excl:
    args += ["--exclude-module", m]
PyInstaller.__main__.run(args)
