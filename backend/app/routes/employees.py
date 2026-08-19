from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import re

from ..database import get_db
from .. import models, schemas, auth as auth_utils
from ..permission_catalog import ACTION_LABELS, AVAILABLE_GRANTS, PERMISSION_CATALOG
from ..permissions import grant_payload, role_names

router = APIRouter()

# ─── API UNTUK ROLE (Hanya Admin) ───
@router.get("/roles", response_model=List[schemas.RoleOut])
def get_roles(db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.get_current_user)):
    roles = db.query(models.Role).all()
    return sorted(
        roles,
        key=lambda role: (
            0 if role.name == "admin" else 1 if role.name == "kasir" else 2,
            role.name,
        ),
    )

@router.post("/roles", response_model=schemas.RoleOut)
def create_role(role: schemas.RoleCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    name = role.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Nama role wajib diisi.")
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", name):
        raise HTTPException(
            status_code=400,
            detail="Nama role hanya boleh berisi huruf kecil, angka, garis bawah, atau tanda minus.",
        )
    if db.query(models.Role).filter(models.Role.name == name).first():
        raise HTTPException(status_code=400, detail="Jenis role ini sudah ada.")
    new_role = models.Role(
        name=name,
        description="[permissions-seeded] Role kustom FPOS",
    )
    db.add(new_role)
    db.flush()
    auth_utils.write_audit(
        db, current_user.id, "CREATE", "roles", new_role.id, f"Buat role {name}"
    )
    db.commit()
    db.refresh(new_role)
    return new_role

@router.put("/roles/{role_id}", response_model=schemas.RoleOut)
def update_role(role_id: int, role_in: schemas.RoleCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    role = db.query(models.Role).filter(models.Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    if role.name in {"admin", "kasir"}:
        raise HTTPException(status_code=400, detail="Role bawaan sistem tidak dapat diubah.")
    new_name = role_in.name.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", new_name):
        raise HTTPException(
            status_code=400,
            detail="Nama role hanya boleh berisi huruf kecil, angka, garis bawah, atau tanda minus.",
        )
    duplicate = db.query(models.Role).filter(models.Role.name == new_name, models.Role.id != role_id).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Jenis role ini sudah ada.")

    old_name = role.name
    for user in db.query(models.User).all():
        names = role_names(user)
        if old_name in names:
            user.role = ",".join(new_name if name == old_name else name for name in names)
    role.name = new_name
    auth_utils.write_audit(
        db, current_user.id, "UPDATE", "roles", role.id,
        f"Ubah nama role {old_name} menjadi {new_name}",
    )
    db.commit()
    db.refresh(role)
    return role

@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.require_admin)):
    role = db.query(models.Role).filter(models.Role.id == role_id).first()
    if not role: raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    if role.name in {"admin", "kasir"}:
        raise HTTPException(status_code=400, detail="Role bawaan sistem tidak dapat dihapus.")
    assigned = [user.username for user in db.query(models.User).all() if role.name in role_names(user)]
    if assigned:
        preview = ", ".join(assigned[:5])
        if len(assigned) > 5:
            preview += f", dan {len(assigned) - 5} lainnya"
        raise HTTPException(
            status_code=409,
            detail=f"Role masih dipakai oleh: {preview}. Ubah role pegawai terlebih dahulu.",
        )
    role_name = role.name
    db.delete(role)
    auth_utils.write_audit(
        db, current_user.id, "DELETE", "roles", role_id, f"Hapus role {role_name}"
    )
    db.commit()
    return {"message": "Role berhasil dihapus"}


@router.get("/permission-catalog")
def get_permission_catalog(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth_utils.require_admin),
):
    return {
        "actions": ACTION_LABELS,
        "categories": PERMISSION_CATALOG,
    }


@router.get("/roles/{role_id}/permissions")
def get_role_permissions(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth_utils.require_admin),
):
    role = db.query(models.Role).filter(models.Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    grants = set(AVAILABLE_GRANTS) if role.name == "admin" else {
        (row.permission_key, row.action) for row in role.permissions
    }
    return {
        "role": {"id": role.id, "name": role.name},
        "locked": role.name == "admin",
        "grants": grant_payload(grants),
    }


@router.put("/roles/{role_id}/permissions")
def update_role_permissions(
    role_id: int,
    data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth_utils.require_admin),
):
    role = db.query(models.Role).filter(models.Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    if role.name == "admin":
        raise HTTPException(status_code=400, detail="Izin Administrator selalu penuh dan tidak dapat diubah.")

    requested: set[tuple[str, str]] = set()
    raw_grants = data.get("grants")
    if not isinstance(raw_grants, dict):
        raise HTTPException(status_code=422, detail="grants harus berupa objek permission dan daftar aksi.")
    for key, actions in raw_grants.items():
        if not isinstance(actions, list):
            raise HTTPException(status_code=422, detail=f"Daftar aksi {key} tidak valid.")
        for action in actions:
            grant = (str(key), str(action))
            if grant not in AVAILABLE_GRANTS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Izin tidak tersedia atau tidak dikenal: {key}.{action}",
                )
            requested.add(grant)

    db.query(models.RolePermission).filter(
        models.RolePermission.role_id == role.id
    ).delete(synchronize_session=False)
    db.add_all(
        models.RolePermission(role_id=role.id, permission_key=key, action=action)
        for key, action in sorted(requested)
    )
    auth_utils.write_audit(
        db, current_user.id, "UPDATE", "role_permissions", role.id,
        f"Ubah hak akses role {role.name}: {len(requested)} grant",
    )
    db.commit()
    return {
        "message": "Hak akses berhasil disimpan",
        "grants": grant_payload(requested),
    }


# ─── API UNTUK PEGAWAI (Hanya Admin) ───
@router.get("/branches")
def get_employee_branch_options(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth_utils.require_admin),
):
    return [
        {
            "id": branch.id,
            "code": branch.code,
            "name": branch.name,
            "status": branch.status,
            "is_active": branch.is_active,
        }
        for branch in db.query(models.Branch).order_by(models.Branch.name).all()
    ]


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
