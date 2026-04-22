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
import io, base64, random, string
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..database import get_db
from ..auth import get_current_user
from .. import models

router = APIRouter()


def _generate_barcode_value(item_code: str, barcode_type: str) -> str:
    """Generate nilai barcode unik berdasarkan kode item"""
    if barcode_type == "EAN13":
        # EAN13: 12 digit + 1 check digit
        # Prefix 899 = Indonesia
        digits = "899" + item_code.replace("-","").replace(" ","")[:9].zfill(9)
        digits = digits[:12]
        # Hitung check digit EAN13
        total = sum(int(d) * (1 if i % 2 == 0 else 3)
                   for i, d in enumerate(digits))
        check = (10 - (total % 10)) % 10
        return digits + str(check)
    else:
        # CODE128 / QR / CODE39: pakai prefix + kode item
        return f"IPS{item_code.upper().replace(' ','').replace('-','')}"


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
            "quiet_zone": 2,
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

    # Cek apakah item sudah punya barcode
    barcode_value = data.custom_value
    if not barcode_value:
        if item.barcode:
            barcode_value = item.barcode
        else:
            barcode_value = _generate_barcode_value(item.code, data.barcode_type)

    # Label text default = nama item
    label_text = data.label_text or item.name[:30]

    # Render ke gambar
    image_b64 = _render_barcode(barcode_value, data.barcode_type, label_text)

    # Simpan ke database jika belum ada
    existing = db.query(models.BarcodeLabel).filter(
        models.BarcodeLabel.item_id == data.item_id,
        models.BarcodeLabel.barcode_value == barcode_value
    ).first()

    if not existing:
        label_record = models.BarcodeLabel(
            item_id=data.item_id,
            barcode_value=barcode_value,
            barcode_type=data.barcode_type,
            label_text=label_text,
        )
        db.add(label_record)

        # Update barcode di item jika belum ada
        if not item.barcode:
            item.barcode = barcode_value

        db.commit()

    return {
        "item_id": data.item_id,
        "item_name": item.name,
        "item_code": item.code,
        "barcode_value": barcode_value,
        "barcode_type": data.barcode_type,
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