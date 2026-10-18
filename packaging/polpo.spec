# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller packaging specification for Polpo standalone desktop distribution.

Builds a self-contained --onedir bundle for Linux x86_64 and Windows x64.
Collects required QML interfaces, SQLite migrations, Pandoc and Node.js runtimes,
MathJax worker, LaTeX templates, curated prompts, and licensing documentation.
Excludes legacy Telegram and MySQL server dependencies.
"""

from pathlib import Path
import sys

block_cipher = None
spec_dir = Path(SPECPATH).resolve() if "SPECPATH" in dir() else Path.cwd().resolve()
repo_root = spec_dir.parent if spec_dir.name == "packaging" else spec_dir

# 1. Collect required static assets and data files
datas = [
    (str(repo_root / "interfaces" / "desktop" / "qml"), "interfaces/desktop/qml"),
    (str(repo_root / "infrastructure" / "persistence" / "sqlite" / "migrations"), "infrastructure/persistence/sqlite/migrations"),
    (str(repo_root / "resources" / "templates" / "polpo_preamble.tex"), "resources/templates"),
    (str(repo_root / "resources" / "runtimes.json"), "resources"),
    (str(repo_root / "resources" / "bin"), "resources/bin"),
    (str(repo_root / "resources" / "mathjax"), "resources/mathjax"),
    (str(repo_root / "prompts"), "prompts"),
    (str(repo_root / "LICENSE"), "."),
    (str(repo_root / "README.md"), "."),
    (str(repo_root / "CHANGELOG.md"), "."),
]

entitlements_file = repo_root / "resources" / "entitlements.plist"
if entitlements_file.is_file():
    datas.append((str(entitlements_file), "resources"))

# 2. Hidden imports required for dynamic dispatch, keyring backends, and PySide6 QML plugins
hiddenimports = [
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtSvg",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "keyring.backends",
    "keyring.backends.SecretService",
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "platformdirs",
    "cryptography",
    "core.version",
]

# 3. Explicit exclusion of legacy Telegram, MySQL, and developer test dependencies
excludes = [
    "telegram",
    "python-telegram-bot",
    "pymysql",
    "handlers",
    "interfaces.telegram",
    "database",
    "tests",
    "pytest",
    "unittest",
]

a = Analysis(
    [str(repo_root / "interfaces" / "desktop" / "app.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Polpo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(entitlements_file) if entitlements_file.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Polpo",
)
