"""
iPos 5.0 — Update System
- Cek versi terbaru dari GitHub
- Backup ke email sebelum update
- Return download URL
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime

from ..auth import get_current_user, require_admin
from ..config import settings

router = APIRouter()

CURRENT_VERSION = settings.APP_VERSION


def version_tuple(v: str):
    """Convert '5.1.2' → (5, 1, 2) untuk perbandingan"""
    try:
        return tuple(int(x) for x in v.strip("v").split("."))
    except Exception:
        return (0, 0, 0)


@router.get("/check")
async def check_update(_=Depends(get_current_user)):
    """
    Cek versi terbaru.
    Format version.json di GitHub:
    {
        "version": "5.1.0",
        "release_date": "2025-01-15",
        "notes": "Tambah fitur Multi Gudang dan Perakitan",
        "download_url": "https://github.com/.../iPos-5.1.0.exe",
        "download_url_installer": "https://github.com/.../iPos-Setup-5.1.0.exe",
        "mandatory": false,
        "min_version": "5.0.0"
    }
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.UPDATE_CHECK_URL)
            resp.raise_for_status()
            remote = resp.json()

        latest_version = remote.get("version", CURRENT_VERSION)
        has_update = version_tuple(latest_version) > version_tuple(CURRENT_VERSION)
        mandatory = remote.get("mandatory", False)

        # Cek apakah versi saat ini masih di atas min_version
        min_version = remote.get("min_version", "0.0.0")
        below_minimum = version_tuple(CURRENT_VERSION) < version_tuple(min_version)

        return {
            "current_version": CURRENT_VERSION,
            "latest_version": latest_version,
            "has_update": has_update,
            "mandatory": mandatory or below_minimum,
            "below_minimum": below_minimum,
            "release_date": remote.get("release_date", ""),
            "notes": remote.get("notes", ""),
            "download_url": remote.get("download_url", ""),
            "download_url_installer": remote.get("download_url_installer", ""),
            "checked_at": datetime.now().isoformat()
        }

    except httpx.TimeoutException:
        return {
            "current_version": CURRENT_VERSION,
            "has_update": False,
            "error": "Timeout — tidak bisa cek update. Coba lagi nanti.",
            "checked_at": datetime.now().isoformat()
        }
    except httpx.HTTPStatusError as e:
        return {
            "current_version": CURRENT_VERSION,
            "has_update": False,
            "error": f"Server error: {e.response.status_code}",
            "checked_at": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "current_version": CURRENT_VERSION,
            "has_update": False,
            "error": f"Tidak bisa cek update: {str(e)}",
            "checked_at": datetime.now().isoformat()
        }


@router.get("/version")
def get_version(_=Depends(get_current_user)):
    """Versi aplikasi saat ini"""
    return {
        "version": CURRENT_VERSION,
        "app_name": settings.APP_NAME,
        "built_at": "2025-01-01"
    }


@router.post("/prepare")
async def prepare_update(_=Depends(require_admin)):
    """
    Persiapan sebelum update:
    1. Cek apakah email backup sudah dikonfigurasi
    2. Kirim backup ke email
    3. Kembalikan status: aman atau tidak untuk update
    """
    # Import di sini untuk hindari circular import
    from .email_backup import send_backup_email, load_email_config

    cfg = load_email_config()
    steps = []

    # Step 1: Cek config email
    email_configured = bool(
        cfg.get("smtp_user") and
        cfg.get("smtp_pass") and
        cfg.get("backup_email")
    )
    steps.append({
        "step": 1,
        "name": "Cek konfigurasi email",
        "status": "ok" if email_configured else "warning",
        "message": "Email backup siap" if email_configured
                   else "Email belum dikonfigurasi — backup lokal saja"
    })

    # Step 2: Backup lokal
    import os, shutil
    from datetime import datetime as dt
    db_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "ipos.db"
    ))
    backup_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "backups"
    ))
    local_backup_ok = False
    if os.path.exists(db_path):
        try:
            os.makedirs(backup_dir, exist_ok=True)
            dest = os.path.join(backup_dir, f"pre_update_{dt.now().strftime('%Y%m%d_%H%M%S')}.db")
            shutil.copy2(db_path, dest)
            local_backup_ok = True
            steps.append({
                "step": 2,
                "name": "Backup lokal",
                "status": "ok",
                "message": f"Tersimpan di backups/{os.path.basename(dest)}"
            })
        except Exception as e:
            steps.append({
                "step": 2,
                "name": "Backup lokal",
                "status": "error",
                "message": f"Gagal: {str(e)}"
            })
    else:
        steps.append({
            "step": 2, "name": "Backup lokal",
            "status": "warning", "message": "Database belum ada"
        })
        local_backup_ok = True  # fresh install, tidak masalah

    # Step 3: Backup email (jika dikonfigurasi)
    email_backup_ok = False
    if email_configured:
        result = send_backup_email("Pre-Update Backup")
        email_backup_ok = result["success"]
        steps.append({
            "step": 3,
            "name": "Backup ke email",
            "status": "ok" if result["success"] else "error",
            "message": result["message"]
        })
    else:
        steps.append({
            "step": 3,
            "name": "Backup ke email",
            "status": "skipped",
            "message": "Dilewati — email belum dikonfigurasi"
        })
        email_backup_ok = True  # tidak required jika tidak dikonfigurasi

    # Tentukan apakah aman untuk update
    safe_to_update = local_backup_ok  # minimal backup lokal harus berhasil

    return {
        "safe_to_update": safe_to_update,
        "steps": steps,
        "message": (
            "✓ Semua backup berhasil. Aman untuk melanjutkan update."
            if safe_to_update else
            "✕ Backup lokal gagal. Update dibatalkan untuk keamanan data."
        )
    }
