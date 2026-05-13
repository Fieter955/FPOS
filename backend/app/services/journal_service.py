from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from .. import models


ACCOUNT_INVENTORY = "1-1400"
ACCOUNT_CASH = "1-1100"
ACCOUNT_PAYABLE = "2-1100"
ACCOUNT_TRANSFER_CLEARING = "3-2000"
ACCOUNT_TRANSFER_IN = "3-2100"
ACCOUNT_TRANSFER_OUT = "3-2200"


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
        branch_id=branch_id,
    )


def create_purchase_journal(db: Session, *, date_val: date, number_ref: str,
                            supplier_name: str, total: float, paid: float,
                            user_id: int, branch_id: int):
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
                                     branch_id: int):
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


def create_branch_fulfillment_journal(db: Session, *, date_val: date, number_ref: str,
                                      supplier_name: str, target_branch_id: int,
                                      total: float, paid: float, user_id: int,
                                      pusat_branch_id: int = 1):
    # 1. Jurnal Toko Pusat (Mengeluarkan Kas/Hutang, Mencatat Transfer Keluar)
    entries_pusat = [{"code": ACCOUNT_TRANSFER_OUT, "debit": total, "credit": 0}]
    if paid > 0:
        entries_pusat.append({"code": ACCOUNT_CASH, "debit": 0, "credit": paid})
    payable = total - paid
    if payable > 0:
        entries_pusat.append({"code": ACCOUNT_PAYABLE, "debit": 0, "credit": payable})

    _auto_journal(
        db,
        date_val,
        number_ref,
        f"Pusat beli untuk Cabang ({target_branch_id}) - {supplier_name}",
        entries_pusat,
        user_id,
        pusat_branch_id,
    )

    # 2. Jurnal Cabang Penerima (Menerima Stok, Mencatat Hutang Antar Kantor)
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

