# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build config: pyinstaller pdf2md.spec --noconfirm --clean
import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("app.py", ".")]
binaries = []
hiddenimports = ["fitz", "certifi"]

# Streamlit needs its static files and package metadata at runtime.
# app.py is bundled as a data file, so its imports must be collected explicitly.
for pkg in ["streamlit", "easyocr", "pymupdf"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        print(f"[spec] collect_all({pkg}) skipped: {exc}")

for pkg in ["streamlit", "easyocr", "torch", "torchvision"]:
    try:
        datas += copy_metadata(pkg, recursive=(pkg == "streamlit"))
    except Exception as exc:
        print(f"[spec] copy_metadata({pkg}) skipped: {exc}")

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF2MD",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDF2MD")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PDF2MD.app",
        icon=None,
        bundle_identifier="io.github.pdf2md",
        info_plist={
            "CFBundleDisplayName": "PDF2MD",
            "CFBundleShortVersionString": os.environ.get("APP_VERSION", "0.0.0"),
            "NSHighResolutionCapable": True,
        },
    )
