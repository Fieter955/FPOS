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
from ..services.virtual_units import get_required_stock_qty, is_virtual_variant
from ..services.inventory_fifo import consume_fifo, add_batch, EPS
from .warehouse import get_warehouse_stock, adjust_warehouse_stock

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
    operational_cost: float = 0.0
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
        "operational_cost": b.operational_cost or 0,
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
        "qty_produced": b.qty_produced,
        "operational_cost": b.operational_cost or 0,
        "notes": b.notes,
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
        operational_cost=data.operational_cost,
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


class BOMUpdate(BaseModel):
    product_id: Optional[int] = None
    qty_produced: Optional[float] = None
    operational_cost: Optional[float] = None
    notes: Optional[str] = None
    materials: Optional[List[BOMLineIn]] = None


@router.put("/bom/{bom_id}")
def update_bom(bom_id: int, data: BOMUpdate, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom: raise HTTPException(404, "BOM tidak ditemukan")

    if data.product_id is not None:
        product = db.query(models.Item).get(data.product_id)
        if not product: raise HTTPException(404, "Produk tidak ditemukan")
        # Cek duplikat jika ganti produk
        if data.product_id != bom.product_id:
            existing = db.query(models.BillOfMaterial).filter(
                models.BillOfMaterial.product_id == data.product_id,
                models.BillOfMaterial.is_active == True,
                models.BillOfMaterial.id != bom_id
            ).first()
            if existing:
                raise HTTPException(400, f"BOM untuk {product.name} sudah ada.")
        bom.product_id = data.product_id

    if data.qty_produced is not None:
        bom.qty_produced = data.qty_produced
    if data.operational_cost is not None:
        bom.operational_cost = data.operational_cost
    if data.notes is not None:
        bom.notes = data.notes

    if data.materials is not None:
        if not data.materials:
            raise HTTPException(400, "BOM harus memiliki minimal 1 bahan baku")
        # Hapus baris lama, tambah baru
        for old_line in bom.materials:
            db.delete(old_line)
        db.flush()
        for line in data.materials:
            material = db.query(models.Item).get(line.material_id)
            if not material:
                raise HTTPException(404, f"Bahan baku {line.material_id} tidak ditemukan")
            if line.material_id == bom.product_id:
                raise HTTPException(400, "Bahan baku tidak boleh sama dengan produk jadi")
            if line.qty_needed <= 0:
                raise HTTPException(400, "Qty bahan harus lebih dari 0")
            db.add(models.BOMLine(
                bom_id=bom.id,
                material_id=line.material_id,
                qty_needed=line.qty_needed
            ))

    db.commit(); db.refresh(bom)
    write_audit(db, current_user.id, "UPDATE", "bill_of_materials", bom.id,
                f"Update BOM #{bom.id}")
    db.commit()
    return {"id": bom.id, "message": "BOM diperbarui"}


@router.delete("/bom/{bom_id}")
def delete_bom(bom_id: int, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    b = db.query(models.BillOfMaterial).get(bom_id)
    if not b: raise HTTPException(404, "BOM tidak ditemukan")
    b.is_active = False
    db.commit()
    return {"message": "BOM dinonaktifkan"}


# ─── Eksekusi Perakitan (dipakai bersama create_order & complete_order) ────────

def _stock_item_terkunci(db: Session, item: models.Item) -> models.Item:
    """Kembalikan item STOK NYATA (induk untuk varian virtual) yang dikunci
    (with_for_update) supaya aman dipotong/ditambah stoknya."""
    if is_virtual_variant(item):
        parent = db.query(models.Item).with_for_update().get(item.parent_item_id)
        if not parent:
            raise HTTPException(400, f"Barang induk untuk {item.name} tidak ditemukan.")
        return parent
    return db.query(models.Item).with_for_update().get(item.id)


def _eksekusi_perakitan(db: Session, assembly: models.Assembly, bom: models.BillOfMaterial,
                        qty_planned: float, current_user: models.User) -> float:
    """Jalankan perakitan: konsumsi bahan baku & produksi barang jadi.

    Bila cabang punya gudang default → jalur FIFO: bahan dikonsumsi lewat lapisan
    (StockBatch) sehingga HPP-nya nyata, lalu barang jadi dibuatkan SATU lapisan baru
    seharga total biaya bahan / qty hasil. Stok diperbarui di WarehouseStock DAN
    Item.stock global (sama seperti alur jual/beli). Tidak ada jurnal GL: transformasi
    ini net-nol dalam satu akun Persediaan (1-1400); nilai lapisan yang keluar dari
    bahan = nilai lapisan yang masuk ke barang jadi, jadi neraca & value-drift terjaga.

    Bila cabang TIDAK punya gudang default → perilaku lama (hanya Item.stock global,
    tanpa lapisan) untuk kompatibilitas mundur.

    Kembalikan qty hasil (qty_produced) dan set assembly.status = 'done'.
    """
    nama_produk = bom.product.name if bom.product else "produk"

    gudang = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_default == True,
    ).first()

    qty_result = bom.qty_produced * qty_planned

    # ── Jalur FIFO (ada gudang default) ──────────────────────────────────────
    if gudang:
        # 1) Cek kecukupan stok SEMUA bahan dulu (belum ada mutasi) → atomic.
        rencana = []  # (material, stock_item, needed_dasar)
        shortages = []
        for line in bom.materials:
            material = db.query(models.Item).get(line.material_id)
            if not material:
                continue
            stock_item = _stock_item_terkunci(db, material)
            needed = get_required_stock_qty(material, line.qty_needed * qty_planned)
            tersedia = get_warehouse_stock(db, gudang.id, stock_item.id)
            if tersedia < needed - EPS:
                shortages.append(f"{material.name}: butuh {needed}, tersedia {tersedia}")
            rencana.append((material, stock_item, needed))
        if shortages:
            raise HTTPException(400, "Stok bahan tidak cukup:\n" + "\n".join(shortages))

        # 2) Konsumsi bahan via FIFO + kumpulkan biaya modal nyata.
        total_biaya = 0.0
        for material, stock_item, needed in rencana:
            allocs = consume_fifo(db, item_id=stock_item.id, warehouse_id=gudang.id, qty=needed)
            total_biaya += sum(q * c for (_b, q, c) in allocs)
            adjust_warehouse_stock(db, gudang.id, stock_item.id, -needed)
            before = float(stock_item.stock or 0)
            stock_item.stock = before - needed
            db.add(models.StockMovement(
                date=assembly.date, item_id=stock_item.id,
                branch_id=current_user.active_branch_id,
                type="out", qty=needed,
                qty_before=before, qty_after=stock_item.stock,
                reference=assembly.number, notes=f"Bahan perakitan {nama_produk}",
            ))

        # 3) Produksi barang jadi: satu lapisan baru seharga (biaya bahan + biaya operasional) / qty hasil.
        product = db.query(models.Item).get(bom.product_id)
        if product and qty_result > 0:
            prod_stock = _stock_item_terkunci(db, product)
            biaya_operasional = float(bom.operational_cost or 0)
            unit_cost = ((total_biaya + biaya_operasional) / qty_result) if qty_result else 0.0
            add_batch(db, item_id=prod_stock.id, warehouse_id=gudang.id,
                      qty=qty_result, unit_cost=unit_cost, received_date=assembly.date)
            adjust_warehouse_stock(db, gudang.id, prod_stock.id, qty_result)
            before = float(prod_stock.stock or 0)
            prod_stock.stock = before + qty_result
            prod_stock.buy_price = unit_cost  # segarkan harga modal legacy + lantai POS
            db.add(models.StockMovement(
                date=assembly.date, item_id=prod_stock.id,
                branch_id=current_user.active_branch_id,
                type="in", qty=qty_result,
                qty_before=before, qty_after=prod_stock.stock,
                reference=assembly.number, notes="Hasil perakitan",
            ))

    # ── Jalur lama (tanpa gudang default): hanya Item.stock global ───────────
    else:
        shortages = []
        for line in bom.materials:
            needed = line.qty_needed * qty_planned
            available = line.material.stock if line.material else 0
            if available < needed:
                shortages.append(f"{line.material.name}: butuh {needed}, tersedia {available}")
        if shortages:
            raise HTTPException(400, "Stok bahan tidak cukup:\n" + "\n".join(shortages))

        for line in bom.materials:
            needed = line.qty_needed * qty_planned
            material = db.query(models.Item).get(line.material_id)
            if material:
                before = material.stock
                material.stock -= needed
                db.add(models.StockMovement(
                    date=assembly.date, item_id=material.id,
                    branch_id=current_user.active_branch_id,
                    type="out", qty=needed,
                    qty_before=before, qty_after=material.stock,
                    reference=assembly.number, notes=f"Bahan perakitan {nama_produk}",
                ))

        product = db.query(models.Item).get(bom.product_id)
        if product:
            before = product.stock
            product.stock += qty_result
            db.add(models.StockMovement(
                date=assembly.date, item_id=product.id,
                branch_id=current_user.active_branch_id,
                type="in", qty=qty_result,
                qty_before=before, qty_after=product.stock,
                reference=assembly.number, notes="Hasil perakitan",
            ))

    assembly.qty_produced = qty_result
    assembly.status = "done"
    return qty_result


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
    date_str = data.get("date", str(date.today()))
    try:
        assembly_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        assembly_date = date.today()

    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom: raise HTTPException(404, "BOM tidak ditemukan")
    if qty_planned <= 0: raise HTTPException(400, "Qty harus lebih dari 0")

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

    # Eksekusi: cek stok, konsumsi bahan via FIFO, produksi barang jadi (set status 'done').
    qty_result = _eksekusi_perakitan(db, assembly, bom, qty_planned, current_user)

    db.commit()
    write_audit(db, current_user.id, "CREATE", "assemblies", assembly.id,
                f"Perakitan {number}: {qty_planned}x {bom.product.name if bom.product else '-'}")
    db.commit()

    return {
        "id": assembly.id, "number": number,
        "qty_produced": qty_result,
        "message": f"Perakitan selesai. {qty_result} unit {bom.product.name if bom.product else 'produk'} ditambahkan ke stok."
    }


@router.put("/orders/{order_id}")
def update_assembly_order(order_id: int, data: dict, db: Session = Depends(get_db),
                          current_user: models.User = Depends(get_current_user)):
    """Update status order perakitan (misal: start / cancel)"""
    assembly = db.query(models.Assembly).get(order_id)
    if not assembly: raise HTTPException(404, "Order perakitan tidak ditemukan")

    new_status = data.get("status")
    valid_transitions = {
        "draft": ["in_progress", "cancelled"],
        "pending": ["in_progress", "cancelled"],
        "in_progress": ["done", "cancelled"],
    }
    allowed = valid_transitions.get(assembly.status, [])
    if new_status and new_status not in allowed:
        raise HTTPException(400, f"Tidak bisa ubah status dari '{assembly.status}' ke '{new_status}'")

    if new_status:
        assembly.status = new_status

    if data.get("notes") is not None:
        assembly.notes = data["notes"]

    db.commit()
    write_audit(db, current_user.id, "UPDATE", "assemblies", assembly.id,
                f"Update order {assembly.number} → {new_status}")
    db.commit()
    return {"id": assembly.id, "status": assembly.status, "message": f"Status diubah ke {assembly.status}"}


@router.post("/orders/{order_id}/complete")
def complete_assembly_order(order_id: int, db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    """Selesaikan perakitan: kurangi bahan baku, tambah produk jadi"""
    assembly = db.query(models.Assembly).get(order_id)
    if not assembly: raise HTTPException(404, "Order perakitan tidak ditemukan")
    if assembly.status == "done":
        raise HTTPException(400, "Order sudah selesai")
    if assembly.status == "cancelled":
        raise HTTPException(400, "Order sudah dibatalkan")

    bom = assembly.bom
    if not bom: raise HTTPException(404, "BOM tidak ditemukan")

    qty_planned = assembly.qty_planned

    # Eksekusi: cek stok, konsumsi bahan via FIFO, produksi barang jadi (set status 'done').
    qty_result = _eksekusi_perakitan(db, assembly, bom, qty_planned, current_user)

    db.commit()
    write_audit(db, current_user.id, "UPDATE", "assemblies", assembly.id,
                f"Perakitan selesai {assembly.number}: {qty_result} unit {bom.product.name if bom.product else '-'}")
    db.commit()

    return {
        "id": assembly.id, "number": assembly.number,
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
