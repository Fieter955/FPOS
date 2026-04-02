"""
iPos 5.0 — Surat Jalan
Dokumen pengiriman barang ke lokasi proyek/pelanggan.
Wajib ada di toko bangunan yang punya layanan antar.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

router = APIRouter()


def next_delivery_number(db: Session) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"SJ{today}"
    last = db.query(models.DeliveryNote).filter(
        models.DeliveryNote.number.like(f"{prefix}%")
    ).order_by(models.DeliveryNote.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


class DeliveryItemIn(BaseModel):
    item_id: int
    qty: float
    unit_name: Optional[str] = None
    notes: Optional[str] = None


class DeliveryCreate(BaseModel):
    date: date
    sale_id: Optional[int] = None
    customer_id: Optional[int] = None
    delivery_address: str
    recipient_name: Optional[str] = None
    driver_name: Optional[str] = None
    vehicle_no: Optional[str] = None
    notes: Optional[str] = None
    items: List[DeliveryItemIn]


@router.get("/")
def get_deliveries(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    skip: int = 0, limit: int = 50,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.DeliveryNote)
    if start_date: q = q.filter(models.DeliveryNote.date >= start_date)
    if end_date:   q = q.filter(models.DeliveryNote.date <= end_date)
    if status:     q = q.filter(models.DeliveryNote.status == status)

    notes = q.order_by(models.DeliveryNote.id.desc()).offset(skip).limit(limit).all()

    return [{
        "id": n.id, "number": n.number, "date": str(n.date),
        "customer": n.customer.name if n.customer else "-",
        "delivery_address": n.delivery_address,
        "driver_name": n.driver_name,
        "vehicle_no": n.vehicle_no,
        "status": n.status,
        "items_count": len(n.items),
        "creator": n.creator.username if n.creator else "-",
    } for n in notes]


@router.get("/{note_id}")
def get_delivery(
    note_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    note = db.query(models.DeliveryNote).get(note_id)
    if not note:
        raise HTTPException(404, "Surat jalan tidak ditemukan")
    return {
        "id": note.id, "number": note.number, "date": str(note.date),
        "customer": {"id": note.customer.id, "name": note.customer.name, "phone": note.customer.phone} if note.customer else None,
        "delivery_address": note.delivery_address,
        "recipient_name": note.recipient_name,
        "driver_name": note.driver_name,
        "vehicle_no": note.vehicle_no,
        "notes": note.notes,
        "status": note.status,
        "creator": note.creator.username if note.creator else "-",
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "items": [{
            "id": i.id,
            "item_id": i.item_id,
            "item_code": i.item.code if i.item else "-",
            "item_name": i.item.name if i.item else "-",
            "qty": i.qty,
            "unit_name": i.unit_name or (i.item.unit.abbreviation if i.item and i.item.unit else "pcs"),
            "notes": i.notes,
        } for i in note.items]
    }


@router.post("/")
def create_delivery(
    data: DeliveryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not data.items:
        raise HTTPException(400, "Surat jalan harus memiliki minimal 1 item")
    if not data.delivery_address.strip():
        raise HTTPException(400, "Alamat pengiriman wajib diisi")

    # Validasi semua item ada
    for it in data.items:
        if not db.query(models.Item).get(it.item_id):
            raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        if it.qty <= 0:
            raise HTTPException(400, "Qty harus lebih dari 0")

    number = next_delivery_number(db)
    note = models.DeliveryNote(
        number=number,
        date=data.date,
        sale_id=data.sale_id,
        customer_id=data.customer_id,
        delivery_address=data.delivery_address,
        recipient_name=data.recipient_name,
        driver_name=data.driver_name,
        vehicle_no=data.vehicle_no,
        notes=data.notes,
        status="pending",
        created_by=current_user.id,
    )
    db.add(note)
    db.flush()

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        db.add(models.DeliveryNoteItem(
            delivery_id=note.id,
            item_id=it.item_id,
            qty=it.qty,
            unit_name=it.unit_name or (item.unit.abbreviation if item and item.unit else "pcs"),
            notes=it.notes,
        ))

    db.commit()
    write_audit(db, current_user.id, "CREATE", "delivery_notes", note.id,
                f"Surat jalan {number} ke {data.delivery_address[:50]}")
    db.commit()

    return {"id": note.id, "number": number, "message": "Surat jalan dibuat"}


@router.put("/{note_id}/status")
def update_status(
    note_id: int, data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    note = db.query(models.DeliveryNote).get(note_id)
    if not note:
        raise HTTPException(404, "Surat jalan tidak ditemukan")

    new_status = data.get("status")
    if new_status not in ["pending", "delivered", "signed"]:
        raise HTTPException(400, "Status harus: pending | delivered | signed")

    note.status = new_status
    if new_status == "signed":
        from datetime import datetime
        note.signed_at = datetime.utcnow()
        if data.get("recipient_name"):
            note.recipient_name = data["recipient_name"]

    db.commit()
    write_audit(db, current_user.id, "UPDATE", "delivery_notes", note.id,
                f"Status surat jalan {note.number} → {new_status}")
    db.commit()
    return {"message": f"Status diperbarui ke {new_status}"}


@router.post("/from-sale/{sale_id}")
def create_from_sale(
    sale_id: int, data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Buat surat jalan otomatis dari transaksi penjualan"""
    sale = db.query(models.Sale).get(sale_id)
    if not sale:
        raise HTTPException(404, "Penjualan tidak ditemukan")

    if not data.get("delivery_address"):
        raise HTTPException(400, "Alamat pengiriman wajib diisi")

    number = next_delivery_number(db)
    note = models.DeliveryNote(
        number=number,
        date=sale.date,
        sale_id=sale_id,
        customer_id=sale.customer_id,
        delivery_address=data["delivery_address"],
        driver_name=data.get("driver_name"),
        vehicle_no=data.get("vehicle_no"),
        notes=data.get("notes"),
        status="pending",
        created_by=current_user.id,
    )
    db.add(note)
    db.flush()

    for si in sale.items:
        if si.item:
            db.add(models.DeliveryNoteItem(
                delivery_id=note.id,
                item_id=si.item_id,
                qty=si.qty,
                unit_name=si.item.unit.abbreviation if si.item.unit else "pcs",
            ))

    db.commit()
    write_audit(db, current_user.id, "CREATE", "delivery_notes", note.id,
                f"Surat jalan {number} dari faktur {sale.number}")
    db.commit()
    return {"id": note.id, "number": number, "message": "Surat jalan dibuat dari penjualan"}


@router.delete("/{note_id}")
def delete_delivery(
    note_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    note = db.query(models.DeliveryNote).get(note_id)
    if not note:
        raise HTTPException(404, "Surat jalan tidak ditemukan")
    if note.status == "signed":
        raise HTTPException(400, "Surat jalan yang sudah ditandatangani tidak bisa dihapus")
    write_audit(db, current_user.id, "DELETE", "delivery_notes", note.id,
                f"Hapus surat jalan {note.number}")
    db.delete(note)
    db.commit()
    return {"message": "Surat jalan dihapus"}
