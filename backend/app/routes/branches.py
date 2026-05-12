import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date

from ..database import get_db
from .. import models, schemas, auth as auth_utils
from .accounting import create_auto_journal, get_local_date

router = APIRouter()

# ─── SETORAN KE PUSAT ───

@router.get("/pending-deposits")
def get_pending_deposits(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = "pending", # 'pending' (belum setor) atau 'deposited' (sudah)
    target_branch_id: Optional[int] = None, # Untuk monitoring pusat
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(auth_utils.get_current_user)
):
    active_b_id = current_user.active_branch_id
    
    # Logic: Jika Pusat (ID 1) dan tidak ada target_branch_id, berikan ringkasan per cabang
    if active_b_id == 1 and not target_branch_id:
        # Ringkasan: Cabang Mana saja yang punya kas berdasarkan status
        branches = db.query(models.Branch).filter(models.Branch.id != 1).all()
        summary = []
        overall_total = 0
        
        is_dep_filter = (status == "deposited")
        
        for b in branches:
            q = db.query(models.Shift).filter(
                models.Shift.branch_id == b.id, 
                models.Shift.status == "closed",
                models.Shift.is_deposited == is_dep_filter
            )
            if start_date: q = q.filter(func.date(models.Shift.closed_at) >= start_date)
            if end_date: q = q.filter(func.date(models.Shift.closed_at) <= end_date)
            
            shifts = q.all()
            total_b = sum(s.closing_cash for s in shifts if s.closing_cash)
            if total_b > 0:
                summary.append({
                    "branch_id": b.id,
                    "branch_name": b.name,
                    "branch_code": b.code,
                    "total_amount": total_b, # Diganti dari total_pending agar lebih umum
                    "shift_count": len(shifts)
                })
                overall_total += total_b
        
        return {
            "is_summary": True,
            "status": status,
            "total": overall_total,
            "branches": summary
        }

    # Jika sub-cabang atau Pusat sedang memfilter cabang tertentu
    b_id = target_branch_id if (active_b_id == 1 and target_branch_id) else active_b_id
    
    if not b_id:
        return {"total": 0, "shifts": []}
        
    query = db.query(models.Shift).filter(models.Shift.branch_id == b_id, models.Shift.status == "closed")
    
    if status == "deposited":
        query = query.filter(models.Shift.is_deposited == True)
    else:
        query = query.filter(models.Shift.is_deposited == False)
        
    if start_date:
        query = query.filter(func.date(models.Shift.closed_at) >= start_date)
    if end_date:
        query = query.filter(func.date(models.Shift.closed_at) <= end_date)
        
    shifts = query.order_by(models.Shift.closed_at.desc()).all()
    
    total_val = sum(s.closing_cash for s in shifts if s.closing_cash)
    
    return {
        "is_summary": False,
        "total": total_val,
        "shifts": [{
            "id": s.id,
            "number": f"SHIFT-{s.id}",
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "closing_cash": s.closing_cash,
            "user": s.user.full_name if s.user else "System",
            "is_deposited": s.is_deposited
        } for s in shifts]
    }

@router.get("/deposits")
def get_branch_deposits(
    branch_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth_utils.get_current_user)
):
    active_b_id = current_user.active_branch_id
    
    query = db.query(models.BranchDeposit)
    
    # Security: Sub-cabang hanya boleh lihat punya sendiri
    if active_b_id != 1:
        query = query.filter(models.BranchDeposit.branch_id == active_b_id)
    elif branch_id:
        query = query.filter(models.BranchDeposit.branch_id == branch_id)
        
    if start_date:
        query = query.filter(models.BranchDeposit.date >= start_date)
    if end_date:
        query = query.filter(models.BranchDeposit.date <= end_date)
        
    deposits = query.order_by(models.BranchDeposit.created_at.desc()).all()
    
    # Return data with branch name
    result = []
    for d in deposits:
        result.append({
            "id": d.id,
            "date": d.date.isoformat(),
            "amount": d.amount,
            "cash_amount": d.cash_amount,
            "bank_amount": d.bank_amount,
            "notes": d.notes,
            "branch_name": d.branch.name if d.branch else "Unknown",
            "bank_account_name": d.bank_account.name if d.bank_account else None,
            "created_at": d.created_at.isoformat()
        })
        
    return result

@router.post("/deposit")
def process_branch_deposit(deposit: schemas.BranchDepositCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth_utils.get_current_user)):
    b_id = current_user.active_branch_id
    if not b_id:
        raise HTTPException(400, "User tidak terikat ke cabang mana pun.")
    
    if deposit.amount <= 0:
        raise HTTPException(400, "Nominal setoran harus lebih besar dari 0.")
    
    if round(deposit.cash_amount + deposit.bank_amount, 2) != round(deposit.amount, 2):
        raise HTTPException(400, "Total Kas + Bank tidak sama dengan Total Setoran.")
        
    # 1. Cari shift yang belum disetor
    shifts = db.query(models.Shift).filter(
        models.Shift.branch_id == b_id,
        models.Shift.status == "closed",
        models.Shift.is_deposited == False
    ).with_for_update().all()
    
    # 2. Buat Transaksi Setoran
    new_deposit = models.BranchDeposit(
        branch_id=b_id,
        date=get_local_date(),
        amount=deposit.amount,
        cash_amount=deposit.cash_amount,
        bank_amount=deposit.bank_amount,
        bank_account_id=deposit.bank_account_id,
        notes=deposit.notes
    )
    db.add(new_deposit)
    db.flush()
    
    # 3. Buat Jurnal Otomatis
    # Debit: 3-2300 Setoran ke Pusat
    # Credit: 1-1100 Kas (jika ada)
    # Credit: Bank Account (jika ada)
    
    entries = [{"code": "3-2300", "debit": deposit.amount, "credit": 0}]
    
    if deposit.cash_amount > 0:
        entries.append({"code": "1-1100", "debit": 0, "credit": deposit.cash_amount})
        
    if deposit.bank_amount > 0:
        if not deposit.bank_account_id:
            raise HTTPException(400, "Akun Bank harus dipilih jika ada nominal Bank.")
        bank_acc = db.query(models.Account).get(deposit.bank_account_id)
        if not bank_acc:
            raise HTTPException(404, "Akun Bank tidak ditemukan.")
        entries.append({"code": bank_acc.code, "debit": 0, "credit": deposit.bank_amount})
    
    journal = create_auto_journal(
        db=db,
        date_val=get_local_date(),
        number_ref=f"DEP-{new_deposit.id}",
        description=f"Setoran Gabungan (Kas+Bank) ke Pusat: {deposit.notes or ''}",
        entries=entries,
        user_id=current_user.id,
        branch_id=b_id
    )
    
    new_deposit.journal_id = journal.id

    # 5. JURNAL TIMBAL BALIK UNTUK PUSAT (BRANCH 1)
    # Jika yang menyetor bukan Pusat sendiri, maka buat jurnal di sisi Pusat
    if b_id != 1:
        branch_sending = db.query(models.Branch).get(b_id)
        branch_name = branch_sending.name if branch_sending else "Cabang"
        
        pusat_entries = [{"code": "3-2400", "debit": 0, "credit": deposit.amount}] # Kredit: Setoran dari Cabang
        
        if deposit.cash_amount > 0:
            pusat_entries.append({"code": "1-1100", "debit": deposit.cash_amount, "credit": 0}) # Debit: Kas Pusat
            
        if deposit.bank_amount > 0:
            bank_acc = db.query(models.Account).get(deposit.bank_account_id)
            pusat_entries.append({"code": bank_acc.code, "debit": deposit.bank_amount, "credit": 0}) # Debit: Bank Pusat
            
        create_auto_journal(
            db=db,
            date_val=get_local_date(),
            number_ref=f"DEP-{new_deposit.id}",
            description=f"Terima Setoran dari cabang {branch_name}: {deposit.notes or ''}",
            entries=pusat_entries,
            user_id=current_user.id,
            branch_id=1 # DIPAKSA KE BRANCH 1 (PUSAT)
        )
    
    # 6. Tandai Shift sebagai sudah disetor
    for s in shifts:
        s.is_deposited = True
        s.deposit_id = new_deposit.id
        
    db.commit()
    return {"message": "Setoran gabungan berhasil diproses.", "deposit_id": new_deposit.id}

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