"""
iPos 5.0 — Perakitan (Assembly / Bill of Materials)
- Buat BOM (formula produk jadi dari bahan baku)
- Order perakitan
- Eksekusi perakitan: kurangi bahan, tambah produk jadi
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

router = APIRouter()


def next_assembly_number(db: Session) -> str:
    from datetime import date as d
    today = d.today().strftime("%Y%m%d")
    prefix = f"ASM{today}"
    last = db.query(models.Assembly).filter(
        models.Assembly.number.like(f"{prefix}%")
    ).order_by(models.Assembly.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


# ─── Bill of Materials ────────────────────────────────────────────────────────

class BOMLineIn(BaseModel):
    material_id: int
    qty_needed: float

class BOMCreate(BaseModel):
    product_id: int
    qty_produced: float = 1.0
    notes: Optional[str] = None
    materials: List[BOMLineIn]


@router.get("/bom")
def get_boms(db: Session = Depends(get_db), _=Depends(get_current_user)):
    boms = db.query(models.BillOfMaterial).filter(
        models.BillOfMaterial.is_active == True
    ).all()
    return [{
        "id": b.id,
        "product_id": b.product_id,
        "product_name": b.product.name if b.product else "-",
        "product_code": b.product.code if b.product else "-",
        "qty_produced": b.qty_produced,
        "notes": b.notes,
        "materials": [{
            "material_id": l.material_id,
            "material_name": l.material.name if l.material else "-",
            "material_code": l.material.code if l.material else "-",
            "unit": l.material.unit.abbreviation if l.material and l.material.unit else "pcs",
            "qty_needed": l.qty_needed,
            "current_stock": l.material.stock if l.material else 0
        } for l in b.materials]
    } for b in boms]


@router.get("/bom/{bom_id}")
def get_bom(bom_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    b = db.query(models.BillOfMaterial).get(bom_id)
    if not b: raise HTTPException(404, "BOM tidak ditemukan")
    return {
        "id": b.id, "product_id": b.product_id,
        "product_name": b.product.name if b.product else "-",
        "qty_produced": b.qty_produced, "notes": b.notes,
        "materials": [{
            "id": l.id, "material_id": l.material_id,
            "material_name": l.material.name if l.material else "-",
            "unit": l.material.unit.abbreviation if l.material and l.material.unit else "pcs",
            "qty_needed": l.qty_needed,
            "current_stock": l.material.stock if l.material else 0,
            "enough": (l.material.stock if l.material else 0) >= l.qty_needed
        } for l in b.materials]
    }


@router.post("/bom")
def create_bom(data: BOMCreate, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    product = db.query(models.Item).get(data.product_id)
    if not product: raise HTTPException(404, "Produk tidak ditemukan")

    # Cek duplikat BOM untuk produk yang sama
    existing = db.query(models.BillOfMaterial).filter(
        models.BillOfMaterial.product_id == data.product_id,
        models.BillOfMaterial.is_active == True
    ).first()
    if existing:
        raise HTTPException(400, f"BOM untuk {product.name} sudah ada. Hapus dulu yang lama.")

    if not data.materials:
        raise HTTPException(400, "BOM harus memiliki minimal 1 bahan baku")

    bom = models.BillOfMaterial(
        product_id=data.product_id,
        qty_produced=data.qty_produced,
        notes=data.notes
    )
    db.add(bom); db.flush()

    for line in data.materials:
        material = db.query(models.Item).get(line.material_id)
        if not material:
            raise HTTPException(404, f"Bahan baku {line.material_id} tidak ditemukan")
        if line.material_id == data.product_id:
            raise HTTPException(400, "Bahan baku tidak boleh sama dengan produk jadi")
        if line.qty_needed <= 0:
            raise HTTPException(400, "Qty bahan harus lebih dari 0")
        db.add(models.BOMLine(
            bom_id=bom.id,
            material_id=line.material_id,
            qty_needed=line.qty_needed
        ))

    db.commit(); db.refresh(bom)
    write_audit(db, current_user.id, "CREATE", "bill_of_materials", bom.id,
                f"BOM untuk {product.name}")
    db.commit()
    return {"id": bom.id, "message": f"BOM untuk {product.name} dibuat"}


@router.delete("/bom/{bom_id}")
def delete_bom(bom_id: int, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    b = db.query(models.BillOfMaterial).get(bom_id)
    if not b: raise HTTPException(404, "BOM tidak ditemukan")
    b.is_active = False
    db.commit()
    return {"message": "BOM dinonaktifkan"}


# ─── Assembly Order ───────────────────────────────────────────────────────────

@router.get("/orders")
def get_assemblies(
    status: Optional[str] = None,
    skip: int = 0, limit: int = 50,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.Assembly)
    if status: q = q.filter(models.Assembly.status == status)
    assemblies = q.order_by(models.Assembly.id.desc()).offset(skip).limit(limit).all()

    return [{
        "id": a.id, "number": a.number, "date": str(a.date),
        "bom_id": a.bom_id,
        "product_name": a.bom.product.name if a.bom and a.bom.product else "-",
        "qty_planned": a.qty_planned,
        "qty_produced": a.qty_produced,
        "status": a.status, "notes": a.notes,
        "creator": a.creator.username if a.creator else "-"
    } for a in assemblies]


@router.post("/orders")
def create_assembly_order(data: dict, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    bom_id = data.get("bom_id")
    qty_planned = float(data.get("qty_planned", 1))
    assembly_date = data.get("date", str(date.today()))

    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom: raise HTTPException(404, "BOM tidak ditemukan")
    if qty_planned <= 0: raise HTTPException(400, "Qty harus lebih dari 0")

    # Cek kecukupan stok bahan
    shortages = []
    for line in bom.materials:
        needed = line.qty_needed * qty_planned
        available = line.material.stock if line.material else 0
        if available < needed:
            shortages.append(
                f"{line.material.name}: butuh {needed}, tersedia {available}"
            )

    if shortages:
        raise HTTPException(400,
            f"Stok bahan tidak cukup:\n" + "\n".join(shortages))

    number = next_assembly_number(db)
    assembly = models.Assembly(
        number=number,
        date=assembly_date,
        bom_id=bom_id,
        qty_planned=qty_planned,
        qty_produced=0,
        status="in_progress",
        notes=data.get("notes"),
        created_by=current_user.id
    )
    db.add(assembly); db.flush()

    # Langsung eksekusi: kurangi bahan baku, tambah produk jadi
    for line in bom.materials:
        needed = line.qty_needed * qty_planned
        material = db.query(models.Item).get(line.material_id)
        if material:
            before = material.stock
            material.stock -= needed
            db.add(models.StockMovement(
                date=assembly_date, item_id=material.id,
                type="out", qty=needed,
                qty_before=before, qty_after=material.stock,
                reference=number, notes=f"Bahan perakitan {bom.product.name}"
            ))

    # Tambah stok produk jadi
    qty_result = bom.qty_produced * qty_planned
    product = db.query(models.Item).get(bom.product_id)
    if product:
        before = product.stock
        product.stock += qty_result
        db.add(models.StockMovement(
            date=assembly_date, item_id=product.id,
            type="in", qty=qty_result,
            qty_before=before, qty_after=product.stock,
            reference=number, notes=f"Hasil perakitan"
        ))

    assembly.qty_produced = qty_result
    assembly.status = "done"

    db.commit()
    write_audit(db, current_user.id, "CREATE", "assemblies", assembly.id,
                f"Perakitan {number}: {qty_planned}x {bom.product.name if bom.product else '-'}")
    db.commit()

    return {
        "id": assembly.id, "number": number,
        "qty_produced": qty_result,
        "message": f"Perakitan selesai. {qty_result} unit {bom.product.name if bom.product else 'produk'} ditambahkan ke stok."
    }


@router.get("/simulate/{bom_id}")
def simulate_assembly(
    bom_id: int, qty: float = 1.0,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Simulasi: cek apakah stok bahan cukup untuk qty tertentu"""
    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom: raise HTTPException(404, "BOM tidak ditemukan")

    lines = []
    can_produce = True

    for line in bom.materials:
        needed = line.qty_needed * qty
        available = line.material.stock if line.material else 0
        enough = available >= needed
        if not enough: can_produce = False
        lines.append({
            "material_name": line.material.name if line.material else "-",
            "qty_needed": needed,
            "available": available,
            "enough": enough,
            "shortage": max(0, needed - available)
        })

    max_producible = 0
    if bom.materials:
        max_producible = min(
            (line.material.stock if line.material else 0) / line.qty_needed
            for line in bom.materials
            if line.qty_needed > 0
        ) if bom.materials else 0

    return {
        "bom_id": bom_id,
        "product": bom.product.name if bom.product else "-",
        "qty_requested": qty,
        "qty_result": bom.qty_produced * qty,
        "can_produce": can_produce,
        "max_producible": int(max_producible / bom.qty_produced) if bom.qty_produced else 0,
        "materials": lines
    }
