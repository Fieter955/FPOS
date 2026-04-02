from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from ..database import get_db
from .. import models, schemas, auth as auth_utils
from ..config import settings

router = APIRouter()

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

def check_brute_force(db: Session, username: str, ip: str):
    """Block login jika terlalu banyak percobaan gagal"""
    window = datetime.utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
    failed = db.query(func.count(models.LoginAttempt.id)).filter(
        models.LoginAttempt.username == username,
        models.LoginAttempt.success == False,
        models.LoginAttempt.created_at >= window
    ).scalar()
    if failed >= MAX_FAILED_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Terlalu banyak percobaan gagal. Coba lagi dalam {LOCKOUT_MINUTES} menit."
        )


@router.post("/login", response_model=schemas.Token)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    check_brute_force(db, form_data.username, ip)

    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    success = user and auth_utils.verify_password(form_data.password, user.hashed_password)

    # Catat percobaan login
    db.add(models.LoginAttempt(username=form_data.username, ip_address=ip, success=success))
    db.commit()

    if not success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Akun dinonaktifkan")

    token = auth_utils.create_access_token(data={"sub": user.username})
    auth_utils.write_audit(db, user.id, "LOGIN", "users", user.id, f"Login dari {ip}")
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "full_name": user.full_name, "role": user.role}
    }


@router.post("/register", response_model=schemas.UserOut)
def register(user_in: schemas.UserCreate, db: Session = Depends(get_db),
             current_user: models.User = Depends(auth_utils.require_admin)):
    if db.query(models.User).filter(models.User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="Username sudah digunakan")
    user = models.User(
        username=user_in.username, email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=auth_utils.get_password_hash(user_in.password),
        role=user_in.role
    )
    db.add(user); db.commit(); db.refresh(user)
    auth_utils.write_audit(db, current_user.id, "CREATE", "users", user.id, f"Buat user {user.username}")
    db.commit()
    return user


@router.get("/users", response_model=list[schemas.UserOut])
def get_users(db: Session = Depends(get_db), _=Depends(auth_utils.require_admin)):
    return db.query(models.User).all()

@router.get("/me", response_model=schemas.UserOut)
def get_me(current_user: models.User = Depends(auth_utils.get_current_user)):
    return current_user

@router.put("/users/{uid}/password")
def change_password(uid: int, data: dict, db: Session = Depends(get_db),
                    current_user: models.User = Depends(auth_utils.get_current_user)):
    if current_user.role != "admin" and current_user.id != uid:
        raise HTTPException(403, "Tidak diizinkan")
    user = db.query(models.User).get(uid)
    if not user: raise HTTPException(404, "User tidak ditemukan")
    user.hashed_password = auth_utils.get_password_hash(data["new_password"])
    db.commit()
    return {"message": "Password diubah"}

@router.get("/audit-log")
def get_audit_log(skip: int = 0, limit: int = 100,
                  db: Session = Depends(get_db), _=Depends(auth_utils.require_admin)):
    logs = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).offset(skip).limit(limit).all()
    return [{
        "id": l.id, "action": l.action, "table_name": l.table_name,
        "record_id": l.record_id, "detail": l.detail,
        "user": l.user.username if l.user else "-",
        "created_at": l.created_at.isoformat() if l.created_at else None
    } for l in logs]
