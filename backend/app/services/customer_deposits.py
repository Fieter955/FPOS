from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .. import models


def apply_customer_deposit_change(
    db: Session,
    customer: models.Customer,
    amount: float,
    *,
    event_type: str,
    transaction_date: date,
    branch_id: Optional[int] = None,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    reference: Optional[str] = None,
    description: Optional[str] = None,
    created_by: Optional[int] = None,
    occurred_at: Optional[datetime] = None,
) -> models.CustomerDepositMovement:
    """Ubah saldo dan catat ledger di transaksi database yang sama."""
    amount = round(float(amount or 0), 2)
    customer.deposit_balance = round(float(customer.deposit_balance or 0) + amount, 2)
    movement = models.CustomerDepositMovement(
        customer_id=customer.id,
        branch_id=branch_id,
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        reference=reference,
        transaction_date=transaction_date,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        amount=amount,
        balance_after=customer.deposit_balance,
        description=description,
        created_by=created_by,
    )
    db.add(movement)
    return movement


def _trade_delta(trade) -> float:
    paid = float(trade.cash_amount or 0) + float(trade.bank_amount or 0) + float(trade.deposit_amount or 0)
    surplus = float(trade.return_subtotal or 0) + paid - float(trade.new_subtotal or 0)
    return round(-float(trade.deposit_amount or 0) + max(surplus, 0), 2)


def backfill_customer_deposit_movements(db: Session) -> int:
    """Backfill satu kali; residu data lama dijelaskan sebagai saldo awal/penyesuaian."""
    if db.query(models.CustomerDepositMovement.id).first():
        return 0

    pending = defaultdict(list)
    customers = {customer.id: customer for customer in db.query(models.Customer).all()}

    for sale in db.query(models.Sale).filter(models.Sale.customer_id.isnot(None)).all():
        deposit = sum(float(payment.amount or 0) for payment in sale.payments if payment.method == "deposit")
        if not sale.payments and sale.payment_method == "deposit":
            deposit = float(sale.paid or 0)
        if deposit > 0.005:
            pending[sale.customer_id].append(dict(
                branch_id=sale.branch_id, event_type="purchase", source_type="sale", source_id=sale.id,
                reference=sale.number, transaction_date=sale.date,
                occurred_at=sale.created_at or datetime.combine(sale.date, datetime.min.time()),
                amount=-deposit, description="Pemakaian deposit untuk pembelian",
            ))

    for return_doc in db.query(models.SaleReturn).join(models.Sale).filter(models.Sale.customer_id.isnot(None)).all():
        sale = return_doc.sale
        pending[sale.customer_id].append(dict(
            branch_id=return_doc.branch_id or sale.branch_id, event_type="sale_return",
            source_type="sale_return", source_id=return_doc.id, reference=return_doc.number,
            transaction_date=return_doc.date,
            occurred_at=return_doc.created_at or datetime.combine(return_doc.date, datetime.min.time()),
            amount=float(return_doc.total or 0), description="Retur penjualan masuk ke deposit",
        ))

    for trade in db.query(models.TradeIn).filter(models.TradeIn.customer_id.isnot(None)).all():
        delta = _trade_delta(trade)
        if abs(delta) > 0.005:
            pending[trade.customer_id].append(dict(
                branch_id=trade.branch_id, event_type="trade_in", source_type="trade_in",
                source_id=trade.id, reference=trade.number, transaction_date=trade.date,
                occurred_at=trade.created_at or datetime.combine(trade.date, datetime.min.time()),
                amount=delta, description="Dampak saldo tukar tambah",
            ))

    inserted = 0
    epoch = datetime(1970, 1, 1)
    for customer_id, customer in customers.items():
        entries = sorted(
            pending.get(customer_id, []),
            key=lambda row: (str(row["occurred_at"]), row["source_id"]),
        )
        known_total = round(sum(float(row["amount"]) for row in entries), 2)
        adjustment = round(float(customer.deposit_balance or 0) - known_total, 2)
        running = 0.0
        if abs(adjustment) > 0.005:
            db.add(models.CustomerDepositMovement(
                customer_id=customer_id, branch_id=None, event_type="opening_adjustment",
                source_type="opening_adjustment", source_id=None, reference="SALDO-AWAL",
                transaction_date=date(1970, 1, 1), occurred_at=epoch, amount=adjustment,
                balance_after=adjustment, description="Saldo awal/penyesuaian data sebelum ledger",
            ))
            running = adjustment
            inserted += 1
        for source in entries:
            row_amount = round(float(source["amount"]), 2)
            running = round(running + row_amount, 2)
            values = dict(source)
            values["amount"] = row_amount
            values["balance_after"] = running
            values["customer_id"] = customer_id
            db.add(models.CustomerDepositMovement(**values))
            inserted += 1

    db.commit()
    return inserted
