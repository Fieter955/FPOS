"""
iPos 5.0 — Generate Barcode Internal
Untuk toko bangunan yang banyak barang tanpa barcode supplier.

Format barcode yang didukung:
  - CODE128: universal, bisa angka + huruf, paling umum di thermal printer
  - EAN13: 13 digit angka, standar retail (diisi otomatis)
  - QR: bisa scan dari HP, lebih banyak info
  - CODE39: kompatibel printer lama

Output: PNG base64 yang bisa langsung ditampilkan & diprint
"""
import io, base64, hashlib, re
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional

from ..database import get_db
from ..auth import get_current_user
from .. import models

router = APIRouter()


def _generate_barcode_value(item_code: str, barcode_type: str) -> str:
    """Generate nilai barcode unik berdasarkan kode item"""
    if barcode_type == "EAN13":
        # EAN13 membutuhkan 12 digit dasar. Kode item internal biasanya
        # berbentuk ITM-ABC123, jadi tidak aman langsung menjalankan int(d)
        # terhadap karakter kode tersebut. Gunakan token numerik deterministik
        # dari seluruh kode agar kode tanpa angka tetap valid dan tidak mudah
        # bertabrakan.
        token = int(hashlib.sha256(str(item_code).encode("utf-8")).hexdigest()[:15], 16)
        digits = "899" + str(token % 1_000_000_000).zfill(9)
        # Hitung check digit EAN13
        total = sum(int(d) * (1 if i % 2 == 0 else 3)
                   for i, d in enumerate(digits))
        check = (10 - (total % 10)) % 10
        return digits + str(check)
    else:
        # CODE128 / QR / CODE39: pakai prefix + kode item
        return f"IPS{item_code.upper().replace(' ','').replace('-','')}"


def _normalize_barcode(value: str) -> str:
    return str(value or "").strip().casefold()


def assert_barcode_available(
    db: Session,
    value: str,
    exclude_item_id: int | None = None,
) -> None:
    """Tolak barcode yang sudah menunjuk ke item lain.

    Barcode supplier dan BarcodeLabel ikut diperiksa karena keduanya juga
    dipakai sebagai identitas scan. Perbandingan dibuat case-insensitive dan
    mengabaikan spasi tepi, sama seperti frontend.
    """
    normalized = _normalize_barcode(value)
    if not normalized or normalized == "auto":
        return

    item_query = db.query(models.Item).filter(
        func.lower(func.trim(models.Item.barcode)) == normalized,
    )
    supplier_query = db.query(models.ItemSupplier).join(
        models.Item, models.Item.id == models.ItemSupplier.item_id
    ).filter(
        func.lower(func.trim(models.ItemSupplier.barcode)) == normalized,
    )
    label_query = db.query(models.BarcodeLabel).filter(
        func.lower(func.trim(models.BarcodeLabel.barcode_value)) == normalized,
    )

    if exclude_item_id is not None:
        item_query = item_query.filter(models.Item.id != exclude_item_id)
        supplier_query = supplier_query.filter(models.Item.id != exclude_item_id)
        label_query = label_query.filter(models.BarcodeLabel.item_id != exclude_item_id)

    conflict = item_query.first()
    if conflict:
        raise HTTPException(400, f"Barcode '{value}' sudah digunakan oleh barang '{conflict.name}'.")

    conflict_supplier = supplier_query.first()
    if conflict_supplier:
        raise HTTPException(
            400,
            f"Barcode '{value}' sudah digunakan sebagai barcode supplier oleh barang '{conflict_supplier.item.name}'.",
        )

    conflict_label = label_query.first()
    if conflict_label:
        conflict_item = db.query(models.Item).get(conflict_label.item_id)
        name = conflict_item.name if conflict_item else str(conflict_label.item_id)
        raise HTTPException(400, f"Barcode '{value}' sudah digunakan oleh barang '{name}'.")


def _validate_ean13(value: str) -> None:
    if not re.fullmatch(r"\d{13}", value):
        raise HTTPException(400, "Barcode EAN13 harus terdiri dari tepat 13 digit.")

    total = sum(int(digit) * (1 if index % 2 == 0 else 3)
                for index, digit in enumerate(value[:12]))
    check_digit = (10 - (total % 10)) % 10
    if int(value[-1]) != check_digit:
        raise HTTPException(400, "Digit pemeriksa barcode EAN13 tidak valid.")


def _render_barcode(barcode_value: str, barcode_type: str,
                    label_text: str = "") -> str:
    """
    Render barcode ke PNG base64.
    Coba gunakan python-barcode, fallback ke SVG sederhana.
    """
    try:
        import barcode
        from barcode.writer import ImageWriter

        buf = io.BytesIO()
        if barcode_type == "EAN13":
            bc = barcode.get("ean13", barcode_value, writer=ImageWriter())
        elif barcode_type == "CODE39":
            bc = barcode.get("code39", barcode_value, writer=ImageWriter())
        else:
            bc = barcode.get("code128", barcode_value, writer=ImageWriter())

        bc.write(buf, options={
            "module_width": 0.4,
            "module_height": 12,
            "quiet_zone": 0,
            "write_text": False,
        })
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    except ImportError:
        raise RuntimeError(
            "python-barcode tidak terinstall. "
            "Jalankan: pip install python-barcode[images]"
        )


def _render_svg_barcode(value: str, label: str = "") -> str:
    """Fallback SVG barcode — simple visual representation (tanpa teks, ditampilkan via .st-bot di HTML)"""
    bars = []
    x = 10
    # Simple pattern from value characters
    for char in value:
        width = 1 + (ord(char) % 3)
        color = "black" if ord(char) % 2 == 0 else "white"
        bars.append(f'<rect x="{x}" y="5" width="{width}" height="40" fill="{color}"/>')
        x += width + 1

    total_width = x + 10
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="65">
  <rect width="{total_width}" height="65" fill="white"/>
  {"".join(bars)}
</svg>'''
    return base64.b64encode(svg.encode()).decode()


# ─── Routes ───────────────────────────────────────────────────────────────────

class BarcodeRequest(BaseModel):
    item_id: int
    barcode_type: str = "CODE128"   # CODE128 | EAN13 | QR | CODE39
    custom_value: Optional[str] = None
    label_text: Optional[str] = None
    qty_labels: int = 1


@router.post("/generate")
def generate_barcode(
    data: BarcodeRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Generate barcode untuk item.
    Kalau sudah ada barcode di database → pakai yang ada.
    Kalau belum → generate baru dan simpan.
    """
    item = db.query(models.Item).get(data.item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")

    valid_types = ["CODE128", "EAN13", "CODE39"]
    if data.barcode_type not in valid_types:
        raise HTTPException(400, f"Tipe barcode harus: {valid_types}")

    # Cek apakah item sudah punya barcode. Jika ada label lama, pertahankan
    # tipe aslinya agar barcode CODE128 lama tidak dirender sebagai EAN13.
    barcode_value = _normalize_barcode(data.custom_value)
    if barcode_value == "auto":
        barcode_value = ""
    existing_item_label = None
    render_type = data.barcode_type
    if not barcode_value:
        if item.barcode:
            barcode_value = _normalize_barcode(item.barcode)
            existing_item_label = db.query(models.BarcodeLabel).filter(
                models.BarcodeLabel.item_id == data.item_id,
                func.lower(func.trim(models.BarcodeLabel.barcode_value)) == barcode_value,
            ).first()
            if existing_item_label and existing_item_label.barcode_type in valid_types:
                render_type = existing_item_label.barcode_type
        else:
            barcode_value = _generate_barcode_value(item.code, data.barcode_type)

    if render_type == "EAN13":
        _validate_ean13(barcode_value)

    # Jangan biarkan barcode yang sama mengarah ke beberapa item. Pemeriksaan
    # dilakukan sebelum render/commit supaya error menjadi pesan 400 yang jelas.
    assert_barcode_available(db, barcode_value, exclude_item_id=data.item_id)

    # Label text default = nama item
    label_text = data.label_text or item.name[:30]

    # Render ke gambar
    image_b64 = _render_barcode(barcode_value, render_type, label_text)

    # Simpan ke database jika belum ada
    existing = db.query(models.BarcodeLabel).filter(
        models.BarcodeLabel.item_id == data.item_id,
        func.lower(func.trim(models.BarcodeLabel.barcode_value)) == barcode_value,
    ).first()

    needs_commit = False
    if not existing:
        label_record = models.BarcodeLabel(
            item_id=data.item_id,
            barcode_value=barcode_value,
            barcode_type=render_type,
            label_text=label_text,
        )
        db.add(label_record)
        needs_commit = True

    # Pastikan item ikut menyimpan barcode final yang benar
    if item.barcode != barcode_value:
        item.barcode = barcode_value
        needs_commit = True

    if needs_commit:
        db.commit()
        db.refresh(item)

    return {
        "item_id": data.item_id,
        "item_name": item.name,
        "item_code": item.code,
        "barcode_value": barcode_value,
        "barcode_type": render_type,
        "label_text": label_text,
        "image_base64": image_b64,
        "print_qty": data.qty_labels,
        "sell_price": item.sell_price,
    }


@router.post("/generate-batch")
def generate_batch(
    data: dict,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Generate barcode untuk banyak item sekaligus (item tanpa barcode)"""
    items_without_barcode = db.query(models.Item).filter(
        models.Item.is_active == True,
        (models.Item.barcode == None) | (models.Item.barcode == "")
    ).limit(50).all()

    results = []
    for item in items_without_barcode:
        barcode_value = _generate_barcode_value(item.code, "CODE128")
        item.barcode = barcode_value

        label_record = models.BarcodeLabel(
            item_id=item.id,
            barcode_value=barcode_value,
            barcode_type="CODE128",
            label_text=item.name[:30],
        )
        db.add(label_record)
        results.append({"id": item.id, "name": item.name, "barcode": barcode_value})

    db.commit()
    return {
        "message": f"{len(results)} barcode digenerate",
        "items": results
    }


@router.get("/item/{item_id}")
def get_item_barcodes(
    item_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    item = db.query(models.Item).get(item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")

    labels = db.query(models.BarcodeLabel).filter(
        models.BarcodeLabel.item_id == item_id
    ).all()

    return {
        "item": {"id": item.id, "name": item.name, "barcode": item.barcode},
        "labels": [{"id": l.id, "value": l.barcode_value,
                    "type": l.barcode_type, "printed": l.printed_count}
                   for l in labels]
    }


@router.get("/without-barcode")
def items_without_barcode(
    limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """List semua item yang belum punya barcode"""
    items = db.query(models.Item).filter(
        models.Item.is_active == True,
        (models.Item.barcode == None) | (models.Item.barcode == "")
    ).limit(limit).all()

    return {
        "count": len(items),
        "items": [{"id": i.id, "code": i.code, "name": i.name} for i in items]
    }
