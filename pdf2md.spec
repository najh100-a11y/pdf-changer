# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build config: pyinstaller pdf2md.spec --noconfirm --clean
import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = ["converter", "fitz", "certifi"]
# scipy._cyutility exists only in scipy >= 1.16; include it when present so newer scipy is not bundled half-broken
if importlib.util.find_spec("scipy._cyutility") is not None:
    hiddenimports.append("scipy._cyutility")

for pkg in ["easyocr", "pymupdf"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        print(f"[spec] collect_all({pkg}) skipped: {exc}")

for pkg in ["easyocr", "torch", "torchvision"]:
    try:
        datas += copy_metadata(pkg)
    except Exception as exc:
        print(f"[spec] copy_metadata({pkg}) skipped: {exc}")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
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
            "NSRequiresAquaSystemAppearance": False,
        },
    )
