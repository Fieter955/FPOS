"""
iPos 5.0 — Sistem Lisensi
- Trial 30 hari penuh
- Aktivasi via license key
- Hardware binding (MAC address)
- Plan: trial | basic | pro | ultimate
"""
import hashlib, uuid, platform, subprocess
from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, require_admin
from .. import models

router = APIRouter()

TRIAL_DAYS = 30

# ─── Plans ────────────────────────────────────────────────────────────────────
PLANS = {
    "trial":    {"max_users": 2,  "features": ["pos","items","sales","purchases","reports"]},
    "basic":    {"max_users": 3,  "features": ["pos","items","sales","purchases","reports","inventory","customers","suppliers"]},
    "pro":      {"max_users": 10, "features": ["all"]},
    "ultimate": {"max_users": 99, "features": ["all"]},
}


def get_hardware_id() -> str:
    """Generate hardware fingerprint dari MAC address"""
    try:
        mac = uuid.getnode()
        system = platform.system()
        node = platform.node()
        raw = f"{mac}-{system}-{node}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
    except Exception:
        return "unknown-hardware"


def generate_license_key() -> str:
    """Generate license key format: IPOS-XXXX-XXXX-XXXX-XXXX"""
    parts = [uuid.uuid4().hex[:4].upper() for _ in range(4)]
    return "IPOS-" + "-".join(parts)


def get_or_create_trial(db: Session) -> models.License:
    """Dapatkan atau buat trial license"""
    lic = db.query(models.License).first()
    if not lic:
        hw_id = get_hardware_id()
        lic = models.License(
            license_key=f"TRIAL-{hw_id[:8].upper()}",
            hardware_id=hw_id,
            plan="trial",
            status="active",
            max_users=PLANS["trial"]["max_users"],
            expires_at=datetime.utcnow() + timedelta(days=TRIAL_DAYS)
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return lic


# ─── Routes ───────────────────────────────────────────────────────────────────
@router.get("/status")
def get_license_status(db: Session = Depends(get_db)):
    """Cek status lisensi — bisa diakses tanpa auth untuk gatekeeping"""
    lic = get_or_create_trial(db)
    now = datetime.utcnow()
    
    is_expired = lic.expires_at and lic.expires_at < now if lic.expires_at else False
    days_left = None
    if lic.expires_at:
        delta = lic.expires_at - now
        days_left = max(0, delta.days)

    plan_info = PLANS.get(lic.plan, PLANS["trial"])

    return {
        "plan": lic.plan,
        "status": "expired" if is_expired else lic.status,
        "is_expired": is_expired,
        "is_trial": lic.plan == "trial",
        "days_left": days_left,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "owner_name": lic.owner_name,
        "owner_email": lic.owner_email,
        "max_users": lic.max_users,
        "features": plan_info["features"],
        "hardware_id": lic.hardware_id,
        "license_key": lic.license_key if lic.plan != "trial" else None,
        "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        
        # 👇 INI KUNCI JAWABANNYA: TAMBAHKAN 2 BARIS INI 👇
        "billing_status": getattr(lic, 'billing_status', 'ok'),
        "billing_message": getattr(lic, 'billing_message', 'Aplikasi berjalan normal.')
    }


@router.post("/activate")
def activate_license(data: dict, db: Session = Depends(get_db)):
    """
    Aktivasi lisensi dengan key.
    Format key: IPOS-XXXX-XXXX-XXXX-XXXX
    
    Untuk implementasi production: validasi key ke server lisensi online.
    Untuk sekarang: validasi format + simpan lokal.
    """
    key = data.get("license_key", "").strip().upper()
    owner_name = data.get("owner_name", "")
    owner_email = data.get("owner_email", "")

    if not key:
        raise HTTPException(400, "License key wajib diisi")

    # Validasi format key
    parts = key.split("-")
    if len(parts) != 5 or parts[0] != "IPOS" or not all(len(p) == 4 for p in parts[1:]):
        raise HTTPException(400, "Format license key tidak valid. Format: IPOS-XXXX-XXXX-XXXX-XXXX")

    # Cek apakah key sudah dipakai
    existing = db.query(models.License).filter(
        models.License.license_key == key
    ).first()
    if existing and existing.status == "active" and existing.plan != "trial":
        raise HTTPException(400, "License key sudah aktif")

    # Untuk production: di sini call ke server lisensi online kamu
    # Untuk sekarang: determine plan dari karakter key (demo logic)
    # Key yang diawali IPOS-PRO  → pro
    # Key yang diawali IPOS-ULT  → ultimate  
    # Lainnya → basic
    key_prefix = parts[1][:3]
    if key_prefix == "PRO":
        plan = "pro"
        max_users = 10
        expires = datetime.utcnow() + timedelta(days=365)
    elif key_prefix == "ULT":
        plan = "ultimate"
        max_users = 99
        expires = None  # lifetime
    else:
        plan = "basic"
        max_users = 3
        expires = datetime.utcnow() + timedelta(days=365)

    hw_id = get_hardware_id()

    # Update atau buat license
    lic = db.query(models.License).first()
    if lic:
        lic.license_key = key
        lic.hardware_id = hw_id
        lic.plan = plan
        lic.status = "active"
        lic.owner_name = owner_name
        lic.owner_email = owner_email
        lic.max_users = max_users
        lic.expires_at = expires
        lic.activated_at = datetime.utcnow()
    else:
        lic = models.License(
            license_key=key, hardware_id=hw_id,
            plan=plan, status="active",
            owner_name=owner_name, owner_email=owner_email,
            max_users=max_users, expires_at=expires
        )
        db.add(lic)

    db.commit()
    db.refresh(lic)

    return {
        "success": True,
        "message": f"Lisensi {plan.upper()} berhasil diaktifkan!",
        "plan": plan,
        "max_users": max_users,
        "expires_at": expires.isoformat() if expires else "Seumur Hidup"
    }


@router.get("/hardware-id")
def get_hw_id():
    """Return hardware ID untuk keperluan generate license key"""
    return {"hardware_id": get_hardware_id()}


@router.post("/generate-key")
def generate_key(data: dict, _=Depends(require_admin)):
    """
    Generate license key (untuk penjual).
    Hanya admin yang bisa generate.
    """
    plan = data.get("plan", "basic")
    if plan not in PLANS:
        raise HTTPException(400, f"Plan tidak valid: {list(PLANS.keys())}")
    
    prefix_map = {"basic": "BAS", "pro": "PRO", "ultimate": "ULT", "trial": "TRL"}
    prefix = prefix_map.get(plan, "BAS")
    
    parts = [prefix + uuid.uuid4().hex[:1].upper()] + \
            [uuid.uuid4().hex[:4].upper() for _ in range(3)]
    key = "IPOS-" + "-".join(parts)
    
    return {
        "license_key": key,
        "plan": plan,
        "max_users": PLANS[plan]["max_users"],
        "note": "Berikan key ini ke pembeli untuk aktivasi"
    }


from fastapi import UploadFile, File
import os
import shutil

# ... (Biarkan kode lama Anda tetap di atas) ...

# ─── ENDPOINT UNTUK FIETER MENGUPLOAD BUKTI TRANSFER ───
@router.post("/upload-proof")
async def upload_payment_proof(
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    """Menerima foto bukti transfer dari pemilik toko"""
    lic = get_or_create_trial(db)
    
    # Buat folder khusus untuk menyimpan bukti transfer
    upload_dir = "dist/uploads/billing"
    os.makedirs(upload_dir, exist_ok=True)
    
    # Simpan file gambar
    file_extension = file.filename.split(".")[-1]
    safe_filename = f"proof_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file_extension}"
    file_path = os.path.join(upload_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Catat di database
    payment = models.LicensePayment(
        license_id=lic.id,
        proof_image_path=f"/uploads/billing/{safe_filename}",
        status="pending",
        notes="Di-upload oleh sistem"
    )
    db.add(payment)
    
    # Ubah status billing menjadi "menunggu verifikasi" agar tidak diblokir sementara
    lic.billing_message = "Bukti transfer sedang diproses oleh Developer."
    db.commit()
    
    return {"success": True, "message": "Bukti pembayaran berhasil diupload!"}

# ─── ENDPOINT RAHASIA UNTUK ANDA (DEVELOPER) ───
@router.post("/developer/kill-switch")
def trigger_kill_switch(data: dict, db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    """
    Tombol dewa untuk mengubah status aplikasi klien.
    Hanya bisa diakses jika role user adalah 'superadmin'
    """
   
# 👇 UBAH BAGIAN INI 👇
    if user.username.lower() != "fieter":
        raise HTTPException(403, "Akses ditolak! Hanya Pemilik Sistem yang bisa melakukan ini.")
    # 👆 SAMPAI SINI 👆
        
    action = data.get("action") # "warning" atau "block" atau "ok"
    pesan = data.get("message", "Silakan selesaikan pembayaran Anda.")
    
    lic = get_or_create_trial(db)
    
    if action == "warning":
        lic.billing_status = "warning"
        lic.billing_message = pesan
    elif action == "block":
        lic.billing_status = "blocked"
        lic.billing_message = pesan
    elif action == "ok":
        lic.billing_status = "ok"
        lic.billing_message = "Aplikasi berjalan normal."
        
    db.commit()
    return {"success": True, "message": f"Kill Switch diset ke: {action.upper()}"}