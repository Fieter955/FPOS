"""
iPos 5.0 — Akuntansi Lengkap
Fitur:
  - Chart of Accounts (Daftar Akun)
  - Jurnal Umum (double-entry)
  - Buku Besar per akun
  - Neraca Saldo (Trial Balance)
  - Neraca (Balance Sheet)
  - Laporan Laba Rugi (Income Statement)
  - Kas Masuk / Kas Keluar (tetap ada)
  - Auto-journal dari transaksi penjualan & pembelian
"""

from fastapi import APIRouter, Depends, HTTPException, Response
import pytz
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel

from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit
from .. import schemas

router = APIRouter()
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    return datetime.now(WITA).date()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def next_journal_number(db: Session) -> str:
    from datetime import date as d
    # Gunakan WITA juga di sini agar prefix tanggalnya akurat
    today_str = datetime.now(WITA).strftime('%Y%m%d')
    prefix = f"JU{today_str}"
    
    # KUNCI baris terakhir agar tidak ada proses lain yang mengambil nomor yang sama
    last = db.query(models.Journal).filter(
        models.Journal.number.like(f"{prefix}%")
    ).order_by(models.Journal.id.desc()).with_for_update().first() # <--- TAMBAHKAN LOCK
    
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def create_auto_journal(
    db: Session, 
    date_val: date, 
    number_ref: str, 
    description: str, 
    entries: list,
    user_id: int
):
    """Helper untuk membuat jurnal otomatis dari modul lain"""
    # 1. Validasi Balance
    total_debit = sum(e["debit"] for e in entries)
    total_credit = sum(e["credit"] for e in entries)
    if abs(total_debit - total_credit) > 0.01:
        raise ValueError(f"Jurnal tidak balance! Debit: {total_debit}, Kredit: {total_credit}")

    # 2. Buat Header Jurnal
    journal = models.Journal(
        number=next_journal_number(db),
        date=date_val,
        description=description,
        reference=number_ref,
        source="auto",  # <-- Penanda bahwa ini jurnal otomatis
        created_by=user_id
    )
    db.add(journal)
    db.flush() 

    # 3. Buat Baris Jurnal (Sesuai struktur database iPos 5.0!)
    for entry in entries:
        if entry["debit"] == 0 and entry["credit"] == 0:
            continue 
            
        account = db.query(models.Account).filter(models.Account.code == entry["code"]).first()
        if not account:
            write_audit(db, user_id, "ERROR", "journals", journal.id, f"Akun COA {entry['code']} tidak ditemukan")
            continue
            
        # Jika ada Debit, simpan ke debit_account_id dan isi AMOUNT-nya!
        if entry["debit"] > 0:
            db.add(models.JournalEntryLine(
                journal_id=journal.id,
                debit_account_id=account.id,
                credit_account_id=None,
                amount=entry["debit"],  # <-- INI YANG KEMARIN HILANG!
                description=description
            ))
            
        # Jika ada Kredit, simpan ke credit_account_id dan isi AMOUNT-nya!
        if entry["credit"] > 0:
            db.add(models.JournalEntryLine(
                journal_id=journal.id,
                debit_account_id=None,
                credit_account_id=account.id,
                amount=entry["credit"], # <-- INI YANG KEMARIN HILANG!
                description=description
            ))

def next_cash_number(db: Session) -> str:
    today = date.today()
    prefix = f"KAS{today.strftime('%Y%m%d')}"
    last = db.query(models.CashTransaction).filter(
        models.CashTransaction.number.like(f"{prefix}%")
    ).order_by(models.CashTransaction.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def get_account_balance(db: Session, account_id: int,
                         start_date: Optional[date] = None,
                         end_date: Optional[date] = None) -> float:
    """Hitung saldo akun berdasarkan jurnal entries"""
    account = db.query(models.Account).get(account_id)
    if not account:
        return 0.0

    q_debit = db.query(func.sum(models.JournalEntryLine.amount)).filter(
        models.JournalEntryLine.debit_account_id == account_id
    )
    q_credit = db.query(func.sum(models.JournalEntryLine.amount)).filter(
        models.JournalEntryLine.credit_account_id == account_id
    )

    if start_date or end_date:
        q_debit = q_debit.join(models.Journal)
        q_credit = q_credit.join(models.Journal)
        if start_date:
            q_debit = q_debit.filter(models.Journal.date >= start_date)
            q_credit = q_credit.filter(models.Journal.date >= start_date)
        if end_date:
            q_debit = q_debit.filter(models.Journal.date <= end_date)
            q_credit = q_credit.filter(models.Journal.date <= end_date)

    total_debit = (q_debit.scalar() or 0) + account.opening_balance
    total_credit = q_credit.scalar() or 0

    # Normal balance menentukan cara hitung saldo
    if account.normal_balance == "debit":
        return total_debit - total_credit
    else:
        return total_credit - total_debit


# ══════════════════════════════════════════════════════════════════════════════
# CHART OF ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════

class AccountCreate(BaseModel):
    code: str
    name: str
    type: str
    subtype: Optional[str] = None
    normal_balance: str = "debit"
    opening_balance: float = 0.0

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    subtype: Optional[str] = None
    normal_balance: Optional[str] = None
    opening_balance: Optional[float] = None
    is_active: Optional[bool] = None


@router.get("/accounts")
def get_accounts(
    type: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.Account)
    if active_only:
        q = q.filter(models.Account.is_active == True)
    if type:
        q = q.filter(models.Account.type == type)
    accounts = q.order_by(models.Account.code).all()
    return [{
        "id": a.id, "code": a.code, "name": a.name,
        "type": a.type, "subtype": a.subtype,
        "normal_balance": a.normal_balance,
        "opening_balance": a.opening_balance,
        "is_active": a.is_active,
        "current_balance": get_account_balance(db, a.id)
    } for a in accounts]


@router.post("/accounts")
def create_account(
    data: AccountCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if db.query(models.Account).filter(models.Account.code == data.code).first():
        raise HTTPException(400, "Kode akun sudah digunakan")

    valid_types = ["asset", "liability", "equity", "revenue", "expense"]
    if data.type not in valid_types:
        raise HTTPException(400, f"Tipe akun harus salah satu dari: {valid_types}")

    # Set normal balance otomatis berdasarkan tipe
    normal = "debit" if data.type in ["asset", "expense"] else "credit"

    obj = models.Account(
        code=data.code, name=data.name, type=data.type,
        subtype=data.subtype, normal_balance=normal,
        opening_balance=data.opening_balance
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    write_audit(db, current_user.id, "CREATE", "accounts", obj.id, f"Buat akun {obj.code} - {obj.name}")
    db.commit()
    return {"id": obj.id, "code": obj.code, "name": obj.name, "message": "Akun dibuat"}


@router.put("/accounts/{account_id}")
def update_account(
    account_id: int, data: AccountUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    obj = db.query(models.Account).get(account_id)
    if not obj:
        raise HTTPException(404, "Akun tidak ditemukan")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    write_audit(db, current_user.id, "UPDATE", "accounts", obj.id, f"Update akun {obj.code}")
    db.commit()
    return {"id": obj.id, "message": "Akun diperbarui"}


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    obj = db.query(models.Account).get(account_id)
    if not obj:
        raise HTTPException(404, "Akun tidak ditemukan")
    # Cek apakah akun sudah punya transaksi
    has_transactions = db.query(models.JournalEntryLine).filter(
        (models.JournalEntryLine.debit_account_id == account_id) |
        (models.JournalEntryLine.credit_account_id == account_id)
    ).first()
    if has_transactions:
        obj.is_active = False
        db.commit()
        return {"message": "Akun dinonaktifkan (ada transaksi terkait)"}
    db.delete(obj)
    db.commit()
    return {"message": "Akun dihapus"}


@router.post("/accounts/seed-default")
def seed_default_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Buat chart of accounts standar UMKM Indonesia"""
    if current_user.role != "admin":
        raise HTTPException(403, "Hanya admin")

    existing = db.query(func.count(models.Account.id)).scalar()
    if existing > 0:
        raise HTTPException(400, "Chart of accounts sudah ada")

    default_accounts = [
        # ASET LANCAR
        ("1-1100", "Kas", "asset", "current_asset", "debit"),
        ("1-1200", "Bank", "asset", "current_asset", "debit"),
        ("1-1300", "Piutang Usaha", "asset", "current_asset", "debit"),
        ("1-1400", "Persediaan Barang", "asset", "current_asset", "debit"),
        ("1-1500", "Uang Muka Pembelian", "asset", "current_asset", "debit"),
        ("1-1600", "Perlengkapan Toko", "asset", "current_asset", "debit"),
        # ASET TETAP
        ("1-2100", "Peralatan Toko", "asset", "fixed_asset", "debit"),
        ("1-2200", "Kendaraan", "asset", "fixed_asset", "debit"),
        ("1-2300", "Bangunan", "asset", "fixed_asset", "debit"),
        # KEWAJIBAN LANCAR
        ("2-1100", "Hutang Usaha", "liability", "current_liability", "credit"),
        ("2-1200", "Hutang PPN", "liability", "current_liability", "credit"),
        ("2-1300", "Uang Muka Penjualan", "liability", "current_liability", "credit"),
        ("2-1400", "Beban Masih Harus Dibayar", "liability", "current_liability", "credit"),
        # EKUITAS
        ("3-1100", "Modal Pemilik", "equity", "capital", "credit"),
        ("3-1200", "Prive / Pengambilan Pemilik", "equity", "capital", "debit"),
        ("3-1300", "Laba Ditahan", "equity", "retained_earnings", "credit"),
        # PENDAPATAN
        ("4-1100", "Penjualan", "revenue", "operating", "credit"),
        ("4-1200", "Retur Penjualan", "revenue", "operating", "debit"),
        ("4-1300", "Pendapatan Lain-lain", "revenue", "non_operating", "credit"),
        # HPP & BEBAN
        ("5-1100", "Harga Pokok Penjualan", "expense", "cogs", "debit"),
        ("5-2100", "Beban Gaji Karyawan", "expense", "operating", "debit"),
        ("5-2200", "Beban Sewa Toko", "expense", "operating", "debit"),
        ("5-2300", "Beban Listrik & Air", "expense", "operating", "debit"),
        ("5-2400", "Beban Perlengkapan", "expense", "operating", "debit"),
        ("5-2500", "Beban Transportasi", "expense", "operating", "debit"),
        ("5-2600", "Beban Pemasaran", "expense", "operating", "debit"),
        ("5-2700", "Beban Lain-lain", "expense", "non_operating", "debit"),
    ]

    for code, name, type_, subtype, normal in default_accounts:
        db.add(models.Account(
            code=code, name=name, type=type_,
            subtype=subtype, normal_balance=normal
        ))
    db.commit()
    return {"message": f"{len(default_accounts)} akun default dibuat"}


# ══════════════════════════════════════════════════════════════════════════════
# JURNAL UMUM
# ══════════════════════════════════════════════════════════════════════════════

class JournalLineCreate(BaseModel):
    debit_account_id: Optional[int] = None
    credit_account_id: Optional[int] = None
    amount: float
    description: Optional[str] = None

class JournalCreate(BaseModel):
    date: date
    description: str
    reference: Optional[str] = None
    lines: List[JournalLineCreate]


@router.get("/journals")
def get_journals(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.Journal)
    if start_date:
        q = q.filter(models.Journal.date >= start_date)
    if end_date:
        q = q.filter(models.Journal.date <= end_date)
    journals = q.order_by(models.Journal.id.desc()).offset(skip).limit(limit).all()

    result = []
    for j in journals:
        total_debit = sum(l.amount for l in j.lines if l.debit_account_id)
        total_credit = sum(l.amount for l in j.lines if l.credit_account_id)
        result.append({
            "id": j.id, "number": j.number,
            "date": str(j.date), "description": j.description,
            "reference": j.reference, "source": j.source,
            "creator": j.creator.username if j.creator else "-",
            "total_debit": total_debit, "total_credit": total_credit,
            "balanced": abs(total_debit - total_credit) < 0.01,
            "lines": [{
                "id": l.id,
                "debit_account": f"{l.debit_account.code} - {l.debit_account.name}" if l.debit_account else None,
                "debit_account_id": l.debit_account_id,
                "credit_account": f"{l.credit_account.code} - {l.credit_account.name}" if l.credit_account else None,
                "credit_account_id": l.credit_account_id,
                "amount": l.amount, "description": l.description
            } for l in j.lines]
        })
    return result


@router.get("/journals/{journal_id}")
def get_journal(
    journal_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    j = db.query(models.Journal).get(journal_id)
    if not j:
        raise HTTPException(404, "Jurnal tidak ditemukan")
    return {
        "id": j.id, "number": j.number,
        "date": str(j.date), "description": j.description,
        "reference": j.reference, "source": j.source,
        "creator": j.creator.username if j.creator else "-",
        "lines": [{
            "id": l.id,
            "debit_account_id": l.debit_account_id,
            "debit_account_code": l.debit_account.code if l.debit_account else None,
            "debit_account_name": l.debit_account.name if l.debit_account else None,
            "credit_account_id": l.credit_account_id,
            "credit_account_code": l.credit_account.code if l.credit_account else None,
            "credit_account_name": l.credit_account.name if l.credit_account else None,
            "amount": l.amount, "description": l.description
        } for l in j.lines]
    }


@router.post("/journals")
def create_journal(
    data: JournalCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Validasi: total debit harus = total credit
    total_debit = sum(l.amount for l in data.lines if l.debit_account_id)
    total_credit = sum(l.amount for l in data.lines if l.credit_account_id)

    if abs(total_debit - total_credit) > 0.01:
        raise HTTPException(400,
            f"Jurnal tidak balance. Debit: {total_debit:,.0f}, Credit: {total_credit:,.0f}, "
            f"Selisih: {abs(total_debit - total_credit):,.0f}")

    if not data.lines:
        raise HTTPException(400, "Jurnal harus memiliki minimal 1 baris")

    # Validasi semua akun ada
    for line in data.lines:
        if line.debit_account_id:
            if not db.query(models.Account).get(line.debit_account_id):
                raise HTTPException(404, f"Akun debit {line.debit_account_id} tidak ditemukan")
        if line.credit_account_id:
            if not db.query(models.Account).get(line.credit_account_id):
                raise HTTPException(404, f"Akun kredit {line.credit_account_id} tidak ditemukan")
        if line.amount <= 0:
            raise HTTPException(400, "Jumlah harus lebih dari 0")

    number = next_journal_number(db)
    journal = models.Journal(
        number=number, date=data.date,
        description=data.description, reference=data.reference,
        source="manual", created_by=current_user.id
    )
    db.add(journal)
    db.flush()

    for line in data.lines:
        db.add(models.JournalEntryLine(
            journal_id=journal.id,
            debit_account_id=line.debit_account_id,
            credit_account_id=line.credit_account_id,
            amount=line.amount,
            description=line.description
        ))

    db.commit()
    db.refresh(journal)
    write_audit(db, current_user.id, "CREATE", "journals", journal.id,
                f"Jurnal {journal.number}: {data.description}")
    db.commit()
    return {"id": journal.id, "number": journal.number, "message": "Jurnal berhasil disimpan"}


@router.delete("/journals/{journal_id}")
def delete_journal(
    journal_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    j = db.query(models.Journal).get(journal_id)
    if not j:
        raise HTTPException(404, "Jurnal tidak ditemukan")
    if j.source != "manual":
        raise HTTPException(400, "Jurnal otomatis tidak bisa dihapus")
    write_audit(db, current_user.id, "DELETE", "journals", j.id, f"Hapus jurnal {j.number}")
    db.delete(j)
    db.commit()
    return {"message": "Jurnal dihapus"}


# ══════════════════════════════════════════════════════════════════════════════
# BUKU BESAR
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/ledger/{account_id}")
def get_ledger(
    account_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    account = db.query(models.Account).get(account_id)
    if not account:
        raise HTTPException(404, "Akun tidak ditemukan")

    # Semua baris jurnal yang melibatkan akun ini
    q_debit = db.query(models.JournalEntryLine).filter(
        models.JournalEntryLine.debit_account_id == account_id
    ).join(models.Journal)
    q_credit = db.query(models.JournalEntryLine).filter(
        models.JournalEntryLine.credit_account_id == account_id
    ).join(models.Journal)

    if start_date:
        q_debit = q_debit.filter(models.Journal.date >= start_date)
        q_credit = q_credit.filter(models.Journal.date >= start_date)
    if end_date:
        q_debit = q_debit.filter(models.Journal.date <= end_date)
        q_credit = q_credit.filter(models.Journal.date <= end_date)

    entries = []

    for line in q_debit.all():
        entries.append({
            "date": str(line.journal.date),
            "journal_number": line.journal.number,
            "description": line.journal.description,
            "debit": line.amount, "credit": 0,
            "sort_key": str(line.journal.date) + str(line.journal.id).zfill(10)
        })

    for line in q_credit.all():
        entries.append({
            "date": str(line.journal.date),
            "journal_number": line.journal.number,
            "description": line.journal.description,
            "debit": 0, "credit": line.amount,
            "sort_key": str(line.journal.date) + str(line.journal.id).zfill(10)
        })

    entries.sort(key=lambda x: x["sort_key"])

    # Hitung saldo berjalan
    running_balance = account.opening_balance
    for e in entries:
        if account.normal_balance == "debit":
            running_balance += e["debit"] - e["credit"]
        else:
            running_balance += e["credit"] - e["debit"]
        e["balance"] = running_balance
        del e["sort_key"]

    total_debit = sum(e["debit"] for e in entries)
    total_credit = sum(e["credit"] for e in entries)
    closing_balance = get_account_balance(db, account_id, start_date, end_date)

    return {
        "account": {
            "id": account.id, "code": account.code,
            "name": account.name, "type": account.type,
            "normal_balance": account.normal_balance,
            "opening_balance": account.opening_balance
        },
        "entries": entries,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "closing_balance": closing_balance,
        "period": {"start": str(start_date) if start_date else None,
                   "end": str(end_date) if end_date else None}
    }


# ══════════════════════════════════════════════════════════════════════════════
# NERACA SALDO (TRIAL BALANCE)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/trial-balance")
def get_trial_balance(
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    accounts = db.query(models.Account).filter(
        models.Account.is_active == True
    ).order_by(models.Account.code).all()

    rows = []
    total_debit = 0
    total_credit = 0

    for a in accounts:
        balance = get_account_balance(db, a.id, end_date=end_date)

        if balance == 0:
            continue  # skip akun dengan saldo 0

        if a.normal_balance == "debit":
            debit_bal = balance if balance >= 0 else 0
            credit_bal = abs(balance) if balance < 0 else 0
        else:
            credit_bal = balance if balance >= 0 else 0
            debit_bal = abs(balance) if balance < 0 else 0

        total_debit += debit_bal
        total_credit += credit_bal

        rows.append({
            "code": a.code, "name": a.name,
            "type": a.type, "subtype": a.subtype,
            "debit": debit_bal, "credit": credit_bal
        })

    return {
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": abs(total_debit - total_credit) < 1,
        "as_of": str(end_date) if end_date else str(date.today())
    }


# ══════════════════════════════════════════════════════════════════════════════
# LAPORAN LABA RUGI AKUNTANSI
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/income-statement")
def get_income_statement(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    if not start_date:
        start_date = date.today().replace(day=1)
    if not end_date:
        end_date = date.today()

    accounts = db.query(models.Account).filter(
        models.Account.is_active == True,
        models.Account.type.in_(["revenue", "expense"])
    ).order_by(models.Account.code).all()

    revenues = []
    expenses_cogs = []
    expenses_operating = []
    expenses_other = []

    for a in accounts:
        balance = get_account_balance(db, a.id, start_date, end_date)
        row = {"code": a.code, "name": a.name, "amount": abs(balance)}

        if a.type == "revenue":
            revenues.append(row)
        elif a.type == "expense":
            if a.subtype == "cogs":
                expenses_cogs.append(row)
            elif a.subtype == "operating":
                expenses_operating.append(row)
            else:
                expenses_other.append(row)

    total_revenue = sum(r["amount"] for r in revenues)
    total_cogs = sum(e["amount"] for e in expenses_cogs)
    gross_profit = total_revenue - total_cogs
    total_operating_expense = sum(e["amount"] for e in expenses_operating)
    operating_profit = gross_profit - total_operating_expense
    total_other_expense = sum(e["amount"] for e in expenses_other)
    net_profit = operating_profit - total_other_expense

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "revenues": revenues,
        "total_revenue": total_revenue,
        "cogs": expenses_cogs,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "gross_margin_percent": round(gross_profit / total_revenue * 100, 2) if total_revenue else 0,
        "operating_expenses": expenses_operating,
        "total_operating_expense": total_operating_expense,
        "operating_profit": operating_profit,
        "other_expenses": expenses_other,
        "total_other_expense": total_other_expense,
        "net_profit": net_profit
    }


# ══════════════════════════════════════════════════════════════════════════════
# NERACA (BALANCE SHEET)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/balance-sheet")
def get_balance_sheet(
    as_of: Optional[date] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    if not as_of:
        as_of = date.today()

    accounts = db.query(models.Account).filter(
        models.Account.is_active == True,
        models.Account.type.in_(["asset", "liability", "equity"])
    ).order_by(models.Account.code).all()

    assets_current = []
    assets_fixed = []
    liabilities_current = []
    liabilities_long = []
    equity_items = []

    for a in accounts:
        balance = get_account_balance(db, a.id, end_date=as_of)
        row = {"code": a.code, "name": a.name, "amount": balance}

        if a.type == "asset":
            if a.subtype == "fixed_asset":
                assets_fixed.append(row)
            else:
                assets_current.append(row)
        elif a.type == "liability":
            if a.subtype == "long_term_liability":
                liabilities_long.append(row)
            else:
                liabilities_current.append(row)
        elif a.type == "equity":
            equity_items.append(row)

    # Tambahkan laba berjalan tahun ini ke ekuitas
    year_start = as_of.replace(month=1, day=1)
    revenue_accounts = db.query(models.Account).filter(
        models.Account.is_active == True,
        models.Account.type == "revenue"
    ).all()
    expense_accounts = db.query(models.Account).filter(
        models.Account.is_active == True,
        models.Account.type == "expense"
    ).all()

    ytd_revenue = sum(get_account_balance(db, a.id, year_start, as_of) for a in revenue_accounts)
    ytd_expense = sum(get_account_balance(db, a.id, year_start, as_of) for a in expense_accounts)
    current_period_profit = ytd_revenue - ytd_expense

    if current_period_profit != 0:
        equity_items.append({
            "code": "3-9999",
            "name": f"Laba/Rugi Periode Berjalan ({as_of.year})",
            "amount": current_period_profit
        })

    total_current_assets = sum(r["amount"] for r in assets_current)
    total_fixed_assets = sum(r["amount"] for r in assets_fixed)
    total_assets = total_current_assets + total_fixed_assets

    total_current_liabilities = sum(r["amount"] for r in liabilities_current)
    total_long_liabilities = sum(r["amount"] for r in liabilities_long)
    total_liabilities = total_current_liabilities + total_long_liabilities

    total_equity = sum(r["amount"] for r in equity_items)
    total_liabilities_equity = total_liabilities + total_equity

    return {
        "as_of": str(as_of),
        "assets": {
            "current": assets_current,
            "total_current": total_current_assets,
            "fixed": assets_fixed,
            "total_fixed": total_fixed_assets,
            "total": total_assets
        },
        "liabilities": {
            "current": liabilities_current,
            "total_current": total_current_liabilities,
            "long_term": liabilities_long,
            "total_long_term": total_long_liabilities,
            "total": total_liabilities
        },
        "equity": {
            "items": equity_items,
            "total": total_equity
        },
        "total_liabilities_equity": total_liabilities_equity,
        "balanced": abs(total_assets - total_liabilities_equity) < 1,
        "current_period_profit": current_period_profit
    }


# ══════════════════════════════════════════════════════════════════════════════
# KAS MASUK / KAS KELUAR (existing, dipertahankan)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cash-transactions", response_model=list[schemas.CashTransactionOut])
def get_cash_transactions(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    type: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.CashTransaction)
    if start_date:
        q = q.filter(models.CashTransaction.date >= start_date)
    if end_date:
        q = q.filter(models.CashTransaction.date <= end_date)
    if type:
        q = q.filter(models.CashTransaction.type == type)
    return q.order_by(models.CashTransaction.id.desc()).offset(skip).limit(limit).all()


@router.post("/cash-transactions", response_model=schemas.CashTransactionOut)
def create_cash_transaction(
    data: schemas.CashTransactionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not data.number:
        data.number = next_cash_number(db)
    obj = models.CashTransaction(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    write_audit(db, current_user.id, "CREATE", "cash_transactions", obj.id,
                f"{data.type.upper()} {data.amount:,.0f}: {data.description or '-'}")
    db.commit()
    return obj


@router.get("/cash-balance")
def get_cash_balance(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    income = db.query(func.sum(models.CashTransaction.amount)).filter(
        models.CashTransaction.type == "in"
    ).scalar() or 0
    expense = db.query(func.sum(models.CashTransaction.amount)).filter(
        models.CashTransaction.type == "out"
    ).scalar() or 0
    sales_cash = db.query(func.sum(models.Sale.paid)).filter(
        models.Sale.status.in_(["paid", "partial"])
    ).scalar() or 0
    purchase_paid = db.query(func.sum(models.Purchase.paid)).filter(
        models.Purchase.status.in_(["paid", "partial"])
    ).scalar() or 0
    return {
        "total_income": income + sales_cash,
        "total_expense": expense + purchase_paid,
        "balance": (income + sales_cash) - (expense + purchase_paid)
    }


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT EXCEL AKUNTANSI
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/export/journal")
def export_journal_excel(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    import io

    if not start_date:
        start_date = date.today().replace(day=1)
    if not end_date:
        end_date = date.today()

    journals = db.query(models.Journal).filter(
        models.Journal.date >= start_date,
        models.Journal.date <= end_date
    ).order_by(models.Journal.date, models.Journal.id).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jurnal Umum"

    hfill = PatternFill("solid", fgColor="1E293B")
    hfont = Font(color="10B981", bold=True, size=10)
    thin = Border(
        left=Side(style="thin", color="334155"),
        right=Side(style="thin", color="334155"),
        top=Side(style="thin", color="334155"),
        bottom=Side(style="thin", color="334155")
    )

    # Title
    ws.merge_cells("A1:G1")
    ws["A1"] = f"JURNAL UMUM — {start_date.strftime('%d/%m/%Y')} s/d {end_date.strftime('%d/%m/%Y')}"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A1"].alignment = Alignment(horizontal="center")

    headers = ["No. Jurnal", "Tanggal", "Keterangan", "Referensi",
               "Akun Debit", "Akun Kredit", "Jumlah (Rp)"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.font = hfont
        cell.fill = hfill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin

    row = 4
    for j in journals:
        for line in j.lines:
            ws.cell(row=row, column=1, value=j.number).border = thin
            ws.cell(row=row, column=2, value=str(j.date)).border = thin
            ws.cell(row=row, column=3, value=j.description).border = thin
            ws.cell(row=row, column=4, value=j.reference or "").border = thin
            debit_name = f"{line.debit_account.code} - {line.debit_account.name}" if line.debit_account else ""
            credit_name = f"{line.credit_account.code} - {line.credit_account.name}" if line.credit_account else ""
            ws.cell(row=row, column=5, value=debit_name).border = thin
            ws.cell(row=row, column=6, value=credit_name).border = thin
            amt_cell = ws.cell(row=row, column=7, value=line.amount)
            amt_cell.number_format = '#,##0'
            amt_cell.border = thin
            row += 1

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"jurnal_{start_date}_{end_date}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )
