"""Small, dependency-free helpers shared by the server and update runner.

The update runner is built as a separate PyInstaller executable, so this module
must stay in the Python standard library only.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path


CODE_PATHS = ("FPOS.exe", "_internal", "frontend-dist")
MAX_UPDATE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def install_dir() -> Path:
    """Return the directory that contains FPOS.exe in dev and frozen modes."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def version_tuple(value: str) -> tuple[int, int, int]:
    """Parse the supported release format; invalid versions sort lowest."""

    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def is_valid_version(value: str) -> bool:
    return bool(_VERSION_RE.fullmatch(str(value or "").strip()))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_zip_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _safe_zip_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if not normalized or normalized.endswith("/"):
        return normalized
    drive, _ = os.path.splitdrive(normalized)
    parts = Path(normalized).parts
    if drive or normalized.startswith("/") or ".." in parts:
        raise ValueError(f"Nama file ZIP tidak aman: {name}")
    return normalized


def _package_prefix(names: list[str]) -> str:
    """Find a root prefix, supporting both flat and one-folder ZIP layouts."""

    files = {_normal_zip_name(name).rstrip("/") for name in names}
    candidates = []
    for name in files:
        if name.endswith("/FPOS.exe") or name == "FPOS.exe":
            candidates.append(name[: -len("FPOS.exe")])
    for prefix in candidates:
        required = {
            f"{prefix}FPOS.exe",
            f"{prefix}_internal",
            f"{prefix}frontend-dist",
        }
        has_internal = any(name.startswith(f"{prefix}_internal/") for name in files)
        has_frontend = any(name.startswith(f"{prefix}frontend-dist/") for name in files)
        if f"{prefix}FPOS.exe" in files and has_internal and has_frontend:
            return prefix
    raise ValueError("Paket update tidak berisi FPOS.exe, _internal, dan frontend-dist")


def extract_update_archive(archive: Path, destination: Path) -> Path:
    """Safely extract an update and return the package root inside destination."""

    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = [_safe_zip_name(info.filename) for info in bundle.infolist()]
        prefix = _package_prefix(names)
        extracted_size = sum(info.file_size for info in bundle.infolist())
        if extracted_size > MAX_EXTRACTED_BYTES:
            raise ValueError("Isi hasil ekstraksi paket terlalu besar")
        for info, safe_name in zip(bundle.infolist(), names):
            if not safe_name:
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ValueError(f"Paket update tidak boleh berisi symbolic link: {info.filename}")
            target = (destination / safe_name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError(f"ZIP mencoba menulis di luar staging: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)

    package_root = destination / prefix.rstrip("/")
    if not (package_root / "FPOS.exe").is_file():
        raise ValueError("FPOS.exe tidak ditemukan setelah ekstraksi")
    if not (package_root / "_internal").is_dir():
        raise ValueError("Folder _internal tidak ditemukan setelah ekstraksi")
    if not (package_root / "frontend-dist").is_dir():
        raise ValueError("Folder frontend-dist tidak ditemukan setelah ekstraksi")
    return package_root


def make_temp_dir(parent: Path, prefix: str = ".fpos-update-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
