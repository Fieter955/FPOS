"""Perakitan sederhana: Formula -> Pesanan -> Proses -> Proses Jadi.

Pesanan tidak menyentuh stok. Memulai proses mengonsumsi bahan dari FIFO,
sedangkan Proses Jadi dapat diposting beberapa kali dan baru menambah stok hasil.
Transaksi stok dikoreksi melalui reversal, bukan edit/hapus langsung.
"""
from datetime import date as Date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, write_audit
from ..database import get_db
from ..services.inventory_fifo import EPS, add_batch, consume_fifo
from ..services.virtual_units import get_required_stock_qty, is_virtual_variant
from .warehouse import adjust_warehouse_stock, get_warehouse_stock

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BOMLineIn(BaseModel):
    material_id: int
    qty_needed: float


class BOMCreate(BaseModel):
    product_id: int
    qty_produced: float = 1.0
    operational_cost: float = 0.0
    notes: Optional[str] = None
    materials: List[BOMLineIn]


class BOMUpdate(BaseModel):
    product_id: Optional[int] = None
    qty_produced: Optional[float] = None
    operational_cost: Optional[float] = None
    notes: Optional[str] = None
    materials: Optional[List[BOMLineIn]] = None


class AssemblyOrderLineIn(BaseModel):
    bom_id: int
    qty_ordered: float


class AssemblyOrderCreate(BaseModel):
    date: Date = Field(default_factory=Date.today)
    customer_id: int
    notes: Optional[str] = None
    lines: List[AssemblyOrderLineIn]


class AssemblyProcessLineIn(BaseModel):
    bom_id: Optional[int] = None
    order_line_id: Optional[int] = None
    qty_target: float


class AssemblyProcessCreate(BaseModel):
    date: Date = Field(default_factory=Date.today)
    order_id: Optional[int] = None
    customer_id: Optional[int] = None
    warehouse_id: int
    notes: Optional[str] = None
    lines: List[AssemblyProcessLineIn]


class AssemblyResultLineIn(BaseModel):
    process_line_id: int
    qty_finished: float


class AssemblyResultCreate(BaseModel):
    process_id: int
    date: Date = Field(default_factory=Date.today)
    close_process: bool = False
    confirm_variance: bool = False
    notes: Optional[str] = None
    lines: List[AssemblyResultLineIn]


class CancelIn(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _branch_id(user: models.User) -> int:
    branch_id = user.active_branch_id or user.branch_id
    if not branch_id:
        raise HTTPException(400, "Pilih cabang aktif terlebih dahulu")
    return int(branch_id)


def _next_number(db: Session, model, prefix: str, tx_date: Date) -> str:
    stem = f"{prefix}{tx_date.strftime('%Y%m%d')}"
    last = (
        db.query(model)
        .filter(model.number.like(f"{stem}%"))
        .order_by(model.id.desc())
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.number[-4:]) + 1
        except (TypeError, ValueError):
            seq = last.id + 1
    return f"{stem}{seq:04d}"


def _stock_item(db: Session, item: models.Item, lock: bool = True) -> models.Item:
    item_id = item.parent_item_id if is_virtual_variant(item) else item.id
    q = db.query(models.Item)
    if lock:
        q = q.with_for_update()
    stock_item = q.get(item_id)
    if not stock_item:
        raise HTTPException(400, f"Barang stok untuk {item.name} tidak ditemukan")
    return stock_item


def _warehouse_for_branch(db: Session, warehouse_id: int, branch_id: int) -> models.Warehouse:
    warehouse = (
        db.query(models.Warehouse)
        .filter(
            models.Warehouse.id == warehouse_id,
            models.Warehouse.branch_id == branch_id,
            models.Warehouse.is_active == True,
        )
        .first()
    )
    if not warehouse:
        raise HTTPException(400, "Gudang tidak ditemukan pada cabang aktif")
    return warehouse


def _validate_bom_payload(
    db: Session,
    product_id: int,
    qty_produced: float,
    operational_cost: float,
    materials: List[BOMLineIn],
) -> models.Item:
    product = db.query(models.Item).get(product_id)
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    if qty_produced <= EPS:
        raise HTTPException(400, "Jumlah hasil formula harus lebih dari 0")
    if operational_cost < 0:
        raise HTTPException(400, "Biaya operasional tidak boleh negatif")
    if not materials:
        raise HTTPException(400, "Formula harus memiliki minimal 1 bahan baku")
    seen = set()
    for line in materials:
        if line.material_id in seen:
            raise HTTPException(400, "Bahan yang sama tidak boleh ditambahkan dua kali")
        seen.add(line.material_id)
        material = db.query(models.Item).get(line.material_id)
        if not material:
            raise HTTPException(404, f"Bahan baku {line.material_id} tidak ditemukan")
        if line.material_id == product_id:
            raise HTTPException(400, "Bahan baku tidak boleh sama dengan produk jadi")
        if line.qty_needed <= EPS:
            raise HTTPException(400, "Jumlah bahan harus lebih dari 0")
    return product


def _refresh_order_status(order: models.AssemblyCustomerOrder) -> None:
    if order.status == "cancelled":
        return
    processed = sum(float(line.qty_processed or 0) for line in order.lines)
    ordered = sum(float(line.qty_ordered or 0) for line in order.lines)
    if processed <= EPS:
        order.status = "open"
    elif processed + EPS < ordered:
        order.status = "partially_processed"
    else:
        order.status = "processed"


def _order_dict(order: models.AssemblyCustomerOrder, detail: bool = False) -> dict:
    data = {
        "id": order.id,
        "number": order.number,
        "date": str(order.date),
        "branch_id": order.branch_id,
        "customer_id": order.customer_id,
        "customer_name": order.customer.name if order.customer else "-",
        "status": order.status,
        "notes": order.notes,
        "creator": order.creator.username if order.creator else "-",
        "item_count": len(order.lines),
        "total_qty": sum(float(line.qty_ordered or 0) for line in order.lines),
    }
    if detail:
        data["lines"] = [
            {
                "id": line.id,
                "bom_id": line.bom_id,
                "product_id": line.product_id,
                "product_name": line.product.name if line.product else "-",
                "product_code": line.product.code if line.product else "-",
                "qty_ordered": line.qty_ordered,
                "qty_processed": line.qty_processed,
                "qty_remaining": max(0.0, float(line.qty_ordered) - float(line.qty_processed or 0)),
            }
            for line in order.lines
        ]
    return data


def _process_line_dict(line: models.AssemblyProcessLine, include_materials: bool = False) -> dict:
    data = {
        "id": line.id,
        "order_line_id": line.order_line_id,
        "bom_id": line.bom_id,
        "product_id": line.product_id,
        "product_name": line.product.name if line.product else "-",
        "product_code": line.product.code if line.product else "-",
        "qty_target": line.qty_target,
        "qty_completed": line.qty_completed,
        "qty_remaining": max(0.0, float(line.qty_target) - float(line.qty_completed or 0)),
        "material_cost": line.material_cost_total,
        "operational_cost": line.operational_cost_total,
        "allocated_cost": line.allocated_cost,
    }
    if include_materials:
        data["materials"] = [
            {
                "material_id": material.material_id,
                "material_name": material.material.name if material.material else "-",
                "qty_required": material.qty_required,
                "total_cost": material.total_cost,
            }
            for material in line.materials
        ]
    return data


def _process_dict(process: models.AssemblyProcess, detail: bool = False) -> dict:
    data = {
        "id": process.id,
        "number": process.number,
        "date": str(process.date),
        "branch_id": process.branch_id,
        "order_id": process.order_id,
        "order_number": process.order.number if process.order else None,
        "customer_id": process.customer_id,
        "customer_name": process.customer.name if process.customer else "-",
        "warehouse_id": process.warehouse_id,
        "warehouse_name": process.warehouse.name if process.warehouse else "-",
        "status": process.status,
        "notes": process.notes,
        "creator": process.creator.username if process.creator else "-",
        "is_legacy": process.is_legacy,
        "item_count": len(process.lines),
        "total_target": sum(float(line.qty_target or 0) for line in process.lines),
        "total_completed": sum(float(line.qty_completed or 0) for line in process.lines),
    }
    if detail:
        data["lines"] = [_process_line_dict(line, True) for line in process.lines]
        data["results"] = [
            {
                "id": result.id,
                "number": result.number,
                "date": str(result.date),
                "status": result.status,
                "closes_process": result.closes_process,
                "total_qty": sum(float(line.qty_finished or 0) for line in result.lines),
            }
            for result in process.results
        ]
    return data


def _result_dict(result: models.AssemblyResult, detail: bool = False) -> dict:
    data = {
        "id": result.id,
        "number": result.number,
        "process_id": result.process_id,
        "process_number": result.process.number if result.process else "-",
        "date": str(result.date),
        "branch_id": result.branch_id,
        "warehouse_id": result.warehouse_id,
        "warehouse_name": result.warehouse.name if result.warehouse else "-",
        "status": result.status,
        "closes_process": result.closes_process,
        "notes": result.notes,
        "creator": result.creator.username if result.creator else "-",
        "total_qty": sum(float(line.qty_finished or 0) for line in result.lines),
        "is_legacy": result.is_legacy,
    }
    if detail:
        data["lines"] = [
            {
                "id": line.id,
                "process_line_id": line.process_line_id,
                "product_id": line.product_id,
                "product_name": line.product.name if line.product else "-",
                "qty_finished": line.qty_finished,
                "unit_cost": line.unit_cost,
                "allocated_cost": line.allocated_cost,
            }
            for line in result.lines
        ]
    return data


def _add_stock_movement(
    db: Session,
    *,
    branch_id: int,
    tx_date: Date,
    item: models.Item,
    movement_type: str,
    qty: float,
    reference: str,
    notes: str,
) -> None:
    before = float(item.stock or 0)
    item.stock = before + qty if movement_type == "in" else before - qty
    db.add(
        models.StockMovement(
            branch_id=branch_id,
            date=tx_date,
            item_id=item.id,
            type=movement_type,
            qty=qty,
            qty_before=before,
            qty_after=item.stock,
            reference=reference,
            notes=notes,
        )
    )


# ---------------------------------------------------------------------------
# Formula / BOM
# ---------------------------------------------------------------------------

@router.get("/bom")
def get_boms(db: Session = Depends(get_db), _=Depends(get_current_user)):
    boms = (
        db.query(models.BillOfMaterial)
        .filter(models.BillOfMaterial.is_active == True)
        .order_by(models.BillOfMaterial.id.desc())
        .all()
    )
    return [
        {
            "id": bom.id,
            "product_id": bom.product_id,
            "product_name": bom.product.name if bom.product else "-",
            "product_code": bom.product.code if bom.product else "-",
            "qty_produced": bom.qty_produced,
            "operational_cost": bom.operational_cost or 0,
            "notes": bom.notes,
            "materials": [
                {
                    "material_id": line.material_id,
                    "material_name": line.material.name if line.material else "-",
                    "material_code": line.material.code if line.material else "-",
                    "unit": line.material.unit.abbreviation if line.material and line.material.unit else "pcs",
                    "qty_needed": line.qty_needed,
                    "current_stock": line.material.stock if line.material else 0,
                }
                for line in bom.materials
            ],
        }
        for bom in boms
    ]


@router.get("/bom/{bom_id}")
def get_bom(bom_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom or not bom.is_active:
        raise HTTPException(404, "Formula tidak ditemukan")
    return next(item for item in get_boms(db, _) if item["id"] == bom_id)


@router.post("/bom")
def create_bom(
    data: BOMCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    product = _validate_bom_payload(
        db, data.product_id, data.qty_produced, data.operational_cost, data.materials
    )
    existing = (
        db.query(models.BillOfMaterial)
        .filter(
            models.BillOfMaterial.product_id == data.product_id,
            models.BillOfMaterial.is_active == True,
        )
        .first()
    )
    if existing:
        raise HTTPException(400, f"Formula untuk {product.name} sudah ada")
    bom = models.BillOfMaterial(
        product_id=data.product_id,
        qty_produced=data.qty_produced,
        operational_cost=data.operational_cost,
        notes=data.notes,
    )
    db.add(bom)
    db.flush()
    for line in data.materials:
        db.add(models.BOMLine(bom_id=bom.id, material_id=line.material_id, qty_needed=line.qty_needed))
    write_audit(db, current_user.id, "CREATE", "bill_of_materials", bom.id, f"Formula {product.name}")
    db.commit()
    return {"id": bom.id, "message": f"Formula untuk {product.name} dibuat"}


@router.put("/bom/{bom_id}")
def update_bom(
    bom_id: int,
    data: BOMUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom or not bom.is_active:
        raise HTTPException(404, "Formula tidak ditemukan")
    product_id = data.product_id if data.product_id is not None else bom.product_id
    qty_produced = data.qty_produced if data.qty_produced is not None else bom.qty_produced
    operational_cost = data.operational_cost if data.operational_cost is not None else float(bom.operational_cost or 0)
    materials = data.materials if data.materials is not None else [
        BOMLineIn(material_id=line.material_id, qty_needed=line.qty_needed) for line in bom.materials
    ]
    product = _validate_bom_payload(db, product_id, qty_produced, operational_cost, materials)
    duplicate = (
        db.query(models.BillOfMaterial)
        .filter(
            models.BillOfMaterial.product_id == product_id,
            models.BillOfMaterial.is_active == True,
            models.BillOfMaterial.id != bom_id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(400, f"Formula untuk {product.name} sudah ada")
    bom.product_id = product_id
    bom.qty_produced = qty_produced
    bom.operational_cost = operational_cost
    if data.notes is not None:
        bom.notes = data.notes
    if data.materials is not None:
        for old in list(bom.materials):
            db.delete(old)
        db.flush()
        for line in materials:
            db.add(models.BOMLine(bom_id=bom.id, material_id=line.material_id, qty_needed=line.qty_needed))
    write_audit(db, current_user.id, "UPDATE", "bill_of_materials", bom.id, f"Update formula {product.name}")
    db.commit()
    return {"id": bom.id, "message": "Formula diperbarui"}


@router.delete("/bom/{bom_id}")
def delete_bom(
    bom_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    bom = db.query(models.BillOfMaterial).get(bom_id)
    if not bom or not bom.is_active:
        raise HTTPException(404, "Formula tidak ditemukan")
    open_line = (
        db.query(models.AssemblyCustomerOrderLine)
        .join(models.AssemblyCustomerOrder)
        .filter(
            models.AssemblyCustomerOrderLine.bom_id == bom_id,
            models.AssemblyCustomerOrder.status.in_(["open", "partially_processed"]),
        )
        .first()
    )
    if open_line:
        raise HTTPException(400, "Formula masih dipakai oleh pesanan yang belum selesai")
    bom.is_active = False
    write_audit(db, current_user.id, "DELETE", "bill_of_materials", bom.id, "Formula dinonaktifkan")
    db.commit()
    return {"message": "Formula dinonaktifkan"}


# ---------------------------------------------------------------------------
# Pesanan perakitan (belum menyentuh stok)
# ---------------------------------------------------------------------------

@router.get("/orders")
def get_orders(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.AssemblyCustomerOrder).filter(
        models.AssemblyCustomerOrder.branch_id == _branch_id(current_user)
    )
    if status:
        query = query.filter(models.AssemblyCustomerOrder.status == status)
    orders = query.order_by(models.AssemblyCustomerOrder.id.desc()).offset(skip).limit(limit).all()
    return [_order_dict(order) for order in orders]


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.AssemblyCustomerOrder)
        .filter(
            models.AssemblyCustomerOrder.id == order_id,
            models.AssemblyCustomerOrder.branch_id == _branch_id(current_user),
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Pesanan perakitan tidak ditemukan")
    return _order_dict(order, True)


@router.post("/orders")
def create_order(
    data: AssemblyOrderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = _branch_id(current_user)
    customer = db.query(models.Customer).filter(
        models.Customer.id == data.customer_id, models.Customer.is_active == True
    ).first()
    if not customer:
        raise HTTPException(404, "Pelanggan tidak ditemukan")
    if not data.lines:
        raise HTTPException(400, "Pesanan harus memiliki minimal satu produk")
    if len({line.bom_id for line in data.lines}) != len(data.lines):
        raise HTTPException(400, "Produk yang sama tidak boleh ditambahkan dua kali")

    order = models.AssemblyCustomerOrder(
        number=_next_number(db, models.AssemblyCustomerOrder, "APO", data.date),
        date=data.date,
        branch_id=branch_id,
        customer_id=customer.id,
        status="open",
        notes=data.notes,
        created_by=current_user.id,
    )
    db.add(order)
    db.flush()
    for payload in data.lines:
        bom = db.query(models.BillOfMaterial).filter(
            models.BillOfMaterial.id == payload.bom_id,
            models.BillOfMaterial.is_active == True,
        ).first()
        if not bom:
            raise HTTPException(404, f"Formula {payload.bom_id} tidak ditemukan")
        if payload.qty_ordered <= EPS:
            raise HTTPException(400, "Jumlah pesanan harus lebih dari 0")
        db.add(models.AssemblyCustomerOrderLine(
            order_id=order.id,
            bom_id=bom.id,
            product_id=bom.product_id,
            qty_ordered=payload.qty_ordered,
            qty_processed=0,
        ))
    write_audit(db, current_user.id, "CREATE", "assembly_customer_orders", order.id, order.number)
    db.commit()
    return {"id": order.id, "number": order.number, "message": "Pesanan perakitan dibuat; stok belum berubah"}


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    data: CancelIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.AssemblyCustomerOrder)
        .filter(
            models.AssemblyCustomerOrder.id == order_id,
            models.AssemblyCustomerOrder.branch_id == _branch_id(current_user),
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Pesanan perakitan tidak ditemukan")
    if order.status == "cancelled":
        raise HTTPException(400, "Pesanan sudah dibatalkan")
    if any(float(line.qty_processed or 0) > EPS for line in order.lines):
        raise HTTPException(400, "Pesanan sudah diproses; batalkan proses terkait terlebih dahulu")
    order.status = "cancelled"
    order.cancelled_at = datetime.now()
    if data.reason:
        order.notes = f"{order.notes or ''}\nPembatalan: {data.reason}".strip()
    write_audit(db, current_user.id, "UPDATE", "assembly_customer_orders", order.id, f"Batalkan {order.number}")
    db.commit()
    return {"id": order.id, "status": order.status, "message": "Pesanan dibatalkan; stok tidak berubah"}


# ---------------------------------------------------------------------------
# Proses perakitan (konsumsi bahan)
# ---------------------------------------------------------------------------

@router.get("/processes")
def get_processes(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.AssemblyProcess).filter(
        models.AssemblyProcess.branch_id == _branch_id(current_user)
    )
    if status:
        query = query.filter(models.AssemblyProcess.status == status)
    rows = query.order_by(models.AssemblyProcess.id.desc()).offset(skip).limit(limit).all()
    return [_process_dict(row) for row in rows]


@router.get("/processes/{process_id}")
def get_process(
    process_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    process = (
        db.query(models.AssemblyProcess)
        .filter(
            models.AssemblyProcess.id == process_id,
            models.AssemblyProcess.branch_id == _branch_id(current_user),
        )
        .first()
    )
    if not process:
        raise HTTPException(404, "Proses perakitan tidak ditemukan")
    return _process_dict(process, True)


@router.post("/processes")
def create_process(
    data: AssemblyProcessCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = _branch_id(current_user)
    warehouse = _warehouse_for_branch(db, data.warehouse_id, branch_id)
    if not data.lines:
        raise HTTPException(400, "Proses harus memiliki minimal satu produk")

    order = None
    customer_id = data.customer_id
    if data.order_id is not None:
        order = (
            db.query(models.AssemblyCustomerOrder)
            .filter(
                models.AssemblyCustomerOrder.id == data.order_id,
                models.AssemblyCustomerOrder.branch_id == branch_id,
            )
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(404, "Pesanan perakitan tidak ditemukan")
        if order.status not in ("open", "partially_processed"):
            raise HTTPException(400, "Pesanan tidak dapat diproses lagi")
        customer_id = order.customer_id
    elif customer_id is not None:
        customer = db.query(models.Customer).filter(
            models.Customer.id == customer_id, models.Customer.is_active == True
        ).first()
        if not customer:
            raise HTTPException(404, "Pelanggan tidak ditemukan")

    process = models.AssemblyProcess(
        number=_next_number(db, models.AssemblyProcess, "APR", data.date),
        date=data.date,
        branch_id=branch_id,
        order_id=order.id if order else None,
        customer_id=customer_id,
        warehouse_id=warehouse.id,
        status="in_progress",
        notes=data.notes,
        created_by=current_user.id,
        is_legacy=False,
    )
    db.add(process)
    db.flush()

    seen_keys = set()
    prepared = []
    aggregate_needed = {}
    for payload in data.lines:
        if payload.qty_target <= EPS:
            raise HTTPException(400, "Target produk harus lebih dari 0")
        order_line = None
        if order:
            if payload.order_line_id is None:
                raise HTTPException(400, "Baris proses dari pesanan harus memilih baris pesanan")
            order_line = next((line for line in order.lines if line.id == payload.order_line_id), None)
            if not order_line:
                raise HTTPException(400, "Baris pesanan tidak sesuai dengan pesanan terpilih")
            remaining = float(order_line.qty_ordered) - float(order_line.qty_processed or 0)
            if payload.qty_target > remaining + EPS:
                raise HTTPException(400, f"Target {order_line.product.name} melebihi sisa pesanan {remaining:g}")
            bom = order_line.bom
            key = ("order", order_line.id)
        else:
            if payload.bom_id is None:
                raise HTTPException(400, "Formula wajib dipilih untuk proses langsung")
            bom = db.query(models.BillOfMaterial).filter(
                models.BillOfMaterial.id == payload.bom_id,
                models.BillOfMaterial.is_active == True,
            ).first()
            key = ("bom", payload.bom_id)
        if key in seen_keys:
            raise HTTPException(400, "Baris produk yang sama tidak boleh diduplikasi")
        seen_keys.add(key)
        if not bom or not bom.product or float(bom.qty_produced or 0) <= EPS:
            raise HTTPException(400, "Formula produk tidak valid")

        product_stock = _stock_item(db, bom.product, lock=False)
        process_line = models.AssemblyProcessLine(
            process_id=process.id,
            order_line_id=order_line.id if order_line else None,
            bom_id=bom.id,
            product_id=bom.product_id,
            product_stock_item_id=product_stock.id,
            qty_target=payload.qty_target,
            qty_completed=0,
            formula_output_qty=bom.qty_produced,
            operational_cost_total=float(bom.operational_cost or 0) * (payload.qty_target / float(bom.qty_produced)),
            material_cost_total=0,
            allocated_cost=0,
        )
        db.add(process_line)
        db.flush()
        material_specs = []
        scale = payload.qty_target / float(bom.qty_produced)
        for bom_line in bom.materials:
            material = db.query(models.Item).get(bom_line.material_id)
            if not material:
                raise HTTPException(400, "Salah satu bahan formula sudah tidak tersedia")
            stock_item = _stock_item(db, material, lock=False)
            qty_required = get_required_stock_qty(material, float(bom_line.qty_needed) * scale)
            material_specs.append((material, stock_item, qty_required))
            aggregate_needed[stock_item.id] = aggregate_needed.get(stock_item.id, 0.0) + qty_required
        if not material_specs:
            raise HTTPException(400, f"Formula {bom.product.name} tidak memiliki bahan")
        prepared.append((process_line, order_line, material_specs))

    shortages = []
    for stock_item_id, required in aggregate_needed.items():
        stock_item = db.query(models.Item).with_for_update().get(stock_item_id)
        available = float(get_warehouse_stock(db, warehouse.id, stock_item_id))
        if available + EPS < required:
            shortages.append(f"{stock_item.name}: butuh {required:g}, tersedia {available:g}")
    if shortages:
        raise HTTPException(400, "Stok bahan tidak cukup:\n" + "\n".join(shortages))

    for process_line, order_line, material_specs in prepared:
        for material, stock_item_ref, required in material_specs:
            stock_item = db.query(models.Item).with_for_update().get(stock_item_ref.id)
            material_line = models.AssemblyProcessMaterial(
                process_line_id=process_line.id,
                material_id=material.id,
                stock_item_id=stock_item.id,
                qty_required=required,
                total_cost=0,
            )
            db.add(material_line)
            db.flush()
            allocations = consume_fifo(db, item_id=stock_item.id, warehouse_id=warehouse.id, qty=required)
            total_cost = 0.0
            for batch, qty, unit_cost in allocations:
                total_cost += float(qty) * float(unit_cost)
                db.add(models.AssemblyMaterialBatchAllocation(
                    material_line_id=material_line.id,
                    batch_id=batch.id if batch else None,
                    qty=qty,
                    unit_cost=unit_cost,
                ))
            material_line.total_cost = total_cost
            process_line.material_cost_total += total_cost
            adjust_warehouse_stock(db, warehouse.id, stock_item.id, -required)
            _add_stock_movement(
                db,
                branch_id=branch_id,
                tx_date=data.date,
                item=stock_item,
                movement_type="out",
                qty=required,
                reference=process.number,
                notes=f"Bahan proses perakitan {process_line.product.name}",
            )
        if order_line:
            order_line.qty_processed = float(order_line.qty_processed or 0) + float(process_line.qty_target)

    if order:
        _refresh_order_status(order)
    write_audit(db, current_user.id, "CREATE", "assembly_processes", process.id, process.number)
    db.commit()
    return {
        "id": process.id,
        "number": process.number,
        "status": process.status,
        "message": "Proses perakitan dimulai; bahan baku telah dikurangi, barang jadi belum bertambah",
    }


@router.post("/processes/{process_id}/cancel")
def cancel_process(
    process_id: int,
    data: CancelIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = _branch_id(current_user)
    process = (
        db.query(models.AssemblyProcess)
        .filter(models.AssemblyProcess.id == process_id, models.AssemblyProcess.branch_id == branch_id)
        .with_for_update()
        .first()
    )
    if not process:
        raise HTTPException(404, "Proses perakitan tidak ditemukan")
    if process.is_legacy:
        raise HTTPException(400, "Histori perakitan lama tidak dapat dibalik otomatis")
    if process.status == "cancelled":
        raise HTTPException(400, "Proses sudah dibatalkan")
    if any(result.status == "posted" for result in process.results):
        raise HTTPException(400, "Balikkan seluruh Proses Jadi terlebih dahulu")
    if not process.warehouse_id:
        raise HTTPException(400, "Gudang proses tidak tersedia")

    for line in process.lines:
        for material in line.materials:
            for allocation in material.allocations:
                if allocation.batch_id:
                    batch = db.query(models.StockBatch).with_for_update().get(allocation.batch_id)
                    if batch:
                        batch.qty_remaining = float(batch.qty_remaining or 0) + float(allocation.qty)
                    else:
                        add_batch(
                            db, item_id=material.stock_item_id, warehouse_id=process.warehouse_id,
                            qty=allocation.qty, unit_cost=allocation.unit_cost, received_date=process.date,
                        )
                else:
                    add_batch(
                        db, item_id=material.stock_item_id, warehouse_id=process.warehouse_id,
                        qty=allocation.qty, unit_cost=allocation.unit_cost, received_date=process.date,
                    )
            stock_item = db.query(models.Item).with_for_update().get(material.stock_item_id)
            adjust_warehouse_stock(db, process.warehouse_id, stock_item.id, material.qty_required)
            _add_stock_movement(
                db, branch_id=branch_id, tx_date=Date.today(), item=stock_item,
                movement_type="in", qty=material.qty_required, reference=f"REV-{process.number}",
                notes="Pembatalan proses perakitan",
            )
        if line.order_line:
            line.order_line.qty_processed = max(
                0.0, float(line.order_line.qty_processed or 0) - float(line.qty_target)
            )
    if process.order:
        _refresh_order_status(process.order)
    process.status = "cancelled"
    process.cancelled_at = datetime.now()
    if data.reason:
        process.notes = f"{process.notes or ''}\nPembatalan: {data.reason}".strip()
    write_audit(db, current_user.id, "UPDATE", "assembly_processes", process.id, f"Batalkan {process.number}")
    db.commit()
    return {"id": process.id, "status": process.status, "message": "Proses dibatalkan dan bahan dikembalikan"}


# ---------------------------------------------------------------------------
# Proses Jadi (posting hasil aktual)
# ---------------------------------------------------------------------------

@router.get("/results")
def get_results(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.AssemblyResult).filter(
        models.AssemblyResult.branch_id == _branch_id(current_user)
    )
    if status:
        query = query.filter(models.AssemblyResult.status == status)
    rows = query.order_by(models.AssemblyResult.id.desc()).offset(skip).limit(limit).all()
    return [_result_dict(row) for row in rows]


@router.get("/results/{result_id}")
def get_result(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    result = (
        db.query(models.AssemblyResult)
        .filter(
            models.AssemblyResult.id == result_id,
            models.AssemblyResult.branch_id == _branch_id(current_user),
        )
        .first()
    )
    if not result:
        raise HTTPException(404, "Proses Jadi tidak ditemukan")
    return _result_dict(result, True)


@router.post("/results")
def create_result(
    data: AssemblyResultCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = _branch_id(current_user)
    process = (
        db.query(models.AssemblyProcess)
        .filter(models.AssemblyProcess.id == data.process_id, models.AssemblyProcess.branch_id == branch_id)
        .with_for_update()
        .first()
    )
    if not process:
        raise HTTPException(404, "Proses perakitan tidak ditemukan")
    if process.is_legacy:
        raise HTTPException(400, "Histori perakitan lama tidak dapat menerima hasil baru")
    if process.status in ("completed", "cancelled"):
        raise HTTPException(400, "Proses sudah ditutup atau dibatalkan")
    if not process.warehouse_id:
        raise HTTPException(400, "Gudang proses tidak tersedia")
    if not data.lines:
        raise HTTPException(400, "Masukkan minimal satu jumlah hasil")
    if len({line.process_line_id for line in data.lines}) != len(data.lines):
        raise HTTPException(400, "Produk hasil yang sama tidak boleh diduplikasi")

    payload_by_id = {line.process_line_id: line for line in data.lines}
    prepared = []
    has_variance = False
    for payload in data.lines:
        process_line = next((line for line in process.lines if line.id == payload.process_line_id), None)
        if not process_line:
            raise HTTPException(400, "Baris hasil tidak sesuai dengan proses")
        if payload.qty_finished <= EPS:
            raise HTTPException(400, "Jumlah hasil harus lebih dari 0")
        new_total = float(process_line.qty_completed or 0) + float(payload.qty_finished)
        if not data.close_process and new_total > float(process_line.qty_target) + EPS:
            raise HTTPException(400, "Hasil melebihi target hanya diperbolehkan saat menutup proses")
        if abs(new_total - float(process_line.qty_target)) > EPS:
            has_variance = True
        prepared.append((process_line, payload.qty_finished))

    projected_totals = {
        line.id: float(line.qty_completed or 0) + float(payload_by_id[line.id].qty_finished)
        if line.id in payload_by_id else float(line.qty_completed or 0)
        for line in process.lines
    }
    auto_close = all(
        abs(projected_totals[line.id] - float(line.qty_target)) <= EPS
        for line in process.lines
    )
    effective_close = data.close_process or auto_close

    if effective_close:
        for process_line in process.lines:
            new_total = float(process_line.qty_completed or 0)
            if process_line.id in payload_by_id:
                new_total += float(payload_by_id[process_line.id].qty_finished)
            if abs(new_total - float(process_line.qty_target)) > EPS:
                has_variance = True
            total_cost = float(process_line.material_cost_total or 0) + float(process_line.operational_cost_total or 0)
            remaining_cost = total_cost - float(process_line.allocated_cost or 0)
            if remaining_cost > 0.0001 and process_line.id not in payload_by_id:
                raise HTTPException(
                    400,
                    f"Masukkan hasil terakhir {process_line.product.name} agar sisa biaya proses dapat dialokasikan",
                )
        if data.close_process and has_variance and not data.confirm_variance:
            raise HTTPException(409, "Jumlah aktual berbeda dari target; kirim confirm_variance=true untuk menutup proses")

    result = models.AssemblyResult(
        number=_next_number(db, models.AssemblyResult, "APJ", data.date),
        process_id=process.id,
        branch_id=branch_id,
        warehouse_id=process.warehouse_id,
        date=data.date,
        status="posted",
        closes_process=effective_close,
        notes=data.notes,
        created_by=current_user.id,
        is_legacy=False,
    )
    db.add(result)
    db.flush()

    for process_line, qty_finished in prepared:
        product = db.query(models.Item).get(process_line.product_id)
        if not product:
            raise HTTPException(400, "Produk hasil sudah tidak tersedia")
        stock_item = db.query(models.Item).with_for_update().get(process_line.product_stock_item_id)
        stock_qty = get_required_stock_qty(product, qty_finished)
        total_cost = float(process_line.material_cost_total or 0) + float(process_line.operational_cost_total or 0)
        remaining_cost = max(0.0, total_cost - float(process_line.allocated_cost or 0))
        if effective_close:
            allocated_cost = remaining_cost
        else:
            planned_unit_cost = total_cost / float(process_line.qty_target) if process_line.qty_target else 0.0
            allocated_cost = min(remaining_cost, float(qty_finished) * planned_unit_cost)
        batch_unit_cost = allocated_cost / stock_qty if stock_qty > EPS else 0.0
        display_unit_cost = allocated_cost / float(qty_finished) if qty_finished > EPS else 0.0
        batch = add_batch(
            db,
            item_id=stock_item.id,
            warehouse_id=process.warehouse_id,
            qty=stock_qty,
            unit_cost=batch_unit_cost,
            received_date=data.date,
        )
        adjust_warehouse_stock(db, process.warehouse_id, stock_item.id, stock_qty)
        _add_stock_movement(
            db, branch_id=branch_id, tx_date=data.date, item=stock_item,
            movement_type="in", qty=stock_qty, reference=result.number,
            notes=f"Hasil perakitan {product.name}",
        )
        stock_item.buy_price = batch_unit_cost
        process_line.qty_completed = float(process_line.qty_completed or 0) + float(qty_finished)
        process_line.allocated_cost = float(process_line.allocated_cost or 0) + allocated_cost
        db.add(models.AssemblyResultLine(
            result_id=result.id,
            process_line_id=process_line.id,
            product_id=product.id,
            stock_item_id=stock_item.id,
            qty_finished=qty_finished,
            stock_qty_finished=stock_qty,
            unit_cost=display_unit_cost,
            allocated_cost=allocated_cost,
            batch_id=batch.id,
        ))

    if effective_close:
        process.status = "completed"
        process.completed_at = datetime.now()
    else:
        process.status = "partially_completed"
    write_audit(db, current_user.id, "CREATE", "assembly_results", result.id, result.number)
    db.commit()
    return {
        "id": result.id,
        "number": result.number,
        "process_status": process.status,
        "has_variance": has_variance,
        "message": "Proses Jadi dicatat; stok produk bertambah sesuai jumlah aktual",
    }


@router.post("/results/{result_id}/reverse")
def reverse_result(
    result_id: int,
    data: CancelIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = _branch_id(current_user)
    result = (
        db.query(models.AssemblyResult)
        .filter(models.AssemblyResult.id == result_id, models.AssemblyResult.branch_id == branch_id)
        .with_for_update()
        .first()
    )
    if not result:
        raise HTTPException(404, "Proses Jadi tidak ditemukan")
    if result.status == "reversed":
        raise HTTPException(400, "Proses Jadi sudah dibalik")
    if result.is_legacy or result.process.is_legacy:
        raise HTTPException(400, "Histori perakitan lama tidak dapat dibalik otomatis")
    newest = (
        db.query(models.AssemblyResult)
        .filter(
            models.AssemblyResult.process_id == result.process_id,
            models.AssemblyResult.status == "posted",
        )
        .order_by(models.AssemblyResult.id.desc())
        .first()
    )
    if not newest or newest.id != result.id:
        raise HTTPException(400, "Balikkan Proses Jadi terbaru terlebih dahulu")

    for line in result.lines:
        batch = db.query(models.StockBatch).with_for_update().get(line.batch_id) if line.batch_id else None
        if not batch or float(batch.qty_remaining or 0) + EPS < float(line.stock_qty_finished):
            raise HTTPException(
                400,
                f"Stok hasil {line.product.name if line.product else '-'} sudah terpakai; reversal tidak dapat dilakukan",
            )

    for line in result.lines:
        batch = db.query(models.StockBatch).with_for_update().get(line.batch_id)
        batch.qty_remaining = max(0.0, float(batch.qty_remaining or 0) - float(line.stock_qty_finished))
        stock_item = db.query(models.Item).with_for_update().get(line.stock_item_id)
        current_stock = float(get_warehouse_stock(db, result.warehouse_id, stock_item.id))
        if current_stock + EPS < float(line.stock_qty_finished):
            raise HTTPException(400, "Stok gudang hasil tidak mencukupi untuk reversal")
        adjust_warehouse_stock(db, result.warehouse_id, stock_item.id, -float(line.stock_qty_finished))
        _add_stock_movement(
            db, branch_id=branch_id, tx_date=Date.today(), item=stock_item,
            movement_type="out", qty=float(line.stock_qty_finished), reference=f"REV-{result.number}",
            notes="Reversal Proses Jadi",
        )
        line.process_line.qty_completed = max(
            0.0, float(line.process_line.qty_completed or 0) - float(line.qty_finished)
        )
        line.process_line.allocated_cost = max(
            0.0, float(line.process_line.allocated_cost or 0) - float(line.allocated_cost or 0)
        )

    result.status = "reversed"
    result.reversed_by = current_user.id
    result.reversed_at = datetime.now()
    if data.reason:
        result.notes = f"{result.notes or ''}\nReversal: {data.reason}".strip()
    active_results = [row for row in result.process.results if row.status == "posted" and row.id != result.id]
    result.process.status = "partially_completed" if active_results else "in_progress"
    result.process.completed_at = None
    write_audit(db, current_user.id, "UPDATE", "assembly_results", result.id, f"Reversal {result.number}")
    db.commit()
    return {"id": result.id, "status": result.status, "message": "Proses Jadi dibalik dan stok hasil dikurangi"}


# ---------------------------------------------------------------------------
# Simulation and one-time compatibility migration
# ---------------------------------------------------------------------------

@router.get("/simulate/{bom_id}")
def simulate_assembly(
    bom_id: int,
    qty: float = 1.0,
    warehouse_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    bom = db.query(models.BillOfMaterial).filter(
        models.BillOfMaterial.id == bom_id,
        models.BillOfMaterial.is_active == True,
    ).first()
    if not bom:
        raise HTTPException(404, "Formula tidak ditemukan")
    if qty <= EPS:
        raise HTTPException(400, "Target hasil harus lebih dari 0")
    branch_id = _branch_id(current_user)
    warehouse = None
    if warehouse_id:
        warehouse = _warehouse_for_branch(db, warehouse_id, branch_id)
    else:
        warehouse = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == branch_id,
            models.Warehouse.is_default == True,
            models.Warehouse.is_active == True,
        ).first()
    if not warehouse:
        raise HTTPException(400, "Pilih gudang untuk simulasi")
    scale = qty / float(bom.qty_produced or 1)
    rows = []
    can_produce = True
    for line in bom.materials:
        material = line.material
        stock_item = _stock_item(db, material, lock=False)
        required = get_required_stock_qty(material, float(line.qty_needed) * scale)
        available = float(get_warehouse_stock(db, warehouse.id, stock_item.id))
        enough = available + EPS >= required
        can_produce = can_produce and enough
        rows.append({
            "material_id": material.id,
            "material_name": material.name,
            "qty_required": required,
            "available": available,
            "enough": enough,
            "shortage": max(0.0, required - available),
        })
    return {
        "bom_id": bom.id,
        "product": bom.product.name if bom.product else "-",
        "qty_target": qty,
        "warehouse_id": warehouse.id,
        "can_produce": can_produce,
        "materials": rows,
    }


def migrate_legacy_assemblies(db: Session) -> int:
    """Salin histori tabel lama ke struktur baru tanpa menyentuh persediaan."""
    migrated = 0
    for old in db.query(models.Assembly).order_by(models.Assembly.id).all():
        exists = db.query(models.AssemblyProcess).filter(
            models.AssemblyProcess.legacy_assembly_id == old.id
        ).first()
        if exists or not old.bom or not old.bom.product:
            continue
        creator = old.creator
        branch_id = None
        if creator:
            branch_id = creator.active_branch_id or creator.branch_id
        if not branch_id:
            first_branch = db.query(models.Branch).order_by(models.Branch.id).first()
            branch_id = first_branch.id if first_branch else None
        warehouse = None
        if branch_id:
            warehouse = db.query(models.Warehouse).filter(
                models.Warehouse.branch_id == branch_id,
                models.Warehouse.is_default == True,
            ).first()
        product_stock = _stock_item(db, old.bom.product, lock=False)
        mapped_status = {
            "done": "completed",
            "completed": "completed",
            "cancelled": "cancelled",
        }.get(old.status, "in_progress")
        process = models.AssemblyProcess(
            number=old.number,
            date=old.date,
            branch_id=branch_id,
            warehouse_id=warehouse.id if warehouse else None,
            status=mapped_status,
            notes=old.notes,
            created_by=old.created_by,
            legacy_assembly_id=old.id,
            is_legacy=True,
            completed_at=old.created_at if mapped_status == "completed" else None,
            cancelled_at=old.created_at if mapped_status == "cancelled" else None,
        )
        db.add(process)
        db.flush()
        qty_target = float(old.qty_planned or 0) * float(old.bom.qty_produced or 1)
        line = models.AssemblyProcessLine(
            process_id=process.id,
            bom_id=old.bom_id,
            product_id=old.bom.product_id,
            product_stock_item_id=product_stock.id,
            qty_target=qty_target,
            qty_completed=float(old.qty_produced or 0),
            formula_output_qty=float(old.bom.qty_produced or 1),
            material_cost_total=0,
            operational_cost_total=0,
            allocated_cost=0,
        )
        db.add(line)
        db.flush()
        if mapped_status == "completed" and float(old.qty_produced or 0) > EPS:
            result = models.AssemblyResult(
                number=f"LEG-{old.number}",
                process_id=process.id,
                branch_id=branch_id,
                warehouse_id=warehouse.id if warehouse else None,
                date=old.date,
                status="posted",
                closes_process=True,
                notes="Migrasi histori perakitan lama; stok tidak diposting ulang",
                created_by=old.created_by,
                is_legacy=True,
            )
            db.add(result)
            db.flush()
            db.add(models.AssemblyResultLine(
                result_id=result.id,
                process_line_id=line.id,
                product_id=old.bom.product_id,
                stock_item_id=product_stock.id,
                qty_finished=float(old.qty_produced),
                stock_qty_finished=get_required_stock_qty(old.bom.product, float(old.qty_produced)),
                unit_cost=0,
                allocated_cost=0,
                batch_id=None,
            ))
        migrated += 1
    if migrated:
        db.commit()
    return migrated
