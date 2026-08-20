# ══════════════════════════════════════════════════════════════════════════════
# iPos 5.0 — Modul Penjualan (Sales)
# Versi: Fixed — Journal Diskon, Piutang, & Duplikasi Kode
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, date
import pytz

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, get_query, write_audit
from ..permissions import has_permission
from ..services.virtual_units import (
    get_effective_buy_price,
    get_effective_stock_from_source,
    get_required_stock_qty,
    is_virtual_variant,
)
from ..services.inventory_fifo import consume_fifo, record_allocations, restore_allocations
from ..services.shift_service import require_single_open_branch_shift
from ..services.tax_context import _sale_line_ppn_rates, _sales_ppn_context
from ..services.payment_change import calculate_change
from ..services.receipt_renderer import build_sale_receipt, settings_from_branch
from .print_queue import enqueue_print_job
from .accounting import create_auto_journal, pastikan_akun_ada  # ✅ Import di atas, sekali saja

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


def hitung_total_penjualan_per_baris(baris: list, disc_nominal: float, termasuk_ppn: bool) -> dict:
    """Versi PER-BARIS dari hitung_total_penjualan: tiap baris boleh punya tarif PPN sendiri
    (mis. barang kena PPN 11% dicampur barang non-PPN 0%). `baris` = list of {"gross","tarif"} di
    mana gross = sell_price*(1-disc/100)*qty. Diskon header (disc_nominal) disebar proporsional.
    Hasil identik dengan hitung_total_penjualan bila semua baris bertarif sama (nol regresi)."""
    total_gross = sum(float(b.get("gross") or 0) for b in baris)
    disc_ratio = (disc_nominal / total_gross) if total_gross > 0 else 0
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


# Mengambil riwayat harga jual sebuah barang dari transaksi penjualan nyata.
# Dipakai halaman detail_item.html (tab "Histori Harga Jual"). Bentuk hasil
# sengaja disamakan dengan /purchases/item-history/ agar tabelnya seragam.
@router.get("/item-history/")
def get_item_sale_history(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from sqlalchemy.orm import joinedload

    rows = (
        db.query(models.SaleItem)
        .join(models.Sale, models.SaleItem.sale_id == models.Sale.id)
        .options(
            joinedload(models.SaleItem.item).joinedload(models.Item.unit),
            joinedload(models.SaleItem.sale).joinedload(models.Sale.customer),
        )
        .filter(models.SaleItem.item_id == item_id)
        .order_by(models.Sale.date.desc(), models.SaleItem.id.desc())
        .limit(20)
        .all()
    )

    hasil = []
    for si in rows:
        harga = si.sell_price or 0
        potongan = si.discount or 0  # persen per baris
        harga_setelah_potongan = harga * (1 - potongan / 100)
        hasil.append({
            "tanggal": si.sale.date.isoformat() if si.sale and si.sale.date else None,
            "pelanggan": si.sale.customer.name if si.sale and si.sale.customer else "Umum",
            "jumlah": si.qty,
            "satuan": si.item.unit.name if si.item and si.item.unit else "pcs",
            "harga": harga,
            "potongan": potongan,
            "harga_setelah_potongan": round(harga_setelah_potongan, 2),
        })

    return hasil


def _sale_out_for_user(sale: models.Sale, current_user: models.User, db: Session):
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

    if not has_permission(db, current_user, "sales.cost_price", "view"):
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
    return [_sale_out_for_user(sale, current_user, db) for sale in sales]


@router.get("/{sid}", response_model=schemas.SaleOut)
def get_sale(
    sid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    obj = get_query(db, models.Sale, current_user).filter(models.Sale.id == sid).first()
    if not obj:
        raise HTTPException(404, "Penjualan tidak ditemukan")
    return _sale_out_for_user(obj, current_user, db)


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
    active_shift = require_single_open_branch_shift(
        db,
        current_user,
        for_update=True,
    )

    # ── Kalkulasi Header ──────────────────────────────────────────────────────
    number      = data.number or _next_number(db, current_user)
    # Accounting adalah saklar global yang otoritatif. Data Barang hanya menentukan
    # pengecualian/tarif per item saat mode PKP aktif. Harga jual selalu mengikuti
    # konvensi Accounting: sudah termasuk PPN.
    _, tarif_toko = _sales_ppn_context(db)
    is_inc = True
    line_rates = _sale_line_ppn_rates(db, data.items, tarif_toko)

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
    # ── Biaya Lain ditagihkan ke pelanggan (ongkir/admin) → nambah total & jadi Pendapatan Lain-lain
    other_cost  = round(float(data.other_cost or 0), 2)
    if other_cost < 0:
        raise HTTPException(400, "Biaya lain tidak boleh negatif")
    total      += other_cost
    # Pastikan akun Pendapatan Biaya Lain ada SEBELUM stok berubah (mandat jurnal atomic)
    if other_cost > 0.01:
        pastikan_akun_ada(db, ["4-1500"])
    try:
        change = calculate_change(
            paid=data.paid,
            total=total,
            payments=data.payments,
            cash_received=data.cash_received,
            payment_method=data.payment_method,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
        is_tax_included=is_inc,
        other_cost=other_cost,
        total=total,
        paid=data.paid,
        change=change,
        cash_received=data.cash_received,
        invoice_discount_gross=data.discount or 0,
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
    cust = None
    if data.customer_id:
        cust = db.query(models.Customer).with_for_update().get(data.customer_id)
        if cust:
            cust.points += int(total / 1000)
            cust.loyalty_points += int(total / 1000)

    # ── AUTO JOURNAL ──────────────────────────────────────────────────────────
    # ✅ FIX: Di luar blok customer, selalu jalan untuk semua transaksi
    jurnal_entries = []

    # Pastikan akun yang dipakai jurnal pembayaran benar-benar ada SEBELUM mutasi apa pun
    # (mandat atomic-journal: stok/deposit tak boleh berpindah tanpa jurnal pendamping).
    pastikan_akun_ada(db, ["1-1100", "1-1200", "1-1250", "2-1300", "1-1300"])

    # 1. TENDER BAYAR — rute tiap metode ke akunnya sendiri
    #    cash→Kas, debit/credit_card→Bank, emoney→E-Money, deposit→potong Saldo Pelanggan (2-1300)
    METODE_KE_AKUN = {"cash": "1-1100", "debit": "1-1200", "credit_card": "1-1200", "emoney": "1-1250"}
    if data.payments:
        total_tender = 0.0
        for p in data.payments:
            jml = float(p.jumlah or 0)
            if jml <= 0:
                continue
            total_tender += jml
            db.add(models.SalePayment(sale=sale, method=p.metode, amount=round(jml, 2)))
            if p.metode == "deposit":
                if not cust:
                    raise HTTPException(400, "Bayar pakai Saldo Pelanggan butuh pelanggan dipilih")
                if jml > (cust.deposit_balance or 0) + 0.01:
                    raise HTTPException(400, "Saldo pelanggan tidak cukup")
                cust.deposit_balance -= jml
                jurnal_entries.append({"code": "2-1300", "debit": round(jml, 2), "credit": 0})
            else:
                kode = METODE_KE_AKUN.get(p.metode)
                if not kode:
                    raise HTTPException(400, f"Metode bayar tidak dikenal: {p.metode}")
                jurnal_entries.append({"code": kode, "debit": round(jml, 2), "credit": 0})
        # Jaga invarian: Σ tender = paid (cegah jurnal imbalance diam-diam)
        if abs(total_tender - sale.paid) > 0.05:
            raise HTTPException(400, f"Rincian pembayaran ({total_tender:.2f}) tidak sama dengan total dibayar ({sale.paid:.2f})")
    # Fallback (pemanggil lama tanpa rincian `payments`): logika lama deposit-vs-Kas
    elif sale.paid > 0:
        db.add(models.SalePayment(
            sale=sale,
            method=data.payment_method or "cash",
            amount=round(float(sale.paid or 0), 2),
        ))
        if data.payment_method == "deposit" and cust:
            deduct = min(sale.paid, cust.deposit_balance or 0)
            cust.deposit_balance -= deduct
            if deduct > 0:
                jurnal_entries.append({"code": "2-1300", "debit": float(deduct), "credit": 0})
            
            sisa_cash = sale.paid - deduct
            if sisa_cash > 0:
                jurnal_entries.append({"code": "1-1100", "debit": float(sisa_cash), "credit": 0})
        else:
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

    # 7. BIAYA LAIN ditagihkan ke pelanggan (Pendapatan Lain-lain → Credit 4-1500)
    #    Kas/Piutang di atas sudah termasuk biaya lain (total naik) → kaki ini menyeimbangkan.
    if other_cost > 0.01:
        jurnal_entries.append({"code": "4-1500", "debit": 0, "credit": round(other_cost, 2)})

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

    receipt_job = None
    branch = db.query(models.Branch).filter(models.Branch.id == sale.branch_id).first()
    if branch and (bool(data.receipt_requested) or bool(branch.receipt_auto_print)):
        db.flush()
        receipt_job = enqueue_print_job(
            db,
            branch_id=sale.branch_id,
            content=build_sale_receipt(sale, settings_from_branch(branch), current_user),
            document_type="sale",
            document_id=sale.id,
            created_by=current_user.id,
        )

    db.commit()
    db.refresh(sale)
    result = _sale_out_for_user(sale, current_user, db)
    result["receipt_job"] = (
        {"status": "queued", "job_id": receipt_job.id, "branch_id": sale.branch_id}
        if receipt_job else None
    )
    return result


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

    # 7. Balik BIAYA LAIN (Debit 4-1500) — kas/piutang yg dibalik di atas sudah termasuk biaya lain
    _other_cost = float(getattr(obj, "other_cost", 0) or 0)
    if _other_cost > 0.01:
        pastikan_akun_ada(db, ["4-1500"])
        jurnal_pembalik.append({"code": "4-1500", "debit": round(_other_cost, 2), "credit": 0})

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
def print_receipt_api(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    sale = get_query(db, models.Sale, current_user).filter(models.Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    branch = db.query(models.Branch).filter(models.Branch.id == sale.branch_id).first()
    if not branch:
        raise HTTPException(400, "Cabang transaksi tidak valid")
    creator = db.query(models.User).filter(models.User.id == sale.created_by).first() if sale.created_by else None
    job = enqueue_print_job(
        db,
        branch_id=sale.branch_id,
        content=build_sale_receipt(sale, settings_from_branch(branch), creator),
        document_type="sale_reprint",
        document_id=sale.id,
        created_by=current_user.id,
    )
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": job.branch_id}
