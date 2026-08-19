from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import pytz # 👈 TAMBAHAN IMPORT

from .config import settings
from .database import get_db
from . import models
from .permissions import has_role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# --- Setup Zona Waktu Lokal (WITA / Bali) ---
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    """Mengambil tanggal akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA).date()

def get_local_datetime():
    """Mengambil tanggal & jam akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA)

# ─── Password (direct bcrypt, no passlib) ─────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

# ─── JWT ──────────────────────────────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    # 👇 UBAH: Gunakan get_local_datetime() untuk JWT Expiry
    expire = get_local_datetime() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# 👇 REVISI FUNGSI GET_CURRENT_USER 👇
def get_current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token tidak valid atau sudah expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
        
    # 🔥 LOGIKA PENCEGAT CABANG SECARA GLOBAL 🔥
    requested_branch = request.headers.get("X-Branch-ID")
    
    if has_role(user, "admin"):
        if requested_branch: # Jika admin pilih cabang tertentu
            try:
                b_id = int(requested_branch)
                # 🛡️ VALIDASI: Pastikan cabang benar-benar ada di database
                exists = db.query(models.Branch.id).filter(models.Branch.id == b_id).first()
                if exists:
                    user.active_branch_id = b_id
                else:
                    # Jika ID hantu (misal dari localStorage lama), balikkan ke default
                    user.active_branch_id = user.branch_id or 1
            except (ValueError, TypeError):
                user.active_branch_id = user.branch_id or 1
        else: # Jika admin pilih "-- Semua Cabang --"
            user.active_branch_id = None 
    else:
        # Jika Kasir, HARGA MATI pakai cabang tempat dia ditugaskan (atau default 1)
        user.active_branch_id = user.branch_id or 1
        
    return user


def require_admin(request: Request, current_user: models.User = Depends(get_current_user)):
    # Middleware hak akses menandai request yang sudah lolos izin modul. Ini
    # mempertahankan dependency lama tanpa membuat role kustom selalu ditolak.
    permission_authorized = bool(getattr(request.state, "permission_authorized", False))
    if not has_role(current_user, "admin") and not permission_authorized:
        raise HTTPException(status_code=403, detail="Akses ditolak: hanya admin")
    return current_user

def write_audit(db: Session, user_id: int, action: str, table: str,
                record_id: Optional[int] = None, detail: Optional[str] = None):
    try:
        log = models.AuditLog(
            user_id=user_id, action=action,
            table_name=table, record_id=record_id,
            detail=detail,
            created_at=get_local_datetime() # 👈 Paksa pakai WITA agar log akurat di zona waktu Bali
        )
        db.add(log)
        db.flush()
    except Exception as e:
        # Jangan diam-diam menelan kegagalan audit — jejak keamanan bisa hilang.
        print(f"⚠️ Gagal menulis audit log (user={user_id}, action={action}, table={table}): {e}")


# 👇 INI DIA FUNGSI AJAIBNYA! (Tambahkan di paling bawah auth.py) 👇
def get_query(db: Session, model, current_user: models.User):
    q = db.query(model)
    
    if current_user.active_branch_id is not None:
        if hasattr(model, 'branch_id'):
            # Filter berdasarkan cabang asal (branch_id)
            criteria = (model.branch_id == current_user.active_branch_id) | (model.branch_id == None)
            
            # Kasus khusus Pembelian/PO: Jika cabang ini adalah TUJUAN (target_branch_id), dia juga harus bisa melihatnya
            if hasattr(model, 'target_branch_id'):
                criteria |= (model.target_branch_id == current_user.active_branch_id)
                
            q = q.filter(criteria)
            
    return q
