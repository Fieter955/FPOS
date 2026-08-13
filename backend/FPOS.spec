# -*- mode: python ; coding: utf-8 -*-
import os, sys
# Resolusi DLL: utamakan conda ENV AKTIF (sys.prefix\Library\bin) agar OpenSSL libcrypto/libssl
# yang ter-bundle COCOK dengan _ssl.pyd. Tanpa ini PyInstaller bisa mengambil libcrypto versi lain
# dari PATH (conda base / poppler / php) → exe crash "_ssl: procedure could not be found".
os.environ["PATH"] = os.path.join(sys.prefix, "Library", "bin") + os.pathsep + os.environ.get("PATH", "")

from PyInstaller.utils.hooks import collect_submodules

# uvicorn memuat loop & protokol HTTP/websocket secara DINAMIS (import lewat string saat runtime),
# sehingga analisis statis PyInstaller melewatkannya. Tanpa ini, uvicorn.run() gagal di dalam exe
# (server tidak pernah bind → window "refused to connect"). Kumpulkan semua submodul uvicorn.
_hidden = collect_submodules('uvicorn') + ['h11']

# Environment build `ipos` juga berisi library data-science/CUDA berukuran beberapa
# gigabyte. FPOS tidak mengimpornya; AI aplikasi memakai API online (OpenAI/Gemini/
# Groq), sedangkan import Excel hanya membutuhkan pandas/openpyxl. Mengecualikan
# modul opsional ini menjaga paket Windows tetap ringan tanpa mengurangi fitur FPOS.
_excludes = [
    'torch',
    'torchvision',
    'torchaudio',
    'transformers',
    'cv2',
    'scipy',
    'sklearn',
    'matplotlib',
    'PyQt5',
    'IPython',
    'notebook',
    'nbformat',
    # Tampilan kini memakai Edge/Chrome. Hindari WinForms + pythonnet/.NET yang
    # rentan diblokir Windows ketika ZIP berasal dari internet.
    'webview',
    'pythonnet',
    'clr_loader',
    'clr',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Mode onedir: binaries/datas dikumpulkan ke folder dist/FPOS/ (lewat COLLECT), bukan dibungkus
# ke dalam exe (onefile). Startup jauh lebih cepat karena tidak ada ekstraksi ke folder temp
# tiap kali aplikasi dibuka. Frontend tetap dikirim sebagai file lepas di samping FPOS.exe.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FPOS',
)
