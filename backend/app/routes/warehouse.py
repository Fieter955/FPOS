"""
iPos 5.0 — Multi Gudang
- CRUD gudang
- Stok per gudang
- Transfer antar gudang
- Laporan stok per gudang
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from pydantic import BaseModel
from typing import List

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

router = APIRouter()


def next_transfer_number(db: Session) -> str:
    from datetime import date as d
    today = d.today().strftime("%Y%m%d")
    prefix = f"TR{today}"
    last = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.number.like(f"{prefix}%")
    ).order_by(models.WarehouseTransfer.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def get_warehouse_stock(db: Session, warehouse_id: int, item_id: int) -> float:
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).first()
    return ws.stock if ws else 0.0


def adjust_warehouse_stock(db: Session, warehouse_id: int, item_id: int,
                            delta: float, commit: bool = False):
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).first()
    if ws:
        ws.stock += delta
    else:
        ws = models.WarehouseStock(
            warehouse_id=warehouse_id, item_id=item_id, stock=delta
        )
        db.add(ws)
    if commit:
        db.commit()


# ─── Warehouses CRUD ──────────────────────────────────────────────────────────

@router.get("/")
def get_warehouses(db: Session = Depends(get_db), _=Depends(get_current_user)):
    warehouses = db.query(models.Warehouse).filter(
        models.Warehouse.is_active == True
    ).order_by(models.Warehouse.id).all()
    return [{
        "id": w.id, "code": w.code, "name": w.name,
        "address": w.address, "is_default": w.is_default,
        "is_active": w.is_active,
        "total_items": len(w.stock_items),
        "total_stock_value": sum(
            (ws.stock * (ws.item.buy_price if ws.item else 0))
            for ws in w.stock_items
        )
    } for w in warehouses]


@router.post("/")
def create_warehouse(data: dict, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    code = data.get("code", "").strip()
    name = data.get("name", "").strip()
    if not code or not name:
        raise HTTPException(400, "Kode dan nama gudang wajib diisi")
    if db.query(models.Warehouse).filter(models.Warehouse.code == code).first():
        raise HTTPException(400, "Kode gudang sudah digunakan")

    # Cek apakah ini gudang pertama → jadikan default
    is_first = db.query(models.Warehouse).count() == 0

    w = models.Warehouse(
        code=code, name=name,
        address=data.get("address"),
        is_default=data.get("is_default", is_first) or is_first
    )
    db.add(w); db.commit(); db.refresh(w)

    write_audit(db, current_user.id, "CREATE", "warehouses", w.id, f"Buat gudang {w.name}")
    db.commit()
    return {"id": w.id, "message": f"Gudang {name} dibuat"}


@router.put("/{wid}")
def update_warehouse(wid: int, data: dict, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")
    for k in ["name", "address", "is_active"]:
        if k in data: setattr(w, k, data[k])
    if data.get("is_default"):
        # Unset default dari gudang lain
        db.query(models.Warehouse).filter(
            models.Warehouse.id != wid
        ).update({"is_default": False})
        w.is_default = True
    db.commit()
    return {"message": "Gudang diperbarui"}


@router.delete("/{wid}")
def delete_warehouse(wid: int, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")
    if w.is_default: raise HTTPException(400, "Gudang default tidak bisa dihapus")
    has_stock = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == wid,
        models.WarehouseStock.stock > 0
    ).first()
    if has_stock: raise HTTPException(400, "Masih ada stok di gudang ini. Transfer dulu sebelum hapus.")
    w.is_active = False
    db.commit()
    return {"message": "Gudang dinonaktifkan"}


# ─── Stock per Warehouse ──────────────────────────────────────────────────────

@router.get("/{wid}/stock")
def get_warehouse_stock_list(
    wid: int, search: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")

    q = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == wid
    ).join(models.Item)

    if search:
        q = q.filter(
            models.Item.name.ilike(f"%{search}%") |
            models.Item.code.ilike(f"%{search}%")
        )

    stocks = q.all()
    return {
        "warehouse": {"id": w.id, "name": w.name, "code": w.code},
        "items": [{
            "item_id": s.item_id,
            "item_code": s.item.code if s.item else "-",
            "item_name": s.item.name if s.item else "-",
            "unit": s.item.unit.abbreviation if s.item and s.item.unit else "pcs",
            "stock": s.stock,
            "min_stock": s.item.min_stock if s.item else 0,
            "low_stock": s.stock <= (s.item.min_stock if s.item else 0),
            "stock_value": s.stock * (s.item.buy_price if s.item else 0)
        } for s in stocks],
        "total_items": len(stocks),
        "total_value": sum(
            s.stock * (s.item.buy_price if s.item else 0) for s in stocks
        )
    }


# ─── Warehouse Transfer ───────────────────────────────────────────────────────

class TransferItemIn(BaseModel):
    item_id: int
    qty: float

class TransferCreate(BaseModel):
    date: date
    from_warehouse_id: int
    to_warehouse_id: int
    notes: Optional[str] = None
    items: List[TransferItemIn]


@router.get("/transfers/all")
def get_transfers(skip: int = 0, limit: int = 50,
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    transfers = db.query(models.WarehouseTransfer).order_by(
        models.WarehouseTransfer.id.desc()
    ).offset(skip).limit(limit).all()

    return [{
        "id": t.id, "number": t.number, "date": str(t.date),
        "from_warehouse": t.from_warehouse.name if t.from_warehouse else "-",
        "to_warehouse": t.to_warehouse.name if t.to_warehouse else "-",
        "status": t.status, "notes": t.notes,
        "items_count": len(t.items),
        "creator": t.creator.username if t.creator else "-"
    } for t in transfers]


@router.post("/transfers")
def create_transfer(data: TransferCreate, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    if data.from_warehouse_id == data.to_warehouse_id:
        raise HTTPException(400, "Gudang asal dan tujuan tidak boleh sama")

    from_w = db.query(models.Warehouse).get(data.from_warehouse_id)
    to_w = db.query(models.Warehouse).get(data.to_warehouse_id)
    if not from_w: raise HTTPException(404, "Gudang asal tidak ditemukan")
    if not to_w: raise HTTPException(404, "Gudang tujuan tidak ditemukan")

    # Validasi stok
    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        avail = get_warehouse_stock(db, data.from_warehouse_id, it.item_id)
        if avail < it.qty:
            raise HTTPException(400,
                f"Stok {item.name} di {from_w.name} tidak cukup "
                f"({avail} tersedia, butuh {it.qty})")

    number = next_transfer_number(db)
    transfer = models.WarehouseTransfer(
        number=number, date=data.date,
        from_warehouse_id=data.from_warehouse_id,
        to_warehouse_id=data.to_warehouse_id,
        status="confirmed",
        notes=data.notes,
        created_by=current_user.id
    )
    db.add(transfer); db.flush()

    for it in data.items:
        db.add(models.WarehouseTransferItem(
            transfer_id=transfer.id, item_id=it.item_id, qty=it.qty
        ))
        # Kurangi stok gudang asal
        adjust_warehouse_stock(db, data.from_warehouse_id, it.item_id, -it.qty)
        # Tambah stok gudang tujuan
        adjust_warehouse_stock(db, data.to_warehouse_id, it.item_id, it.qty)

    db.commit()
    write_audit(db, current_user.id, "CREATE", "warehouse_transfers", transfer.id,
                f"Transfer {number}: {from_w.name} → {to_w.name}")
    db.commit()

    return {"id": transfer.id, "number": number, "message": "Transfer berhasil dikonfirmasi"}


# ─── Seed default warehouse ───────────────────────────────────────────────────

@router.post("/seed-default")
def seed_default_warehouse(db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    """Buat gudang utama default jika belum ada"""
    existing = db.query(models.Warehouse).count()
    if existing > 0:
        raise HTTPException(400, "Gudang sudah ada")
    w = models.Warehouse(
        code="GDG-01", name="Gudang Utama",
        is_default=True, is_active=True
    )
    db.add(w); db.commit(); db.refresh(w)

    # Migrate existing item stock ke gudang ini
    items = db.query(models.Item).filter(models.Item.stock > 0).all()
    for item in items:
        db.add(models.WarehouseStock(
            warehouse_id=w.id, item_id=item.id, stock=item.stock
        ))
    db.commit()

    return {"message": f"Gudang Utama dibuat dan {len(items)} item dimigrasi"}
