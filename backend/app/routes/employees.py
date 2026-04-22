from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import models, schemas, auth as auth_utils

router = APIRouter()

# ─── API UNTUK ROLE (Hanya Admin) ───
@router.get("/roles", response_model=List[schemas.RoleOut])
def get_roles(db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.get_current_user)):
    return db.query(models.Role).all()

@router.post("/roles", response_model=schemas.RoleOut)
def create_role(role: schemas.RoleCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    if db.query(models.Role).filter(models.Role.name == role.name).first():
        raise HTTPException(status_code=400, detail="Jenis role ini sudah ada.")
    new_role = models.Role(name=role.name)
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role

@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    role = db.query(models.Role).filter(models.Role.id == role_id).first()
    if not role: raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    db.delete(role)
    db.commit()
    return {"message": "Role berhasil dihapus"}


# ─── API UNTUK PEGAWAI (Hanya Admin) ───
@router.get("/", response_model=List[schemas.UserOut])
def get_employees(db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    return db.query(models.User).all()

@router.post("/", response_model=schemas.UserOut)
def create_employee(user_in: schemas.UserCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    if db.query(models.User).filter(models.User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="Username sudah dipakai orang lain.")
    
    new_user = models.User(
        username=user_in.username,
        full_name=user_in.full_name,
        hashed_password=auth_utils.get_password_hash(user_in.password),
        role=user_in.role,
        branch_id=user_in.branch_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.put("/{user_id}", response_model=schemas.UserOut)
def update_employee(user_id: int, user_in: schemas.UserUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="Pegawai tidak ditemukan")
    
    if user_in.full_name is not None: user.full_name = user_in.full_name
    if user_in.role is not None: user.role = user_in.role
    if user_in.branch_id is not None: user.branch_id = user_in.branch_id
    if user_in.is_active is not None: user.is_active = user_in.is_active
    
    # Jika password diisi, berarti admin mereset passwordnya
    if user_in.password: 
        user.hashed_password = auth_utils.get_password_hash(user_in.password)
        
    db.commit()
    db.refresh(user)
    return user