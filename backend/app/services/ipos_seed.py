"""Seed master data iPos otomatis untuk database FPOS yang benar-benar baru."""
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz
from sqlalchemy.orm import Session

from .. import models, schemas
from ..routes.accounting import ensure_default_accounts
from . import inventory_documents


WITA = pytz.timezone("Asia/Makassar")
SEED_KEY = "ipos-master-data"
DEFAULT_ASSET_NAME = "ipos_seed_v1.json.gz"
EPS = 1e-9


def _now() -> datetime:
    return datetime.now(WITA)


def _asset_path() -> Path:
    override = os.environ.get("IPOS_SEED_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / DEFAULT_ASSET_NAME


def load_seed_payload(path: Path | None = None) -> dict[str, Any]:
    asset = path or _asset_path()
    if not asset.is_file():
        raise FileNotFoundError(f"Aset data awal iPos tidak ditemukan: {asset}")
    with gzip.open(asset, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema_version") != 1 or not payload.get("seed_version"):
        raise ValueError("Versi format aset data awal iPos tidak didukung")
    if len(payload.get("items") or []) != payload.get("source_item_count"):
        raise ValueError("Jumlah barang pada aset data awal iPos tidak konsisten")
    return payload


def seed_status(db: Session) -> dict[str, Any]:
    run = db.query(models.DataSeedRun).filter(models.DataSeedRun.seed_key == SEED_KEY).first()
    if not run:
        return {
            "status": "pending",
            "version": None,
            "counts": {},
            "error": None,
            "started_at": None,
            "completed_at": None,
        }
    try:
        counts = json.loads(run.counts_json) if run.counts_json else {}
    except (TypeError, ValueError):
        counts = {}
    return {
        "status": run.status,
        "version": run.version,
        "counts": counts,
        "error": run.error,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _normalized(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _unit_abbreviation(name: str) -> str:
    known = {
        "pcs": "pcs", "kg": "kg", "kl": "kl", "m": "m", "lt": "lt",
        "ltr": "ltr", "roll": "roll", "set": "set", "pak": "pak",
        "pl": "pl", "ktk": "ktk", "karung": "krg",
    }
    return known.get(_normalized(name), name.strip()[:10].lower())


def _named_master(
    db: Session,
    model,
    cache: dict[str, Any],
    name: str,
    **extra,
):
    key = _normalized(name)
    if key in cache:
        return cache[key]
    obj = model(name=name, **extra)
    db.add(obj)
    db.flush()
    cache[key] = obj
    return obj


def _supplier_parts(raw: str) -> tuple[str, str]:
    value = " ".join(raw.split())
    match = re.match(r"^(SP\d+|S\d+)\s*(.*)$", value, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        name = match.group(2).strip() or code
        return code, name
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())[:35] or "UNKNOWN"
    return f"IPOS-{compact}", value


def _get_supplier(
    db: Session,
    raw: str | None,
    by_raw: dict[str, models.Supplier],
    by_code: dict[str, models.Supplier],
    by_name: dict[str, models.Supplier],
) -> models.Supplier | None:
    if not raw:
        return None
    raw_key = _normalized(raw)
    if raw_key in by_raw:
        return by_raw[raw_key]
    code, name = _supplier_parts(raw)
    obj = by_code.get(code.casefold()) or by_name.get(_normalized(name))
    if not obj:
        obj = models.Supplier(code=code, name=name, is_active=True)
        db.add(obj)
        db.flush()
        by_code[code.casefold()] = obj
        by_name[_normalized(name)] = obj
    by_raw[raw_key] = obj
    return obj


def _base_unit(source: dict[str, Any]) -> dict[str, Any]:
    for unit in source["units"]:
        if abs(float(unit.get("conversion") or 0) - 1.0) < EPS:
            return unit
    raise ValueError(f"Barang {source['code']} tidak memiliki satuan dasar")


def _margin(buy_price: float, sell_price: float) -> float:
    if buy_price <= 0:
        return 0.0
    return round(((sell_price - buy_price) / buy_price) * 100, 8)


def _add_prices(db: Session, item: models.Item, unit: dict[str, Any]) -> None:
    for name, price in (unit.get("group_prices") or {}).items():
        if float(price or 0) > 0:
            db.add(models.ItemPrice(
                item_id=item.id,
                name=name,
                price=float(price),
                min_qty=1,
            ))
    for tier in unit.get("tier_prices") or []:
        if float(tier.get("min_qty") or 0) > 0 and float(tier.get("price") or 0) > 0:
            db.add(models.ItemPrice(
                item_id=item.id,
                name="Grosir",
                price=float(tier["price"]),
                min_qty=float(tier["min_qty"]),
            ))


def _add_supplier_detail(
    db: Session,
    item: models.Item,
    supplier: models.Supplier | None,
    buy_price: float,
    barcode: str | None,
    ppn_percent: float,
) -> None:
    if not supplier:
        return
    db.add(models.ItemSupplier(
        item_id=item.id,
        supplier_id=supplier.id,
        buy_price=buy_price,
        barcode=barcode,
        ppn_type="included",
        ppn_percent=ppn_percent,
    ))


def _apply_seed(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    branch = db.query(models.Branch).order_by(models.Branch.id).first()
    if not branch:
        raise RuntimeError("Cabang utama belum tersedia")
    warehouse = (
        db.query(models.Warehouse)
        .filter(models.Warehouse.branch_id == branch.id, models.Warehouse.is_active == True)
        .order_by(models.Warehouse.is_default.desc(), models.Warehouse.id.asc())
        .first()
    )
    if not warehouse:
        raise RuntimeError("Gudang utama belum tersedia")
    admin = (
        db.query(models.User)
        .filter(models.User.role.ilike("%admin%"), models.User.is_active == True)
        .order_by(models.User.id.asc())
        .first()
    )
    if not admin:
        raise RuntimeError("Akun admin untuk audit seed belum tersedia")
    admin.active_branch_id = branch.id
    if not admin.branch_id:
        admin.branch_id = branch.id

    ensure_default_accounts(db)

    category_cache = {_normalized(row.name): row for row in db.query(models.Category).all()}
    brand_cache = {_normalized(row.name): row for row in db.query(models.Brand).all()}
    unit_cache = {_normalized(row.name): row for row in db.query(models.Unit).all()}
    supplier_by_code = {row.code.casefold(): row for row in db.query(models.Supplier).all()}
    supplier_by_name = {_normalized(row.name): row for row in db.query(models.Supplier).all()}
    supplier_by_raw: dict[str, models.Supplier] = {}

    group_cache = {_normalized(row.name): row for row in db.query(models.CustomerGroup).all()}
    for group_name in ("Level 2", "Level 3", "Level 4"):
        _named_master(db, models.CustomerGroup, group_cache, group_name, discount_percent=0)

    sources = {row["code"]: row for row in payload["items"]}
    excluded = set(payload.get("excluded_codes") or [])
    inactive = set(payload.get("inactive_codes") or [])
    reparent_rows = {row["code"]: row for row in payload.get("reparent_variants") or []}

    item_by_source_code: dict[str, models.Item] = {}
    base_unit_by_source_code: dict[str, dict[str, Any]] = {}
    supplier_by_source_code: dict[str, models.Supplier | None] = {}
    stock_by_item_id: dict[int, float] = {}
    virtual_count = 0

    # Pass 1: seluruh SKU fisik/induk. Semua sementara aktif agar saldo awal dapat
    # diposting atomik; status nonaktif diterapkan setelah dokumen terbentuk.
    for code in sorted(sources):
        if code in excluded or code in reparent_rows:
            continue
        source = sources[code]
        base = _base_unit(source)
        category = _named_master(db, models.Category, category_cache, source["category"])
        brand = _named_master(db, models.Brand, brand_cache, source["brand"])
        unit = _named_master(
            db, models.Unit, unit_cache, base["name"], abbreviation=_unit_abbreviation(base["name"])
        )
        supplier = _get_supplier(
            db, source.get("supplier"), supplier_by_raw, supplier_by_code, supplier_by_name
        )
        buy_price = float(base.get("buy_price") or 0)
        sell_price = float(base.get("sell_price") or 0)
        item = models.Item(
            code=code,
            name=source["name"],
            category_id=category.id,
            brand_id=brand.id,
            unit_id=unit.id,
            buy_price=buy_price,
            sell_price=sell_price,
            profit_margin=_margin(buy_price, sell_price),
            ppn_percent=float(source.get("ppn_percent") or 0),
            stock=0,
            min_stock=float(source.get("min_stock") or 0),
            description=source.get("description"),
            barcode=base.get("barcode"),
            is_virtual_variant=False,
            is_active=True,
            is_discountable=True,
        )
        db.add(item)
        db.flush()
        _add_prices(db, item, base)
        _add_supplier_detail(
            db, item, supplier, buy_price, base.get("barcode"), float(source.get("ppn_percent") or 0)
        )
        db.add(models.WarehouseStock(warehouse_id=warehouse.id, item_id=item.id, stock=0))
        item_by_source_code[code] = item
        base_unit_by_source_code[code] = base
        supplier_by_source_code[code] = supplier
        stock_by_item_id[item.id] = float(source.get("stock") or 0)

    # Pass 2: satuan lain dalam SKU yang sama menjadi child virtual dengan kode
    # stabil CODE-U<slot>. Modal selalu mengikuti KONVERSI, bukan rasio HPP Excel.
    for code in sorted(item_by_source_code):
        source = sources[code]
        parent = item_by_source_code[code]
        base = base_unit_by_source_code[code]
        supplier = supplier_by_source_code[code]
        for unit_payload in source["units"]:
            if int(unit_payload["slot"]) == int(base["slot"]):
                continue
            factor = float(unit_payload.get("conversion") or 0)
            if factor <= 0:
                continue
            unit = _named_master(
                db,
                models.Unit,
                unit_cache,
                unit_payload["name"],
                abbreviation=_unit_abbreviation(unit_payload["name"]),
            )
            buy_price = float(parent.buy_price or 0) * factor
            direct_sell = float(unit_payload.get("sell_price") or 0)
            sell_price = direct_sell or float(parent.sell_price or 0) * factor
            child = models.Item(
                code=f"{code}-U{unit_payload['slot']}",
                name=f"{source['name']} [{unit_payload['name']}]"[:200],
                category_id=parent.category_id,
                brand_id=parent.brand_id,
                unit_id=unit.id,
                buy_price=buy_price,
                sell_price=sell_price,
                profit_margin=_margin(buy_price, sell_price),
                ppn_percent=parent.ppn_percent,
                stock=0,
                min_stock=0,
                description=parent.description,
                barcode=unit_payload.get("barcode"),
                parent_item_id=parent.id,
                conversion_factor_to_parent=factor,
                is_virtual_variant=True,
                is_active=parent.is_active,
                is_discountable=True,
            )
            db.add(child)
            db.flush()
            _add_prices(db, child, unit_payload)
            _add_supplier_detail(
                db, child, supplier, buy_price, unit_payload.get("barcode"), float(parent.ppn_percent or 0)
            )
            db.add(models.UnitConversion(
                item_id=parent.id,
                child_item_id=child.id,
                unit_id=parent.unit_id,
                base_unit_id=child.unit_id,
                conversion_factor=factor,
                buy_price=buy_price,
                sell_price=sell_price,
                is_active=True,
            ))
            virtual_count += 1

    # Pass 3: SKU terpisah yang telah diputuskan menjadi satuan virtual dari SKU lain.
    for code in sorted(reparent_rows):
        directive = reparent_rows[code]
        source = sources[code]
        parent = item_by_source_code.get(directive["parent_code"])
        if not parent:
            raise ValueError(f"Induk virtual {directive['parent_code']} untuk {code} tidak ditemukan")
        own_base = _base_unit(source)
        factor = float(directive["factor"])
        unit = _named_master(
            db,
            models.Unit,
            unit_cache,
            own_base["name"],
            abbreviation=_unit_abbreviation(own_base["name"]),
        )
        category = _named_master(db, models.Category, category_cache, source["category"])
        brand = _named_master(db, models.Brand, brand_cache, source["brand"])
        supplier = _get_supplier(
            db, source.get("supplier"), supplier_by_raw, supplier_by_code, supplier_by_name
        )
        buy_price = float(parent.buy_price or 0) * factor
        sell_price = float(own_base.get("sell_price") or 0) or float(parent.sell_price or 0) * factor
        child = models.Item(
            code=code,
            name=source["name"],
            category_id=category.id,
            brand_id=brand.id,
            unit_id=unit.id,
            buy_price=buy_price,
            sell_price=sell_price,
            profit_margin=_margin(buy_price, sell_price),
            ppn_percent=float(source.get("ppn_percent") or 0),
            stock=0,
            min_stock=0,
            description=source.get("description"),
            barcode=own_base.get("barcode"),
            parent_item_id=parent.id,
            conversion_factor_to_parent=factor,
            is_virtual_variant=True,
            is_active=True,
            is_discountable=True,
        )
        db.add(child)
        db.flush()
        _add_prices(db, child, own_base)
        _add_supplier_detail(
            db, child, supplier, buy_price, own_base.get("barcode"), float(child.ppn_percent or 0)
        )
        db.add(models.UnitConversion(
            item_id=parent.id,
            child_item_id=child.id,
            unit_id=parent.unit_id,
            base_unit_id=child.unit_id,
            conversion_factor=factor,
            buy_price=buy_price,
            sell_price=sell_price,
            is_active=True,
        ))
        stock_by_item_id[parent.id] = stock_by_item_id.get(parent.id, 0) + float(source.get("stock") or 0) * factor
        virtual_count += 1

    # Delapan formula dipakai untuk produksi berikutnya. Stok produk jadi yang
    # sudah ada di Excel tetap diposting apa adanya dan tidak mengonsumsi bahan.
    for bom_payload in payload.get("boms") or []:
        product = item_by_source_code.get(bom_payload["product_code"])
        if not product:
            raise ValueError(f"Produk BOM {bom_payload['product_code']} tidak ditemukan")
        bom = models.BillOfMaterial(
            product_id=product.id,
            qty_produced=1,
            operational_cost=0,
            notes="Formula hasil migrasi data iPos",
            is_active=True,
        )
        db.add(bom)
        db.flush()
        for material_payload in bom_payload["materials"]:
            material = item_by_source_code.get(material_payload["code"])
            if not material:
                raise ValueError(f"Bahan BOM {material_payload['code']} tidak ditemukan")
            db.add(models.BOMLine(
                bom_id=bom.id,
                material_id=material.id,
                qty_needed=float(material_payload["qty"]),
            ))

    positive_lines = [
        schemas.InventoryDocumentLineCreate(item_id=item_id, qty=qty)
        for item_id, qty in sorted(stock_by_item_id.items())
        if qty > EPS
    ]
    opening_document = None
    if positive_lines:
        opening_document = inventory_documents.create_document(
            db,
            schemas.InventoryDocumentCreate(
                type="opening_stock",
                date=inventory_documents.local_date(),
                warehouse_id=warehouse.id,
                notes=f"Saldo awal otomatis {payload['seed_version']}",
                lines=positive_lines,
            ),
            admin,
        )

    for code in inactive:
        item = item_by_source_code.get(code)
        if item:
            item.is_active = False

    db.flush()
    return {
        "source_items": int(payload["source_item_count"]),
        "excluded_items": len(excluded),
        "items": db.query(models.Item).count(),
        "virtual_variants": virtual_count,
        "categories": len(category_cache),
        "brands": len(brand_cache),
        "units": len(unit_cache),
        "suppliers": len(supplier_by_code),
        "customer_groups": 3,
        "boms": len(payload.get("boms") or []),
        "opening_stock_lines": len(positive_lines),
        "opening_stock_document": opening_document.number if opening_document else None,
    }


def run_automatic_seed(db: Session) -> dict[str, Any]:
    """Jalankan sekali untuk DB kosong; kegagalan dapat dicoba lagi saat restart."""
    try:
        payload = load_seed_payload()
        version = str(payload["seed_version"])
    except Exception as exc:
        # Tabel status sudah dibuat oleh create_all. Catat error aset agar menu
        # onboarding memberi penyebab yang dapat ditindaklanjuti.
        version = "unknown"
        payload = None
        load_error = exc
    else:
        load_error = None

    run = db.query(models.DataSeedRun).filter(models.DataSeedRun.seed_key == SEED_KEY).first()
    if run and run.status in {"completed", "skipped_existing_data"}:
        return seed_status(db)

    if not run:
        run = models.DataSeedRun(seed_key=SEED_KEY, version=version, status="pending")
        db.add(run)
        db.flush()

    if load_error is not None:
        run.version = version
        run.status = "failed"
        run.error = str(load_error)[:4000]
        run.completed_at = None
        db.commit()
        return seed_status(db)

    # Tidak menyentuh instalasi lama. Marker skipped mencegah data tiba-tiba
    # masuk bila seluruh barang instalasi tersebut kelak dihapus manual.
    if db.query(models.Item.id).first():
        run.version = version
        run.status = "skipped_existing_data"
        run.error = None
        run.completed_at = _now()
        db.commit()
        return seed_status(db)

    run.version = version
    run.status = "running"
    run.error = None
    run.counts_json = None
    run.started_at = _now()
    run.completed_at = None
    db.commit()

    try:
        counts = _apply_seed(db, payload)
        run = db.query(models.DataSeedRun).filter(models.DataSeedRun.seed_key == SEED_KEY).one()
        run.status = "completed"
        run.counts_json = json.dumps(counts, ensure_ascii=False, sort_keys=True)
        run.error = None
        run.completed_at = _now()
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.query(models.DataSeedRun).filter(models.DataSeedRun.seed_key == SEED_KEY).first()
        if not run:
            run = models.DataSeedRun(seed_key=SEED_KEY, version=version)
            db.add(run)
        run.status = "failed"
        run.error = str(exc)[:4000]
        run.completed_at = None
        db.commit()
    return seed_status(db)
