"""
iPos 5.0 — Email Backup System
- Backup manual ke email
- Auto backup harian
- Test koneksi SMTP
- Konfigurasi tersimpan di .env (bisa diubah via API)
"""

import smtplib, os, shutil, json
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from ..auth import require_admin, get_current_user
from .. import models
from ..config import settings

router = APIRouter()

# Path untuk simpan override config (agar bisa diubah via UI tanpa edit .env)
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "email_override.json")


# ─── Config helpers ───────────────────────────────────────────────────────────

def load_email_config() -> dict:
    """Load config: prioritas dari override file, fallback ke .env"""
    base = {
        "smtp_host": settings.SMTP_HOST,
        "smtp_port": settings.SMTP_PORT,
        "smtp_user": settings.SMTP_USER,
        "smtp_pass": settings.SMTP_PASS,
        "backup_email": settings.BACKUP_EMAIL,
        "auto_backup_hour": settings.AUTO_BACKUP_HOUR,
        "enabled": settings.EMAIL_BACKUP_ENABLED,
    }
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE) as f:
                override = json.load(f)
            base.update(override)
    except Exception:
        pass
    return base


def save_email_config(config: dict):
    """Simpan config ke override file"""
    # Jangan simpan field yang kosong
    cleaned = {k: v for k, v in config.items() if v is not None and v != ""}
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cleaned, f, indent=2)


# ─── Core email send function ─────────────────────────────────────────────────

def send_backup_email(label: str = "Manual") -> dict:
    """
    Kirim backup database ke email.
    Returns: {"success": bool, "message": str}
    """
    cfg = load_email_config()

    # Validasi config
    missing = []
    for field in ["smtp_user", "smtp_pass", "backup_email"]:
        if not cfg.get(field):
            missing.append(field)
    if missing:
        return {"success": False,
                "message": f"Konfigurasi email belum lengkap: {', '.join(missing)}"}

    # Cari file database
    db_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "ipos.db"
    ))
    if not os.path.exists(db_path):
        return {"success": False, "message": "File database tidak ditemukan"}

    # Buat file backup sementara
    now = datetime.now()
    backup_filename = f"ipos_backup_{now.strftime('%Y%m%d_%H%M%S')}.db"
    tmp_path = os.path.join(os.path.dirname(db_path), "tmp_" + backup_filename)

    try:
        shutil.copy2(db_path, tmp_path)
        db_size_kb = os.path.getsize(tmp_path) / 1024

        # Buat email
        msg = MIMEMultipart()
        msg["From"] = cfg["smtp_user"]
        msg["To"] = cfg["backup_email"]
        msg["Subject"] = f"[iPos 5.0] Backup Database — {now.strftime('%d %B %Y %H:%M')} ({label})"

        body = f"""
<html><body style="font-family:Arial,sans-serif;color:#333">
<h2 style="color:#10b981">📦 Backup Database iPos 5.0</h2>
<table style="border-collapse:collapse;width:100%;max-width:500px">
  <tr><td style="padding:8px;background:#f1f5f9;font-weight:bold">Tanggal</td>
      <td style="padding:8px">{now.strftime('%d %B %Y')}</td></tr>
  <tr><td style="padding:8px;background:#f1f5f9;font-weight:bold">Waktu</td>
      <td style="padding:8px">{now.strftime('%H:%M:%S')}</td></tr>
  <tr><td style="padding:8px;background:#f1f5f9;font-weight:bold">Tipe</td>
      <td style="padding:8px">{label}</td></tr>
  <tr><td style="padding:8px;background:#f1f5f9;font-weight:bold">Ukuran File</td>
      <td style="padding:8px">{db_size_kb:.1f} KB</td></tr>
  <tr><td style="padding:8px;background:#f1f5f9;font-weight:bold">File</td>
      <td style="padding:8px">{backup_filename}</td></tr>
</table>
<p style="color:#666;font-size:13px;margin-top:20px">
  File database terlampir. Simpan di tempat yang aman.<br>
  Untuk restore: ganti file <code>ipos.db</code> di folder backend dengan file ini.
</p>
<p style="color:#10b981;font-size:12px">— iPos 5.0 Backup System</p>
</body></html>
"""
        msg.attach(MIMEText(body, "html"))

        # Attach file database
        with open(tmp_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{backup_filename}"')
        msg.attach(part)

        # Kirim via SMTP
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.sendmail(cfg["smtp_user"], cfg["backup_email"], msg.as_string())

        return {
            "success": True,
            "message": f"Backup berhasil dikirim ke {cfg['backup_email']}",
            "filename": backup_filename,
            "size_kb": round(db_size_kb, 1),
            "sent_at": now.isoformat()
        }

    except smtplib.SMTPAuthenticationError:
        return {"success": False,
                "message": "Autentikasi Gmail gagal. Pastikan App Password benar dan 2FA aktif."}
    except smtplib.SMTPConnectError:
        return {"success": False,
                "message": f"Tidak bisa terhubung ke {cfg['smtp_host']}:{cfg['smtp_port']}"}
    except smtplib.SMTPException as e:
        return {"success": False, "message": f"SMTP Error: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}
    finally:
        # Hapus file tmp
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ─── Auto backup scheduler ────────────────────────────────────────────────────

_last_auto_backup_date: Optional[str] = None

def check_and_run_auto_backup():
    """
    Dipanggil dari loop di main.py setiap menit.
    Jalankan backup jika sudah jam yang ditentukan dan belum backup hari ini.
    """
    global _last_auto_backup_date
    cfg = load_email_config()
    if not cfg.get("enabled"):
        return

    now = datetime.now()
    today_str = date.today().isoformat()

    if (now.hour == cfg.get("auto_backup_hour", 21) and
            _last_auto_backup_date != today_str):
        _last_auto_backup_date = today_str
        result = send_backup_email("Auto Harian")
        if result["success"]:
            print(f"✓ Auto email backup: {result['message']}")
        else:
            print(f"⚠ Auto email backup gagal: {result['message']}")


# ─── Routes ───────────────────────────────────────────────────────────────────

class EmailConfigIn(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    backup_email: Optional[str] = None
    auto_backup_hour: Optional[int] = None
    enabled: Optional[bool] = None


@router.get("/config")
def get_config(_=Depends(require_admin)):
    cfg = load_email_config()
    # Sembunyikan password sebagian
    if cfg.get("smtp_pass"):
        p = cfg["smtp_pass"]
        cfg["smtp_pass_masked"] = p[:4] + "****" + p[-4:] if len(p) > 8 else "****"
    cfg.pop("smtp_pass", None)
    return cfg


@router.post("/config")
def update_config(data: EmailConfigIn, _=Depends(require_admin)):
    current = load_email_config()
    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    current.update(updates)
    save_email_config(current)
    return {"message": "Konfigurasi email disimpan"}


@router.post("/test")
def test_email(_=Depends(require_admin)):
    """Test kirim email dengan backup kecil"""
    cfg = load_email_config()
    if not cfg.get("smtp_user") or not cfg.get("smtp_pass"):
        raise HTTPException(400, "Konfigurasi email belum diisi")
    result = send_backup_email("Test Koneksi")
    if not result["success"]:
        raise HTTPException(400, result["message"])
    return result


@router.post("/send")
def manual_backup(
    background_tasks: BackgroundTasks,
    _=Depends(require_admin)
):
    """Kirim backup manual sekarang (background task)"""
    cfg = load_email_config()
    if not cfg.get("smtp_user") or not cfg.get("smtp_pass") or not cfg.get("backup_email"):
        raise HTTPException(400, "Konfigurasi email belum lengkap. Buka Settings → Email Backup")

    # Langsung jalankan (bukan background, agar bisa dapat error)
    result = send_backup_email("Manual")
    if not result["success"]:
        raise HTTPException(500, result["message"])
    return result


@router.post("/send-before-update")
def backup_before_update(_=Depends(require_admin)):
    """
    Backup ke email sebelum update versi.
    Harus berhasil sebelum update bisa dilanjutkan.
    """
    result = send_backup_email("Pre-Update Backup")
    if not result["success"]:
        raise HTTPException(500,
            f"Backup gagal, update dibatalkan. Alasan: {result['message']}")
    return {
        **result,
        "safe_to_update": True,
        "message": result["message"] + " — Aman untuk melanjutkan update."
    }


@router.get("/status")
def backup_status(_=Depends(get_current_user)):
    cfg = load_email_config()
    return {
        "enabled": cfg.get("enabled", False),
        "configured": bool(cfg.get("smtp_user") and cfg.get("smtp_pass") and cfg.get("backup_email")),
        "backup_email": cfg.get("backup_email", ""),
        "auto_backup_hour": cfg.get("auto_backup_hour", 21),
        "last_auto_backup": _last_auto_backup_date
    }
