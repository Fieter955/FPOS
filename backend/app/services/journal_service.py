from datetime import date
from typing import Literal, Optional

from sqlalchemy.orm import Session

from .. import models


ACCOUNT_INVENTORY = "1-1400"
ACCOUNT_CASH = "1-1100"
ACCOUNT_BANK = "1-1200"
ACCOUNT_PAYABLE = "2-1100"
ACCOUNT_RECEIVABLE = "1-1300"
ACCOUNT_SALDO_SUPPLIER = "1-1600"
ACCOUNT_SALDO_CUSTOMER = "2-1300"
ACCOUNT_WRITE_OFF_INCOME = "4-1400"
ACCOUNT_TRANSFER_CLEARING = "3-2000"
ACCOUNT_TRANSFER_IN = "3-2100"
ACCOUNT_TRANSFER_OUT = "3-2200"
ACCOUNT_SALES = "4-1100"
ACCOUNT_SALES_RETURN = "4-1200"
ACCOUNT_COGS = "5-1100"
ACCOUNT_TAX_EXPENSE = "5-2000" # Beban Pajak
ACCOUNT_PPN_MASUKAN = "1-1550"  # PPN Masukan (Pajak Dibayar Dimuka) — dipisah dari modal saat toko PKP
ACCOUNT_PPN_KELUARAN = "2-1200"  # Hutang PPN (Keluaran) — PPN yang dipungut ke pelanggan saat jual
ACCOUNT_PURCHASE_DISCOUNT = "4-2000"   # Diskon Pembelian — untung retur (refund > modal FIFO)
ACCOUNT_INVENTORY_VARIANCE = "5-1200"  # Beban Susut & Selisih Persediaan — rugi retur (refund < modal)


def _auto_journal(db: Session, date_val: date, number_ref: str, description: str,
                  entries: list[dict], user_id: int, branch_id: int):
    from ..routes.accounting import create_auto_journal

    return create_auto_journal(
        db=db,
        date_val=date_val,
        number_ref=number_ref,
        description=description,
        entries=entries,
        user_id=user_id,
        branch_id=branch_id
    )


def create_customer_balance_transfer_journal(db: Session, *, date_val: date, amount: float,
                                           source_name: str, target_name: str,
                                           user_id: int, branch_id: int):
    """Jurnal transfer saldo deposit antar pelanggan (Mutasi di akun Hutang/Titipan Pelanggan)"""
    entries = [
        {"code": ACCOUNT_SALDO_CUSTOMER, "debit": amount, "credit": 0},  # Source Customer (Liability decreases)
        {"code": ACCOUNT_SALDO_CUSTOMER, "debit": 0, "credit": amount}, # Target Customer (Liability increases)
    ]
    return _auto_journal(
        db, date_val, f"TRF-{date_val.strftime('%Y%m%d')}",
        f"Transfer Saldo: {source_name} ke {target_name}",
        entries, user_id, branch_id
    )


def create_customer_balance_write_off_journal(db: Session, *, date_val: date, amount: float,
                                            customer_name: str, user_id: int, branch_id: int):
    """Jurnal penghapusan saldo deposit pelanggan (Liability ke Revenue)"""
    entries = [
        {"code": ACCOUNT_SALDO_CUSTOMER, "debit": amount, "credit": 0},    # Liability decreases
        {"code": ACCOUNT_WRITE_OFF_INCOME, "debit": 0, "credit": amount}, # Other Income increases
    ]
    return _auto_journal(
        db, date_val, f"WO-{date_val.strftime('%Y%m%d')}",
        f"Penghapusan Saldo Pelanggan: {customer_name}",
        entries, user_id, branch_id
    )


def create_purchase_journal(db: Session, *, date_val: date, number_ref: str,
                            supplier_name: str, total: float, paid: float,
                            user_id: int, branch_id: int,
                            tax: float = 0, pisah_ppn: bool = False):
    # Pembelian bernilai 0 tidak menghasilkan debit/kredit apa pun -> jangan buat
    # jurnal "hantu" tanpa baris (lihat detail jurnal yang kosong di Akuntansi).
    if total <= 0:
        return None
    tax = float(tax or 0)
    if pisah_ppn and tax > 0:
        # Toko PKP: PPN ("PPN Masukan") dipisah jadi aset yang bisa dikreditkan ke negara,
        # sehingga modal/persediaan = nilai net (tanpa PPN). Lihat CLAUDE.md & rencana PKP.
        net = total - tax
        entries = [
            {"code": ACCOUNT_INVENTORY, "debit": net, "credit": 0},
            {"code": ACCOUNT_PPN_MASUKAN, "debit": tax, "credit": 0},
        ]
    else:
        # Non-PKP (default): PPN melebur jadi bagian modal/persediaan (1-1400).
        entries = [{"code": ACCOUNT_INVENTORY, "debit": total, "credit": 0}]
    if paid > 0:
        entries.append({"code": ACCOUNT_CASH, "debit": 0, "credit": paid})
    payable = total - paid
    if payable > 0:
        entries.append({"code": ACCOUNT_PAYABLE, "debit": 0, "credit": payable})

    return _auto_journal(
        db,
        date_val,
        number_ref,
        f"Pembelian {number_ref} - {supplier_name}",
        entries,
        user_id,
        branch_id,
    )


def create_purchase_reversal_journal(db: Session, *, date_val: date, number_ref: str,
                                     total: float, paid: float, user_id: int,
                                     branch_id: int,
                                     tax: float = 0, pisah_ppn: bool = False):
    tax = float(tax or 0)
    if pisah_ppn and tax > 0:
        # Kebalikan pembelian PKP: PPN Masukan (1-1550) ikut dibalik, persediaan dibalik NET.
        net = total - tax
        entries = [
            {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": net},
            {"code": ACCOUNT_PPN_MASUKAN, "debit": 0, "credit": tax},
        ]
    else:
        entries = [{"code": ACCOUNT_INVENTORY, "debit": 0, "credit": total}]
    if paid > 0:
        entries.append({"code": ACCOUNT_CASH, "debit": paid, "credit": 0})
    payable = total - paid
    if payable > 0:
        entries.append({"code": ACCOUNT_PAYABLE, "debit": payable, "credit": 0})

    return _auto_journal(
        db,
        date_val,
        number_ref,
        f"PEMBATALAN PEMBELIAN: {number_ref}",
        entries,
        user_id,
        branch_id,
    )


def create_purchase_payment_journal(db: Session, *, date_val: date, number_ref: str,
                                    description: str, cash_amount: float,
                                    bank_amount: float, user_id: int,
                                    branch_id: int):
    entries = [{"code": ACCOUNT_PAYABLE, "debit": cash_amount + bank_amount, "credit": 0}]
    if cash_amount > 0:
        entries.append({"code": ACCOUNT_CASH, "debit": 0, "credit": cash_amount})
    if bank_amount > 0:
        entries.append({"code": "1-1200", "debit": 0, "credit": bank_amount})

    return _auto_journal(
        db,
        date_val,
        number_ref,
        description,
        entries,
        user_id,
        branch_id,
    )


def create_transfer_journal(db: Session, *, date_val: date, number_ref: str,
                            total: float, user_id: int, branch_id: int,
                            direction: Literal["out", "in"], description: str):
    if direction == "out":
        entries = [
            {"code": ACCOUNT_TRANSFER_CLEARING, "debit": total, "credit": 0},
            {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": total},
        ]
    else:
        entries = [
            {"code": ACCOUNT_INVENTORY, "debit": total, "credit": 0},
            {"code": ACCOUNT_TRANSFER_CLEARING, "debit": 0, "credit": total},
        ]

    return _auto_journal(db, date_val, number_ref, description, entries, user_id, branch_id)


def create_pusat_fulfillment_journal(db: Session, *, date_val: date, number_ref: str,
                                     supplier_name: str, target_branch_id: int,
                                     total: float, paid: float, user_id: int,
                                     pusat_branch_id: int = 1):
    # Jurnal Toko Pusat (Mengeluarkan Kas/Hutang, Mencatat Transfer Keluar)
    entries_pusat = [{"code": ACCOUNT_TRANSFER_OUT, "debit": total, "credit": 0}]
    if paid > 0:
        entries_pusat.append({"code": ACCOUNT_CASH, "debit": 0, "credit": paid})
    payable = total - paid
    if payable > 0:
        entries_pusat.append({"code": ACCOUNT_PAYABLE, "debit": 0, "credit": payable})

    return _auto_journal(
        db,
        date_val,
        number_ref,
        f"Pusat beli untuk Cabang ({target_branch_id}) - {supplier_name}",
        entries_pusat,
        user_id,
        pusat_branch_id,
    )


def create_branch_receiving_journal(db: Session, *, date_val: date, number_ref: str,
                                    total: float, user_id: int,
                                    target_branch_id: int):
    # Jurnal Cabang Penerima (Menerima Stok, Mencatat Hutang Antar Kantor)
    entries_cabang = [
        {"code": ACCOUNT_INVENTORY, "debit": total, "credit": 0},
        {"code": ACCOUNT_TRANSFER_IN, "debit": 0, "credit": total},
    ]

    return _auto_journal(
        db,
        date_val,
        number_ref,
        f"Terima stok dari Pusat (PO: {number_ref})",
        entries_cabang,
        user_id,
        target_branch_id,
    )


def create_branch_fulfillment_journal(db: Session, *, date_val: date, number_ref: str,
                                      supplier_name: str, target_branch_id: int,
                                      total: float, paid: float, user_id: int,
                                      pusat_branch_id: int = 1):
    # Backward compatibility or combined call if needed (deprecated in new flow)
    create_pusat_fulfillment_journal(
        db, date_val=date_val, number_ref=number_ref, supplier_name=supplier_name,
        target_branch_id=target_branch_id, total=total, paid=paid, user_id=user_id,
        pusat_branch_id=pusat_branch_id
    )
    return create_branch_receiving_journal(
        db, date_val=date_val, number_ref=number_ref, total=total,
        user_id=user_id, target_branch_id=target_branch_id
    )


def create_branch_fulfillment_reversal_journal(db: Session, *, date_val: date,
                                               number_ref: str, target_branch_id: int,
                                               total: float, paid: float,
                                               user_id: int, pusat_branch_id: int = 1):
    # 1. Balik Jurnal Toko Pusat
    entries_pusat = [{"code": ACCOUNT_TRANSFER_OUT, "debit": 0, "credit": total}]
    if paid > 0:
        entries_pusat.append({"code": ACCOUNT_CASH, "debit": paid, "credit": 0})
    payable = total - paid
    if payable > 0:
        entries_pusat.append({"code": ACCOUNT_PAYABLE, "debit": payable, "credit": 0})

    _auto_journal(
        db,
        date_val,
        number_ref,
        f"PEMBATALAN FULFILLMENT CABANG ({target_branch_id}): {number_ref}",
        entries_pusat,
        user_id,
        pusat_branch_id,
    )

    # 2. Balik Jurnal Cabang Penerima
    entries_cabang = [
        {"code": ACCOUNT_TRANSFER_IN, "debit": total, "credit": 0},
        {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": total},
    ]

    return _auto_journal(
        db,
        date_val,
        number_ref,
        f"PEMBATALAN TERIMA STOK: {number_ref}",
        entries_cabang,
        user_id,
        target_branch_id,
    )


def create_purchase_return_journal(db: Session, *, date_val: date, number_ref: str,
                                   supplier_name: str, total_inventory: float,
                                   total_tax: float, is_tax_included: bool,
                                   user_id: int, branch_id: int,
                                   total_carrying: Optional[float] = None,
                                   ppn_dipisah: bool = False):
    """Jurnal retur pembelian dengan SELISIH HARGA.

    Supplier (1-1600) ditagih sebesar refund disepakati (`total_inventory + PPN`); Persediaan
    (1-1400) dikurangi sebesar BIAYA NYATA barang yang keluar; selisihnya = untung/rugi retur.

    Dua mode (lihat Fase 4):
    - **Mode FIFO/gudang** (`total_carrying` diisi): biaya batch SUDAH landed (termasuk pajak &
      diskon yang dikapitalisasi saat beli). Barang keluar di biaya landed; pajak TIDAK dibalik
      terpisah. Selisih = refund(incl pajak) − biaya landed → untung 4-2000 / rugi 5-1200.
    - **Mode tanpa gudang** (`total_carrying` None): tak ada lapisan FIFO → persediaan keluar di
      nilai barang (ex-pajak) dan pajak dibalik ke Beban Pajak 5-2000 (perilaku lama).

    Bila akun selisih (4-2000/5-1200) tak ada di COA klien → selisih diserap ke Persediaan
    (fallback 2 kaki) agar jurnal tetap balance (FPOS dijual ke banyak klien dgn COA berbeda)."""
    total_to_supplier = total_inventory + total_tax  # refund disepakati ke supplier

    # ── Mode tanpa gudang (tak ada FIFO): perilaku lama, pajak terpisah ke 5-2000 ──
    if total_carrying is None:
        entries = [
            {"code": ACCOUNT_SALDO_SUPPLIER, "debit": total_to_supplier, "credit": 0},
            {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": total_inventory},
        ]
        if total_tax > 0:
            if ppn_dipisah:
                # PKP: balikkan PPN Masukan (1-1550) yang dipisah saat beli.
                entries.append({"code": ACCOUNT_PPN_MASUKAN, "debit": 0, "credit": total_tax})
            elif not is_tax_included:
                entries.append({"code": ACCOUNT_TAX_EXPENSE, "debit": 0, "credit": total_tax})
        return _auto_journal(
            db, date_val, number_ref,
            f"Retur Pembelian {number_ref} - {supplier_name}",
            entries, user_id, branch_id
        )

    # ── Mode FIFO: biaya batch sudah landed (incl pajak) → tanpa kaki pajak terpisah ──
    carrying = float(total_carrying)

    # ── Mode PKP (faktur ini dulu memisah PPN): balikkan PPN Masukan (1-1550) terpisah; selisih
    #    harga dihitung pada NILAI NET barang (total_inventory) vs biaya batch nyata (carrying). ──
    if ppn_dipisah and total_tax > 0.005:
        variance = total_inventory - carrying
        if variance > 0.005:
            if not db.query(models.Account).filter(models.Account.code == ACCOUNT_PURCHASE_DISCOUNT).first():
                carrying, variance = total_inventory, 0.0  # akun selisih tak ada → serap ke Persediaan
        elif variance < -0.005:
            if not db.query(models.Account).filter(models.Account.code == ACCOUNT_INVENTORY_VARIANCE).first():
                carrying, variance = total_inventory, 0.0
        entries = [
            {"code": ACCOUNT_SALDO_SUPPLIER, "debit": total_to_supplier, "credit": 0},
            {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": carrying},
            {"code": ACCOUNT_PPN_MASUKAN, "debit": 0, "credit": total_tax},
        ]
        if variance > 0.005:
            entries.append({"code": ACCOUNT_PURCHASE_DISCOUNT, "debit": 0, "credit": variance})
        elif variance < -0.005:
            entries.append({"code": ACCOUNT_INVENTORY_VARIANCE, "debit": -variance, "credit": 0})
        return _auto_journal(
            db, date_val, number_ref,
            f"Retur Pembelian {number_ref} - {supplier_name}",
            entries, user_id, branch_id
        )

    variance = total_to_supplier - carrying  # refund(incl pajak) − biaya landed nyata

    # Hanya akui selisih bila akunnya tersedia; jika tidak, serap ke Persediaan (2 kaki).
    if variance > 0.005:
        if not db.query(models.Account).filter(models.Account.code == ACCOUNT_PURCHASE_DISCOUNT).first():
            carrying, variance = total_to_supplier, 0.0
    elif variance < -0.005:
        if not db.query(models.Account).filter(models.Account.code == ACCOUNT_INVENTORY_VARIANCE).first():
            carrying, variance = total_to_supplier, 0.0

    entries = [
        {"code": ACCOUNT_SALDO_SUPPLIER, "debit": total_to_supplier, "credit": 0},
        {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": carrying},
    ]
    if variance > 0.005:
        # Refund > biaya landed → untung retur
        entries.append({"code": ACCOUNT_PURCHASE_DISCOUNT, "debit": 0, "credit": variance})
    elif variance < -0.005:
        # Refund < biaya landed → rugi/selisih persediaan
        entries.append({"code": ACCOUNT_INVENTORY_VARIANCE, "debit": -variance, "credit": 0})

    return _auto_journal(
        db, date_val, number_ref,
        f"Retur Pembelian {number_ref} - {supplier_name}",
        entries, user_id, branch_id
    )


def create_sale_return_journal(db: Session, *, date_val: date, number_ref: str,
                               customer_name: str, total_sales: float,
                               total_tax: float, total_cogs: float,
                               is_tax_included: bool, user_id: int, branch_id: int):
    # Total yang menjadi hutang ke customer (Saldo di Customer)
    total_to_customer = total_sales + total_tax
    
    entries = [
        {"code": ACCOUNT_SALDO_CUSTOMER, "debit": 0, "credit": total_to_customer},
        {"code": ACCOUNT_SALES_RETURN, "debit": total_sales, "credit": 0},
        # Kembalikan stok
        {"code": ACCOUNT_INVENTORY, "debit": total_cogs, "credit": 0},
        {"code": ACCOUNT_COGS, "debit": 0, "credit": total_cogs}
    ]
    
    if total_tax > 0:
        # PPN Keluaran yang dipungut saat jual ikut dibalik → kurangi Hutang PPN (2-1200),
        # KONSISTEN dgn jurnal penjualan yang meng-kredit 2-1200. Berlaku untuk penjualan
        # INKLUSIF (PKP: PPN dikupas mundur dari harga jual oleh pemanggil) MAUPUN eksklusif
        # (PPN ditambah di atas). `is_tax_included` tak lagi menentukan — yang penting ada PPN.
        entries.append({"code": ACCOUNT_PPN_KELUARAN, "debit": total_tax, "credit": 0})

    return _auto_journal(
        db, date_val, number_ref,
        f"Retur Penjualan {number_ref} - {customer_name}",
        entries, user_id, branch_id
    )

