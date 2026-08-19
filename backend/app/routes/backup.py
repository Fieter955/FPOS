from fastapi import APIRouter, Depends, Response, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
import shutil, os
from datetime import datetime
from ..database import get_db
from ..auth import require_admin, get_current_user
from .. import models

router = APIRouter()

@router.get("/download")
def download_backup(_=Depends(require_admin)):
    """Download backup database sebagai file"""
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "ipos.db")
    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        from fastapi import HTTPException
        raise HTTPException(404, "Database tidak ditemukan")
    with open(db_path, "rb") as f:
        content = f.read()
    filename = f"ipos_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/auto")
def auto_backup(_=Depends(require_admin)):
    """Simpan backup ke folder backups/ lokal"""
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ipos.db"))
    backup_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backups"))
    os.makedirs(backup_dir, exist_ok=True)
    filename = f"ipos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    dest = os.path.join(backup_dir, filename)
    shutil.copy2(db_path, dest)
    # Simpan max 30 backup
    backups = sorted(os.listdir(backup_dir))
    while len(backups) > 30:
        os.remove(os.path.join(backup_dir, backups.pop(0)))
    return {"message": f"Backup disimpan: {filename}", "path": dest}

@router.get("/list")
def list_backups(_=Depends(require_admin)):
    backup_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backups"))
    if not os.path.exists(backup_dir):
        return []
    files = sorted(os.listdir(backup_dir), reverse=True)
    return [{"filename": f, "size_kb": round(os.path.getsize(os.path.join(backup_dir, f))/1024, 1)}
            for f in files if f.endswith(".db")]


# ══════════════════════════════════════════════════════════════════════════════
# IMPORT DATABASE
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import UploadFile, File
import shutil, tempfile

@router.post("/import")
async def import_database(
    file: UploadFile = File(...),
    current_user: models.User = Depends(require_admin)
):
    """
    Import / restore database dari file .db yang diupload.
    Menggunakan SQLite built-in backup API — aman di Windows (tidak perlu delete file).
    """
    if not file.filename or not file.filename.endswith(".db"):
        raise HTTPException(400, "File harus berekstensi .db")

    file_content = await file.read()

    if len(file_content) > 200 * 1024 * 1024:
        raise HTTPException(400, "File terlalu besar (max 200MB)")

    if len(file_content) < 16 or file_content[:16] != b"SQLite format 3\x00":
        raise HTTPException(400, "File bukan database SQLite yang valid")

    # SQLite backup API + file I/O = blocking. Jalankan di threadpool agar event
    # loop tidak freeze (penting saat server publik melayani banyak cabang).
    return await run_in_threadpool(_import_database_sync, file_content)


def _import_database_sync(file_content: bytes):
    import sqlite3, os, shutil
    from datetime import datetime

    # Tentukan path DB dari DATABASE_URL di config
    from ..config import settings
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///./"):
        # Relative path — cari dari CWD (folder backend/)
        rel = db_url.replace("sqlite:///./", "")
        db_path = os.path.abspath(rel)
    elif db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
    else:
        raise HTTPException(500, "Hanya mendukung SQLite database")

    backup_dir = os.path.abspath(os.path.join(os.path.dirname(db_path), "backups"))
    os.makedirs(backup_dir, exist_ok=True)

    # Tulis file upload ke temp path dulu
    tmp_path = db_path + ".import_tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(file_content)

        # Validasi: cek tabel wajib
        src_conn = sqlite3.connect(tmp_path)
        tables = [r[0] for r in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        src_conn.close()

        required = ["users", "items"]
        missing = [t for t in required if t not in tables]
        if missing:
            raise HTTPException(400,
                f"Database tidak valid — tabel {missing} tidak ada. "
                f"Pastikan upload file ipos.db yang benar.")

        # Backup database aktif sekarang
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"pre_import_{ts}.db"
        backup_path = os.path.join(backup_dir, backup_name)
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_path)

        # ── Gunakan SQLite backup API ───────────────────────────────────────
        # Cara ini bekerja di Windows meski file DB sedang dibuka oleh SQLAlchemy
        # karena SQLite backup API berjalan di level SQLite, bukan level OS file
        from ..database import engine
        engine.dispose()  # release semua connection pool

        source = sqlite3.connect(tmp_path)
        dest = sqlite3.connect(db_path)
        source.backup(dest, pages=500)   # copy 500 pages sekaligus
        dest.close()
        source.close()

        os.remove(tmp_path)

        # Dispose lagi supaya SQLAlchemy buka koneksi fresh ke data baru
        engine.dispose()

        return {
            "success": True,
            "message": "Database berhasil di-import! Silakan refresh halaman.",
            "tables_found": len(tables),
            "backup_created": backup_name,
            "warning": "Logout dan login ulang agar data baru aktif."
        }

    except HTTPException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(500, f"Import gagal: {str(e)}")
