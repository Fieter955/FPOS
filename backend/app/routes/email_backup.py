import os, smtplib, shutil, json
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional

from ..auth import require_admin, get_current_user
from ..config import settings
from ..database import engine

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(BASE_DIR, "..", "..", "email_override.json")
DB_PATH = os.path.join(BASE_DIR, "..", "..", "ipos.db")

# ─── Config Helpers ───────────────────────────────────────────────────────────

def load_email_config() -> dict:
    base = {
        "smtp_host": getattr(settings, "SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": getattr(settings, "SMTP_PORT", 587),
        "smtp_user": getattr(settings, "SMTP_USER", ""),
        "smtp_pass": getattr(settings, "SMTP_PASS", ""),
        "backup_email": getattr(settings, "BACKUP_EMAIL", ""),
        "auto_backup_hour": getattr(settings, "AUTO_BACKUP_HOUR", 1),
        "enabled": getattr(settings, "EMAIL_BACKUP_ENABLED", True),
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
    with open(_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# ─── Email Sender Logic ───────────────────────────────────────────────────────

def send_backup_email(label: str = "Manual") -> dict:
    cfg = load_email_config()
    
    if not cfg["smtp_user"] or not cfg["smtp_pass"] or not cfg["backup_email"]:
        return {"success": False, "message": "Konfigurasi Email belum lengkap di .env"}
        
    if not os.path.exists(DB_PATH):
        return {"success": False, "message": "File database (ipos.db) tidak ditemukan"}

    now = datetime.now()
    backup_filename = f"ipos_backup_{now.strftime('%Y%m%d_%H%M%S')}_{label}.db"
    tmp_path = DB_PATH + ".tmp"
    
    try:
        # Salin DB agar aman dikirim
        shutil.copy2(DB_PATH, tmp_path)
        db_size_kb = os.path.getsize(tmp_path) / 1024

        # Siapkan Pesan Email
        msg = MIMEMultipart()
        msg['From'] = cfg["smtp_user"]
        msg['To'] = cfg["backup_email"]
        msg['Subject'] = f"[iPos 5.0] Backup Database {label} - {now.strftime('%d %b %Y')}"
        
        body = f"Berikut adalah backup database otomatis dari sistem iPos 5.0.\nWaktu: {now.strftime('%Y-%m-%d %H:%M:%S')}\nUkuran: {round(db_size_kb, 1)} KB"
        msg.attach(MIMEText(body, 'plain'))

        # Siapkan Lampiran (Attachment)
        with open(tmp_path, "rb") as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f"attachment; filename= {backup_filename}")
            msg.attach(part)

        # Kirim Email via SMTP
        server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"])
        server.starttls()
        server.login(cfg["smtp_user"], cfg["smtp_pass"])
        server.send_message(msg)
        server.quit()

        return {
            "success": True,
            "message": f"Backup berhasil dikirim ke {cfg['backup_email']}",
            "filename": backup_filename,
            "size_kb": round(db_size_kb, 1),
            "sent_at": now.isoformat()
        }

    except Exception as e:
        return {"success": False, "message": f"Email Error: {str(e)}"}
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

# ─── Auto backup scheduler ────────────────────────────────────────────────────

_last_auto_backup_date: Optional[str] = None

def check_and_run_auto_backup():
    global _last_auto_backup_date
    cfg = load_email_config()
    if not cfg.get("enabled"): return

    now = datetime.now()
    today_str = date.today().isoformat()

    if now.hour == int(cfg.get("auto_backup_hour", 1)) and _last_auto_backup_date != today_str:
        _last_auto_backup_date = today_str
        send_backup_email("Auto")

# ─── Routes ───────────────────────────────────────────────────────────────────

class EmailConfigIn(BaseModel):
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    backup_email: Optional[str] = None
    auto_backup_hour: Optional[int] = None
    enabled: Optional[bool] = None

@router.get("/config")
def get_config(_=Depends(require_admin)):
    return load_email_config()

@router.post("/config")
def update_config(data: EmailConfigIn, _=Depends(require_admin)):
    current = load_email_config()
    current.update(data.model_dump(exclude_unset=True))
    save_email_config(current)
    return {"message": "Konfigurasi Email diperbarui"}

@router.post("/test")
def test_email(_=Depends(require_admin)):
    result = send_backup_email("Test")
    if not result["success"]:
        raise HTTPException(400, result["message"])
    return result

@router.post("/import")
async def import_db(file: UploadFile = File(...), _=Depends(require_admin)):
    """Fitur untuk menimpa database dengan file dari luar."""
    if not file.filename.endswith(".db"):
        raise HTTPException(400, "Hanya menerima file .db")
    
    try:
        engine.dispose() # Putuskan koneksi DB aktif
        # Buat backup darurat sebelum ditimpa
        shutil.copy2(DB_PATH, DB_PATH + ".bak")
        
        with open(DB_PATH, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        return {"success": True, "message": "Restore sukses! Silakan restart server."}
    except Exception as e:
        raise HTTPException(500, str(e))