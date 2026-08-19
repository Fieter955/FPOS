"""Secure in-app update flow for the single FPOS server installation.

The running FPOS process never replaces its own files.  It downloads and
verifies a release, then starts ``FPOS-Updater.exe`` which waits for this
process to exit, swaps the code directories, verifies startup, and rolls back
on failure.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException

from update_common import (
    MAX_UPDATE_BYTES,
    install_dir,
    is_valid_version,
    make_temp_dir,
    sha256_file,
    version_tuple,
)

from ..auth import get_current_user
from ..config import settings
from ..permissions import has_role

router = APIRouter()
CURRENT_VERSION = settings.APP_VERSION
_STATE_NAME = ".update-state.json"
_LOCK_NAME = ".update.lock"


def require_update_admin(current_user=Depends(get_current_user)):
    """Only a real admin may replace the server executable and code bundle."""

    if not has_role(current_user, "admin"):
        raise HTTPException(status_code=403, detail="Akses ditolak: hanya admin dapat mengupdate aplikasi.")
    return current_user


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _state_path() -> Path:
    return install_dir() / _STATE_NAME


def _lock_path() -> Path:
    return install_dir() / _LOCK_NAME


def _write_state(status: str, **extra: Any) -> dict[str, Any]:
    payload = {"status": status, "updated_at": _now(), **extra}
    path = _state_path()
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
    return payload


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _acquire_lock() -> None:
    try:
        descriptor = os.open(str(_lock_path()), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Update lain sedang berjalan.")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "started_at": _now()}))


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except FileNotFoundError:
        pass


def _allow_http() -> bool:
    return bool(getattr(settings, "UPDATE_ALLOW_HTTP", False))


def _validate_url(value: Any, field_name: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    allowed_schemes = {"https"} | ({"http"} if _allow_http() else set())
    if parsed.scheme.lower() not in allowed_schemes or not parsed.netloc:
        raise ValueError(f"{field_name} harus berupa URL HTTPS yang valid")
    return url


def _validate_manifest(remote: Any) -> dict[str, Any]:
    if not isinstance(remote, dict):
        raise ValueError("Manifest update bukan JSON object")

    version = str(remote.get("version", "")).strip()
    if not is_valid_version(version):
        raise ValueError("Manifest update memiliki versi yang tidak valid")

    result = dict(remote)
    result["version"] = version
    result["download_url"] = _validate_url(remote.get("download_url"), "download_url")
    checksum = str(remote.get("sha256", "")).strip().lower()
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise ValueError("Manifest update wajib memiliki SHA-256 valid")
    result["sha256"] = checksum
    try:
        result["size_bytes"] = max(0, int(remote.get("size_bytes", 0) or 0))
    except (TypeError, ValueError):
        result["size_bytes"] = 0
    return result


async def _fetch_manifest() -> dict[str, Any]:
    manifest_url = _validate_url(settings.UPDATE_CHECK_URL, "UPDATE_CHECK_URL")
    timeout = httpx.Timeout(15.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(manifest_url)
        response.raise_for_status()
        return _validate_manifest(response.json())


def _version_result(remote: dict[str, Any]) -> dict[str, Any]:
    latest_version = remote["version"]
    minimum_version = str(remote.get("minimum_version", remote.get("min_version", "0.0.0")))
    below_minimum = version_tuple(CURRENT_VERSION) < version_tuple(minimum_version)
    has_update = version_tuple(latest_version) > version_tuple(CURRENT_VERSION)
    return {
        "current_version": CURRENT_VERSION,
        "latest_version": latest_version,
        "has_update": has_update,
        "mandatory": bool(remote.get("mandatory", False)) or below_minimum,
        "below_minimum": below_minimum,
        "release_date": remote.get("release_date", ""),
        "notes": remote.get("notes", ""),
        "download_url": remote.get("download_url", "") if has_update else "",
        "sha256": remote.get("sha256", "") if has_update else "",
        "size_bytes": remote.get("size_bytes", 0) if has_update else 0,
        "checked_at": _now(),
    }


async def _download_update(remote: dict[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    expected_size = int(remote.get("size_bytes", 0) or 0)
    received = 0
    timeout = httpx.Timeout(60.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", remote["download_url"]) as response:
                response.raise_for_status()
                final_url = urlparse(str(response.url))
                if final_url.scheme.lower() not in ({"https"} | ({"http"} if _allow_http() else set())):
                    raise ValueError("Server update mengarahkan ke URL yang tidak aman")
                content_length = int(response.headers.get("content-length", 0) or 0)
                if content_length > MAX_UPDATE_BYTES or (expected_size and content_length != expected_size):
                    raise ValueError("Ukuran paket update tidak sesuai manifest")
                with partial.open("wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        received += len(chunk)
                        if received > MAX_UPDATE_BYTES:
                            raise ValueError("Paket update melebihi batas ukuran")
                        output.write(chunk)
        if received == 0 or (expected_size and received != expected_size):
            raise ValueError("Paket update tidak lengkap")
        actual = sha256_file(partial)
        if actual != remote["sha256"]:
            raise ValueError("SHA-256 paket update tidak cocok")
        os.replace(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


def _backup_database() -> Path | None:
    root = install_dir()
    source = root / "ipos.db"
    if not source.exists():
        return None
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = backup_dir / f"pre_update_{stamp}.db"
    temporary = destination.with_suffix(".db.tmp")
    try:
        with sqlite3.connect(str(source), timeout=15) as source_db, sqlite3.connect(str(temporary)) as backup_db:
            source_db.backup(backup_db)
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_local_backup() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    try:
        backup = _backup_database()
        if backup:
            steps.append({"step": 1, "name": "Backup database", "status": "ok", "message": str(backup)})
        else:
            steps.append({"step": 1, "name": "Backup database", "status": "warning", "message": "Database belum ada"})
        for name in (".env", "secret.key"):
            source = install_dir() / name
            if source.exists():
                target_dir = install_dir() / "backups" / "runtime"
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target_dir / name)
        return {"safe_to_update": True, "steps": steps, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        steps.append({"step": 1, "name": "Backup database", "status": "error", "message": str(exc)})
        return {"safe_to_update": False, "steps": steps, "backup_path": None}


def _runner_command(archive: Path, version: str, backup_path: str | None) -> list[str]:
    root = install_dir()
    helper = root / "FPOS-Updater.exe"
    if getattr(sys, "frozen", False):
        if not helper.is_file():
            raise RuntimeError("FPOS-Updater.exe belum tersedia di paket instalasi")
        command = [str(helper)]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parents[2] / "update_runner.py")]
    return command + [
        "--install-dir", str(root),
        "--archive", str(archive),
        "--pid", str(os.getpid()),
        "--version", version,
        "--backup-path", backup_path or "",
    ]


def _launch_runner(command: list[str]) -> None:
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        subprocess.Popen(
            command,
            cwd=str(install_dir()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except Exception as exc:
        _write_state("failed", error=f"Updater tidak dapat dijalankan: {exc}")
        _release_lock()


@router.get("/check")
async def check_update(_=Depends(get_current_user)):
    try:
        return _version_result(await _fetch_manifest())
    except httpx.TimeoutException:
        return {"current_version": CURRENT_VERSION, "has_update": False, "error": "Timeout saat mengecek update.", "checked_at": _now()}
    except httpx.HTTPStatusError as exc:
        return {"current_version": CURRENT_VERSION, "has_update": False, "error": f"Server update mengembalikan HTTP {exc.response.status_code}.", "checked_at": _now()}
    except Exception as exc:
        return {"current_version": CURRENT_VERSION, "has_update": False, "error": f"Tidak bisa mengecek update: {exc}", "checked_at": _now()}


@router.get("/status")
def update_status(_=Depends(get_current_user)):
    return {"current_version": CURRENT_VERSION, **_read_state()}


@router.get("/version")
def get_version(_=Depends(get_current_user)):
    return {"version": CURRENT_VERSION, "app_name": settings.APP_NAME}


@router.post("/prepare")
async def prepare_update(_=Depends(require_update_admin)):
    """Create a consistent local backup before showing the final confirmation."""

    result = _prepare_local_backup()
    if not result["safe_to_update"]:
        result["message"] = "Backup gagal. Update dibatalkan untuk keamanan data."
        return result

    # Email backup remains optional and is deliberately not required for a safe update.
    try:
        from .email_backup import load_email_config, send_backup_email

        cfg = load_email_config()
        configured = bool(cfg.get("smtp_user") and cfg.get("smtp_pass") and cfg.get("backup_email"))
        if configured:
            email_result = send_backup_email("Pre-Update Backup")
            result["steps"].append({
                "step": 2,
                "name": "Backup email",
                "status": "ok" if email_result.get("success") else "warning",
                "message": email_result.get("message", "Tidak ada detail"),
            })
    except Exception as exc:
        result["steps"].append({"step": 2, "name": "Backup email", "status": "warning", "message": str(exc)})
    result["message"] = "Backup berhasil. Aman untuk melanjutkan update."
    return result


@router.post("/apply")
async def apply_update(_=Depends(require_update_admin)):
    """Download, verify, and hand off installation to the external updater."""

    _acquire_lock()
    launched = False
    work_dir: Path | None = None
    try:
        remote = await _fetch_manifest()
        result = _version_result(remote)
        if not result["has_update"]:
            raise HTTPException(status_code=409, detail="Tidak ada update baru.")

        backup_result = _prepare_local_backup()
        if not backup_result["safe_to_update"]:
            raise HTTPException(status_code=409, detail="Backup gagal. Update dibatalkan.")

        work_dir = make_temp_dir(install_dir())
        _write_state("downloading", version=remote["version"], progress=0)
        archive = await _download_update(remote, work_dir / "update.zip")
        _write_state("verifying", version=remote["version"], progress=100)

        # Extraction here validates the ZIP before the server is stopped. The helper
        # extracts again in its own staging area before touching installed code.
        import zipfile

        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Paket update rusak")

        command = _runner_command(archive, remote["version"], backup_result.get("backup_path"))
        _write_state("ready_to_restart", version=remote["version"], notes=remote.get("notes", ""))
        threading.Timer(0.8, _launch_runner, args=(command,)).start()
        launched = True
        return {
            "status": "ready_to_restart",
            "version": remote["version"],
            "message": "Update siap dipasang. Server akan restart beberapa detik.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        _write_state("failed", error=str(exc))
        raise HTTPException(status_code=400, detail=f"Update dibatalkan: {exc}") from exc
    finally:
        if not launched:
            _release_lock()
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
