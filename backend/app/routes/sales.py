# ══════════════════════════════════════════════════════════════════════════════
# iPos 5.0 — Modul Penjualan (Sales)
# Versi: Fixed — Journal Diskon, Piutang, & Duplikasi Kode
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, date
import pytz
import unicodedata
import textwrap

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, get_query, write_audit
from ..services.virtual_units import (
    get_effective_buy_price,
    get_effective_stock_from_source,
    get_required_stock_qty,
    is_virtual_variant,
)
from ..services.inventory_fifo import consume_fifo, record_allocations, restore_allocations
from .accounting import create_auto_journal  # ✅ Import di atas, sekali saja

router = APIRouter()


def hitung_total_penjualan(gross_subtotal: float, disc_persen: float, tarif_persen: float,
                           termasuk_ppn: bool) -> dict:
    """Hitung subtotal/diskon/PPN/total satu penjualan.
    - tarif_persen<=0  → tanpa PPN (perilaku lama, identik dengan sebelum fitur PKP).
    - termasuk_ppn=True  (harga jual SUDAH termasuk PPN) → PPN dikupas MUNDUR; total = harga.
    - termasuk_ppn=False (PPN ditambah di atas)          → PPN ditambahkan; total = harga + PPN.
    subtotal & discount yang dikembalikan SELALU ex-PPN supaya Pendapatan (4-1100) bersih dan
    jurnal balance: 4-1100=subtotal, 4-1150=discount, 2-1200=tax, kas/piutang=total."""
    disc_gross = gross_subtotal * ((disc_persen or 0) / 100)
    if tarif_persen and tarif_persen > 0:
        f = 1 + tarif_persen / 100
        if termasuk_ppn:
            after = gross_subtotal - disc_gross          # yang benar-benar dibayar pelanggan
            subtotal = gross_subtotal / f
            diskon = disc_gross / f
            pajak = after - after / f
            total = after
        else:
            subtotal = gross_subtotal
            diskon = disc_gross
            pajak = (gross_subtotal - disc_gross) * (tarif_persen / 100)
            total = gross_subtotal - disc_gross + pajak
    else:
        subtotal = gross_subtotal
        diskon = disc_gross
        pajak = 0.0
        total = gross_subtotal - disc_gross
    return {"subtotal": subtotal, "discount": diskon, "tax": pajak, "total": total}


def hitung_total_penjualan_per_baris(baris: list, disc_persen: float, termasuk_ppn: bool) -> dict:
    """Versi PER-BARIS dari hitung_total_penjualan: tiap baris boleh punya tarif PPN sendiri
    (mis. barang kena PPN 11% dicampur barang non-PPN 0%). `baris` = list of {"gross","tarif"} di
    mana gross = sell_price*(1-disc/100)*qty. Diskon header (disc_persen) disebar proporsional.
    Hasil identik dengan hitung_total_penjualan bila semua baris bertarif sama (nol regresi)."""
    disc_ratio = (disc_persen or 0) / 100
    subtotal = diskon = pajak = total = 0.0
    for b in baris:
        g = float(b.get("gross") or 0)
        tarif = float(b.get("tarif") or 0)
        dg = g * disc_ratio
        if tarif > 0:
            f = 1 + tarif / 100
            if termasuk_ppn:
                after = g - dg                # yang benar-benar dibayar pelanggan utk baris ini
                subtotal += g / f
                diskon   += dg / f
                pajak    += after - after / f
                total    += after
            else:
                subtotal += g
                diskon   += dg
                p = (g - dg) * (tarif / 100)
                pajak    += p
                total    += g - dg + p
        else:
            subtotal += g
            diskon   += dg
            total    += g - dg
    return {"subtotal": subtotal, "discount": diskon, "tax": pajak, "total": total}


def _is_admin_user(user: models.User) -> bool:
    return (user.role or "") == "admin"


def _sale_out_for_user(sale: models.Sale, current_user: models.User):
    data = schemas.SaleOut.model_validate(sale).model_dump()
    for line in data.get("items") or []:
        qty = float(line.get("qty") or 0)
        buy_price = float(line.get("buy_price") or 0)
        line_total = float(line.get("total") or 0)
        margin_amount = line_total - (buy_price * qty)
        line["margin_amount"] = round(margin_amount, 2)
        line["margin_percent"] = (
            round((margin_amount / (buy_price * qty)) * 100, 2)
            if buy_price > 0 and qty > 0
            else (100 if margin_amount > 0 else 0)
        )

    if not _is_admin_user(current_user):
        for line in data.get("items") or []:
            line["buy_price"] = 0
            line["margin_amount"] = 0
            line["margin_percent"] = 0
            item = line.get("item")
            if item:
                item["buy_price"] = 0
    return data

# ── Zona Waktu ────────────────────────────────────────────────────────────────
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    return datetime.now(WITA).date()

def get_local_datetime():
    return datetime.now(WITA)


# ── Helper Printer ────────────────────────────────────────────────────────────
def printer_safe(text: str, max_len: int = None) -> str:
    """Konversi string agar aman untuk printer thermal ESC/POS."""
    if not text:
        return ""
    text = str(text).replace('\xa0', ' ')
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


def _get_locked_stock_item(db: Session, item: models.Item, require_active: bool = True):
    item_map = {item.id: item}
    if not is_virtual_variant(item):
        return item, item_map

    parent = db.query(models.Item).with_for_update().get(item.parent_item_id)
    if not parent or (require_active and not parent.is_active):
        raise HTTPException(
            400,
            f"Barang induk untuk {item.name} tidak ditemukan atau sudah nonaktif.",
        )

    item_map[parent.id] = parent
    return parent, item_map


# ── Penomoran Faktur ──────────────────────────────────────────────────────────
def _next_number(db: Session, current_user: models.User) -> str:
    today = get_local_date()
    cabang_id = current_user.active_branch_id or 0
    prefix = f"INV-C{cabang_id}-{today.strftime('%Y%m%d')}"
    last = get_query(db, models.Sale, current_user).filter(
        models.Sale.number.like(f"{prefix}%")
    ).order_by(models.Sale.id.desc()).first()
    if last and len(last.number) >= 4:
        try:
            seq = int(last.number[-4:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


# ══════════════════════════════════════════════════════════════════════════════
# GET — Daftar & Detail Penjualan
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=list[schemas.SaleOut])
def get_sales(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    q = get_query(db, models.Sale, current_user)
    if start_date: q = q.filter(models.Sale.date >= start_date)
    if end_date:   q = q.filter(models.Sale.date <= end_date)
    if customer_id: q = q.filter(models.Sale.customer_id == customer_id)
    if status:     q = q.filter(models.Sale.status == status)
    sales = q.order_by(models.Sale.id.desc()).offset(skip).limit(limit).all()
    return [_sale_out_for_user(sale, current_user) for sale in sales]


@router.get("/{sid}", response_model=schemas.SaleOut)
def get_sale(
    sid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    obj = get_query(db, models.Sale, current_user).filter(models.Sale.id == sid).first()
    if not obj:
        raise HTTPException(404, "Penjualan tidak ditemukan")
    return _sale_out_for_user(obj, current_user)


# ══════════════════════════════════════════════════════════════════════════════
# POST — Buat Penjualan Baru
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/")
def create_sale(
    data: schemas.SaleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    local_date     = get_local_date()
    local_datetime = get_local_datetime()

    # ── Cek Shift Kasir ───────────────────────────────────────────────────────
    active_shift = get_query(db, models.Shift, current_user).filter(
        models.Shift.user_id == current_user.id,
        models.Shift.status  == "open"
    ).first()
    if not active_shift:
        raise HTTPException(400, "Anda belum membuka shift kasir hari ini.")

    # ── Kalkulasi Header ──────────────────────────────────────────────────────
    number      = data.number or _next_number(db, current_user)
    gross_subtotal = sum((it.sell_price * (1 - it.discount / 100)) * it.qty for it in data.items)
    is_inc = data.is_tax_included if data.is_tax_included is not None else True
    # Tarif PPN efektif TIAP BARIS (PKP). Urutan sumber tarif:
    #   tarif yang dikirim baris → tarif barang (Item.ppn_percent) → tarif transaksi (data.tax).
    # data.tax<=0 (non-PKP / struk biasa) → semua 0 → hasil identik perilaku lama (nol regresi).
    line_rates = []
    if (data.tax or 0) > 0:
        _ids = [it.item_id for it in data.items]
        _rows = db.query(models.Item.id, models.Item.ppn_percent).filter(models.Item.id.in_(_ids)).all()
        _ppn_map = {rid: rppn for (rid, rppn) in _rows}
        for it in data.items:
            if it.ppn_percent is not None:
                line_rates.append(float(it.ppn_percent))
            else:
                mp = _ppn_map.get(it.item_id)
                line_rates.append(float(mp) if mp is not None else float(data.tax or 0))
    else:
        line_rates = [0.0 for _ in data.items]

    # PPN penjualan dihitung PER BARIS (harga jual dianggap SUDAH termasuk PPN saat termasuk_ppn),
    # lalu dijumlah → subtotal/discount/tax/total. Konsisten dengan jurnal (4-1100/4-1150/2-1200).
    _baris = [
        {"gross": (it.sell_price * (1 - it.discount / 100)) * it.qty, "tarif": line_rates[idx]}
        for idx, it in enumerate(data.items)
    ]
    _t = hitung_total_penjualan_per_baris(_baris, data.discount or 0, is_inc)
    subtotal    = _t["subtotal"]
    disc_amount = _t["discount"]
    tax_amount  = _t["tax"]
    total       = _t["total"]
    # tax_percent header (tampilan/struk): satu nilai bila semua baris bertarif sama, else 0
    # (struk menampilkan "Termasuk PPN" tanpa persen saat tarif campur).
    eff_tax_percent = line_rates[0] if (line_rates and len(set(line_rates)) == 1) else 0
    change      = max(0, data.paid - total)
    status      = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    # ── Resolve Customer 'Umum' if empty ──────────────────────────────────────
    customer_id = data.customer_id
    if not customer_id:
        umum = db.query(models.Customer).filter(func.lower(models.Customer.name) == "umum").first()
        if not umum:
            umum = models.Customer(
                code="CUST-UMUM",
                name="Umum",
                phone="-",
                is_active=True
            )
            db.add(umum)
            db.flush()
        customer_id = umum.id

    # ── Simpan Header Sale ────────────────────────────────────────────────────
    sale = models.Sale(
        number=number,
        date=local_date,
        branch_id=current_user.active_branch_id,
        created_at=local_datetime,
        created_by=current_user.id,
        shift_id=active_shift.id,
        customer_id=customer_id,
        salesperson_id=data.salesperson_id,
        subtotal=subtotal,
        discount=disc_amount,
        tax=tax_amount,
        tax_percent=eff_tax_percent,
        is_tax_included=data.is_tax_included if data.is_tax_included is not None else True,
        total=total,
        paid=data.paid,
        change=change,
        payment_method=data.payment_method,
        status=status,
        notes=data.notes
    )
    db.add(sale)
    db.flush()

    # ── Cek Gudang Default Cabang ─────────────────────────────────────────────
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_default == True
    ).first()

    total_hpp = 0.0

    # ── Loop Item ─────────────────────────────────────────────────────────────
    for idx, it in enumerate(data.items):
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")

        stock_item, item_map = _get_locked_stock_item(db, item)
        required_qty = get_required_stock_qty(item, it.qty)

        if gudang_aktif:
            from .warehouse import get_warehouse_stock, adjust_warehouse_stock
            stok_lokal = get_warehouse_stock(db, gudang_aktif.id, stock_item.id)
            available_qty = get_effective_stock_from_source(item, stok_lokal)
            if stok_lokal < required_qty:
                raise HTTPException(400, f"Stok {item.name} di Gudang Etalase tidak cukup (Sisa: {round(available_qty, 4)})")
            adjust_warehouse_stock(db, gudang_aktif.id, stock_item.id, -required_qty)
        else:
            available_qty = get_effective_stock_from_source(item, stock_item.stock or 0)
            if stock_item.stock < required_qty:
                raise HTTPException(400, f"Stok {item.name} tidak cukup (Sisa: {round(available_qty, 4)})")

        line_total = (it.sell_price * (1 - it.discount / 100)) * it.qty

        # ── HPP via FIFO: konsumsi batch tertua dulu di gudang penjualan ────────
        # required_qty sudah dalam satuan dasar (mengikuti konversi multi-satuan).
        if gudang_aktif:
            allocations = consume_fifo(
                db, item_id=stock_item.id, warehouse_id=gudang_aktif.id, qty=required_qty
            )
            line_hpp = sum(a_qty * a_cost for (_b, a_qty, a_cost) in allocations)
        else:
            # Tanpa gudang (mode lama): jatuh ke harga modal item (perilaku lama).
            allocations = []
            line_hpp = (get_effective_buy_price(db, item, item_map=item_map) or 0) * it.qty

        # buy_price disimpan PER SATUAN JUAL (it.qty), konsisten dgn logika lama
        # (pembalikan/retur & margin memakai SaleItem.buy_price * qty).
        current_buy_price = (line_hpp / it.qty) if it.qty else 0
        total_hpp        += line_hpp

        sale_item = models.SaleItem(
            sale_id=sale.id,
            item_id=it.item_id,
            qty=it.qty,
            buy_price=current_buy_price,
            sell_price=it.sell_price,
            discount=it.discount,
            ppn_percent=line_rates[idx],   # tarif PPN baris ini → dipakai saat retur per-baris
            total=line_total
        )
        db.add(sale_item)
        db.flush()
        record_allocations(db, sale_item_id=sale_item.id, allocations=allocations)

        before = float(stock_item.stock or 0)
        stock_item.stock -= required_qty

        db.add(models.StockMovement(
            date=local_date,
            created_at=local_datetime,
            item_id=stock_item.id,
            branch_id=current_user.active_branch_id,
            type="out",
            qty=required_qty,
            qty_before=before,
            qty_after=stock_item.stock,
            reference=number,
            notes=(
                f"Penjualan Kasir - via {item.name}"
                if stock_item.id != item.id
                else "Penjualan Kasir"
            ),
        ))

    # ── Customer Point ────────────────────────────────────────────────────────
    # ✅ FIX: Dipisah dari blok AUTO JOURNAL agar jurnal tidak bergantung pada customer
    if data.customer_id:
        cust = db.query(models.Customer).with_for_update().get(data.customer_id)
        if cust:
            cust.points += int(total / 1000)
            cust.loyalty_points += int(total / 1000)

    # ── AUTO JOURNAL ──────────────────────────────────────────────────────────
    # ✅ FIX: Di luar blok customer, selalu jalan untuk semua transaksi
    jurnal_entries = []

    # 1. KAS — senilai yang benar-benar dibayar saat ini
    if sale.paid > 0:
        jurnal_entries.append({"code": "1-1100", "debit": float(sale.paid), "credit": 0})

    # 2. PIUTANG — sisa tagihan belum lunas (partial / unpaid)
    piutang = total - sale.paid
    if piutang > 0.01:
        jurnal_entries.append({"code": "1-1300", "debit": round(piutang, 2), "credit": 0})

    # 3. DISKON KE CUSTOMER (Contra Revenue → Debit 4-1150)
    if disc_amount > 0.01:
        jurnal_entries.append({"code": "4-1150", "debit": round(disc_amount, 2), "credit": 0})

    # 4. PPN / TAX (Hutang ke Negara → Credit 2-1200)
    if tax_amount > 0.01:
        jurnal_entries.append({"code": "2-1200", "debit": 0, "credit": round(tax_amount, 2)})

    # 5. PENDAPATAN GROSS (Credit 4-1100 = subtotal SEBELUM diskon)
    #    Diskon terlihat jelas di L/R sebagai pengurang via 4-1150
    jurnal_entries.append({"code": "4-1100", "debit": 0, "credit": round(subtotal, 2)})

    # 6. HPP & PERSEDIAAN KELUAR
    if total_hpp > 0.01:
        jurnal_entries.append({"code": "5-1100", "debit": round(total_hpp, 2), "credit": 0})
        jurnal_entries.append({"code": "1-1400", "debit": 0, "credit": round(total_hpp, 2)})

    # Guard: validasi balance
    total_d = round(sum(e["debit"]  for e in jurnal_entries), 2)
    total_c = round(sum(e["credit"] for e in jurnal_entries), 2)
    if abs(total_d - total_c) > 0.05:
        raise HTTPException(500, f"BUG: Jurnal tidak balance! Debit={total_d} Kredit={total_c}")

    create_auto_journal(
        db=db,
        date_val=local_date,
        number_ref=number,
        description=f"Penjualan Kasir {number}",
        entries=jurnal_entries,
        user_id=current_user.id,
        branch_id=current_user.active_branch_id
    )

    db.commit()
    db.refresh(sale)
    return _sale_out_for_user(sale, current_user)


# ══════════════════════════════════════════════════════════════════════════════
# POST — Batalkan Penjualan
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{sid}/cancel")
def cancel_sale(
    sid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    obj = get_query(db, models.Sale, current_user).filter(
        models.Sale.id == sid
    ).with_for_update().first()

    if not obj:
        raise HTTPException(404, "Penjualan tidak ditemukan")
    if obj.status == "cancelled":
        raise HTTPException(400, "Faktur penjualan ini sudah dibatalkan sebelumnya")

    from .accounting import get_books_locked_until
    locked_until = get_books_locked_until(db, obj.branch_id or current_user.active_branch_id)
    if locked_until and obj.date and obj.date <= locked_until:
        raise HTTPException(
            status_code=400,
            detail=f"DITOLAK: Penjualan tanggal {obj.date} berada di periode tutup buku (sampai {locked_until}). Tidak bisa dibatalkan.",
        )

    local_date     = get_local_date()
    local_datetime = get_local_datetime()

    # ── Gudang Default Cabang Asal ─────────────────────────────────────────
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == obj.branch_id,
        models.Warehouse.is_default == True
    ).first()

    # ── Kembalikan Stok ───────────────────────────────────────────────────────
    for it in obj.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if item:
            stock_item, _ = _get_locked_stock_item(db, item, require_active=False)
            required_qty = get_required_stock_qty(item, it.qty)
            before = float(stock_item.stock or 0)
            stock_item.stock += required_qty

            if gudang_aktif:
                from .warehouse import adjust_warehouse_stock
                adjust_warehouse_stock(db, gudang_aktif.id, stock_item.id, required_qty)

            # ── FIFO: kembalikan sisa ke batch yg dulu dikonsumsi baris jual ini ─
            restore_allocations(db, it.id)

            db.add(models.StockMovement(
                date=local_date,
                created_at=local_datetime,
                item_id=stock_item.id,
                branch_id=obj.branch_id,
                type="in",
                qty=required_qty,
                qty_before=before,
                qty_after=stock_item.stock,
                reference=obj.number,
                notes=(
                    f"Batal Penjualan {obj.number} - restore dari {item.name}"
                    if stock_item.id != item.id
                    else f"Batal Penjualan {obj.number}"
                ),
            ))

    # ── Tarik Kembali Poin Pelanggan ──────────────────────────────────────────
    if obj.customer_id:
        cust = db.query(models.Customer).with_for_update().get(obj.customer_id)
        if cust:
            poin_dibatalkan = int(obj.total / 1000)
            cust.points    -= poin_dibatalkan
            cust.loyalty_points -= poin_dibatalkan
            if cust.points < 0: cust.points = 0
            if cust.loyalty_points < 0: cust.loyalty_points = 0

    # ── Jurnal Pembalik (cermin sempurna dari jurnal asli) ────────────────────
    total_hpp      = sum(it.buy_price * it.qty for it in obj.items)
    jurnal_pembalik = []

    # 1. Balik KAS
    if obj.paid > 0:
        jurnal_pembalik.append({"code": "1-1100", "debit": 0, "credit": float(obj.paid)})

    # 2. Balik PIUTANG
    piutang_sisa = obj.total - obj.paid
    if piutang_sisa > 0.01:
        jurnal_pembalik.append({"code": "1-1300", "debit": 0, "credit": round(piutang_sisa, 2)})

    # 3. Balik DISKON (Credit 4-1150)
    if obj.discount and obj.discount > 0.01:
        jurnal_pembalik.append({"code": "4-1150", "debit": 0, "credit": round(float(obj.discount), 2)})

    # 4. Balik PPN (Debit 2-1200)
    if obj.tax and obj.tax > 0.01:
        jurnal_pembalik.append({"code": "2-1200", "debit": round(float(obj.tax), 2), "credit": 0})

    # 5. Balik PENDAPATAN GROSS (Debit 4-1100 = obj.subtotal)
    jurnal_pembalik.append({"code": "4-1100", "debit": round(float(obj.subtotal), 2), "credit": 0})

    # 6. Balik HPP & PERSEDIAAN MASUK
    if total_hpp > 0.01:
        jurnal_pembalik.append({"code": "5-1100", "debit": 0,                      "credit": round(total_hpp, 2)})
        jurnal_pembalik.append({"code": "1-1400", "debit": round(total_hpp, 2), "credit": 0})

    create_auto_journal(
        db=db,
        date_val=local_date,
        number_ref=obj.number,
        description=f"Batal Penjualan Kasir {obj.number}",
        entries=jurnal_pembalik,
        user_id=current_user.id,
        branch_id=obj.branch_id
    )

    # ── Update Status & Audit ─────────────────────────────────────────────────
    obj.status = "cancelled"
    try:
        write_audit(
            db, current_user.id, "CANCEL", "sales", obj.id,
            f"Membatalkan faktur penjualan {obj.number} (Total: {obj.total})"
        )
    except Exception:
        pass

    db.commit()
    return {
        "message": "Faktur penjualan berhasil dibatalkan. Stok dikembalikan dan poin ditarik.",
        "number": obj.number,
        "status": obj.status
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST — Cetak Struk ke Print Queue
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/print/{sale_id}")
async def print_receipt_api(
    sale_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        try:
            data = await request.json()
        except Exception:
            data = {}

        settings_toko = data.get("settings", {})

        sale = db.query(models.Sale).get(sale_id)
        if not sale:
            return JSONResponse(status_code=404, content={"detail": "Transaksi tidak ditemukan"})

        if not sale.branch_id:
            return JSONResponse(status_code=400, content={"detail": "Branch ID tidak valid"})

        branch_id = sale.branch_id

        # ── Helper Formatter ──────────────────────────────────────────────────
        def format_rp(val):
            try:
                return f"{int(float(val)):,}".replace(",", ".") + ",00"
            except Exception:
                return "0,00"

        def format_qty(val):
            try:
                v = float(val)
                return f"{int(v)},00" if v.is_integer() else f"{v}".replace(".", ",")
            except Exception:
                return "0,00"

        W = 48
        def lr(left, right):
            spaces = W - len(left) - len(right)
            if spaces < 1: spaces = 1
            return f"{left}{' ' * spaces}{right}\n"

        # ── Header & Footer ───────────────────────────────────────────────────
        nama_toko = printer_safe(settings_toko.get("storeName", "TOKO")).upper()
        alamat    = printer_safe(settings_toko.get("storeAddr", ""))
        footer    = printer_safe(settings_toko.get("storeFooter", "Terima Kasih!"))

        try:
            parsed_date = sale.date.strftime("%d-%m-%Y") if hasattr(sale.date, 'strftime') else str(sale.date)
        except Exception:
            parsed_date = datetime.now(WITA).strftime("%d-%m-%Y")

        try:
            time_str = sale.created_at.strftime("%H:%M:%S") if hasattr(sale.created_at, 'strftime') else datetime.now(WITA).strftime("%H:%M:%S")
        except Exception:
            time_str = "-"

        no_str    = str(sale.number)
        kasir     = "ADMIN"
        pelanggan = "UMUM"
        payment   = str(getattr(sale, 'payment_method', 'CASH')).upper()

        if getattr(sale, 'created_by', None):
            user = db.query(models.User).filter(models.User.id == sale.created_by).first()
            if user:
                kasir = printer_safe(user.username).upper()

        if getattr(sale, 'customer_id', None):
            cust = db.query(models.Customer).filter(models.Customer.id == sale.customer_id).first()
            if cust:
                pelanggan = printer_safe(cust.name).upper()

        # ── Rakit Struk ───────────────────────────────────────────────────────
        struk  = "\x1B\x61\x01\x1D\x21\x11"
        struk += f"{nama_toko}\n\n"
        struk += "\x1D\x21\x00\x1B\x61\x00"

        for line in alamat.split('\n'):
            struk += f"{line}\n"
        struk += "\n"

        struk += lr(f"No.  : {no_str}", parsed_date)
        struk += lr(f"Kasir: {kasir}", time_str)
        struk += f"Pel. : {pelanggan}/{payment}\n"

        garis  = "-" * W
        struk += f"{garis}\n"

        brs       = len(sale.items) if getattr(sale, 'items', None) else 0
        total_qty = 0.0

        if brs > 0:
            for item in sale.items:
                nama_barang = "BARANG"
                unit_name   = "PCS"
                if getattr(item, 'item', None):
                    raw_nama    = printer_safe(item.item.name).upper()
                    wrapped     = textwrap.wrap(raw_nama, width=W)
                    nama_barang = "\n".join(wrapped)
                    if getattr(item.item, 'unit', None):
                        unit_name = printer_safe(item.item.unit.name).upper()

                struk += f"{nama_barang}\n"

                qty        = float(item.qty)
                total_qty += qty
                harga_str  = format_rp(item.sell_price)
                qty_str    = format_qty(qty)
                total_str  = format_rp(item.total)
                left_part  = f"{harga_str:<14} x {qty_str:<5} {unit_name:<4} ="
                struk     += lr(left_part, total_str)

        struk += f"{garis}\n"
        struk += lr(f"BRS={brs}  , QTY={format_qty(total_qty)}", format_rp(getattr(sale, 'total', 0)))
        # Mode PKP: harga sudah termasuk PPN → tampilkan porsinya (info; tidak menambah total).
        if float(getattr(sale, 'tax', 0) or 0) > 0:
            _tp = float(getattr(sale, 'tax_percent', 0) or 0)
            _lbl = f"Termasuk PPN({_tp:g}%) =" if _tp else "Termasuk PPN ="
            struk += lr(_lbl, format_rp(sale.tax))
        struk += lr("Tunai    =", format_rp(getattr(sale, 'paid', 0)))
        struk += f"{'-' * 22:>{W}}\n"
        struk += lr("Kembali  =", format_rp(getattr(sale, 'change', 0)))
        struk += "\n\x1B\x61\x01"
        struk += f"{footer}\n\n\n"

        # ── Simpan ke Print Queue ─────────────────────────────────────────────
        db.add(models.PrintJob(
            branch_id=branch_id,
            content=struk,
            content_type="raw",
            status="pending"
        ))
        db.commit()

        return {"status": "success", "message": f"Struk masuk ke antrean cetak Cabang {branch_id}!"}

    except Exception as e:
        print(f"🔥 ERROR PRINT FATAL: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Gagal mencetak: {str(e)}"})
