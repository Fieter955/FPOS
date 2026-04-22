import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import models, schemas, auth as auth_utils

router = APIRouter()

# ─── MENGAMBIL DATA (Semua user yang login boleh melihat) ───
@router.get("/", response_model=List[schemas.BranchOut])
def get_branches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.get_current_user)):
    branches = db.query(models.Branch).offset(skip).limit(limit).all()
    return branches


# ─── MENAMBAH DATA (Hanya Admin) ───
# ─── MENAMBAH DATA (Hanya Admin) ───
@router.post("/", response_model=schemas.BranchOut)
def create_branch(branch: schemas.BranchCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    branch_data = branch.model_dump()
    
    # 🛡️ AUTO-GENERATE KODE CABANG
    auto_code = f"CBG-{uuid.uuid4().hex[:4].upper()}"
    
    # Pastikan kode benar-benar unik (menghindari tabrakan di masa depan)
    while db.query(models.Branch).filter(models.Branch.code == auto_code).first():
        auto_code = f"CBG-{uuid.uuid4().hex[:4].upper()}"
        
    branch_data["code"] = auto_code # Timpa inputan "AUTO" dari frontend
        
    # A. Simpan Cabang Baru
    new_branch = models.Branch(**branch_data)
    db.add(new_branch)
    db.commit()
    db.refresh(new_branch)

    # B. Otomatis Bangun Gudang Fisik untuk Cabang Baru Ini
    new_warehouse = models.Warehouse(
        code=f"WH-{auto_code}",
        name=f"Etalase {new_branch.name}",
        branch_id=new_branch.id,
        is_active=True,
        is_default=True # 🚀 REVISI: DIUBAH MENJADI TRUE
    )
    db.add(new_warehouse)
    db.commit()

    return new_branch

# ─── MENGUBAH DATA (Hanya Admin) ───
@router.put("/{branch_id}", response_model=schemas.BranchOut)
def update_branch(branch_id: int, branch: schemas.BranchUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    db_branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if not db_branch:
        raise HTTPException(status_code=404, detail="Cabang tidak ditemukan.")
        
    update_data = branch.model_dump(exclude_unset=True)
    
    # 🛡️ KUNCI KODE AGAR TIDAK BISA DIUBAH
    if "code" in update_data:
        del update_data["code"]
        
    for key, value in update_data.items():
        setattr(db_branch, key, value)
        
    db.commit()
    db.refresh(db_branch)
    return db_branch


# ─── MENGHAPUS / NON-AKTIF (Hanya Admin) ───
@router.delete("/{branch_id}")
def delete_branch(branch_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    db_branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if not db_branch:
        raise HTTPException(status_code=404, detail="Cabang tidak ditemukan.")
        
    # Non-aktifkan cabang (Safe delete)
    db_branch.is_active = False
    
    # Opsi Tambahan: Non-aktifkan juga gudangnya agar tidak bisa transaksi
    gudang_terkait = db.query(models.Warehouse).filter(models.Warehouse.branch_id == branch_id).all()
    for gudang in gudang_terkait:
        gudang.is_active = False
        
    db.commit()
    return {"message": "Cabang dan gudang terkait dinonaktifkan."}