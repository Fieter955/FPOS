from sqlalchemy.orm import Session

from .. import models


PUSAT_BRANCH_ID = 1
PURCHASE_TAX_TYPES = {"include", "exclude", "none"}


def normalize_purchase_tax_type(value, *, is_tax_included: bool = True) -> str:
    """Return the canonical purchase tax mode.

    ``tax_type`` was added after ``is_tax_included``.  Keeping the fallback here
    preserves the meaning of existing invoices: true means Include and false
    means Exclude.  New requests must use one of include/exclude/none.
    """
    value = str(value or "").strip().lower()
    if value in PURCHASE_TAX_TYPES:
        return value
    return "include" if bool(is_tax_included) else "exclude"


def purchase_tax_type(purchase) -> str:
    """Read a persisted purchase mode, including legacy purchases."""
    return normalize_purchase_tax_type(
        getattr(purchase, "tax_type", None),
        is_tax_included=getattr(purchase, "is_tax_included", True),
    )


def _purchase_branch(db: Session):
    return (
        db.get(models.Branch, PUSAT_BRANCH_ID)
        or db.query(models.Branch).order_by(models.Branch.id).first()
    )


def purchase_line_ppn_rates(db: Session, data) -> list[float]:
    """Resolve the effective PPN rate for each purchase line.

    A rate explicitly entered on the invoice is a transaction snapshot and is
    therefore authoritative.  Missing rates fall back to the selected
    supplier's item setting, then the item master, then the store rate.  A
    stored zero on the item master remains an explicit Non-PPN override.
    """
    branch = _purchase_branch(db)
    store_rate = max(0.0, float(getattr(branch, "tarif_ppn", 0) or 0)) if branch else 0.0
    item_ids = [line.item_id for line in data.items]
    item_rows = db.query(models.Item).filter(models.Item.id.in_(item_ids)).all()
    item_map = {item.id: item for item in item_rows}
    supplier_id = getattr(data, "supplier_id", None)
    spec_map = {}
    if supplier_id:
        specs = db.query(models.ItemSupplier).filter(
            models.ItemSupplier.supplier_id == supplier_id,
            models.ItemSupplier.item_id.in_(item_ids),
        ).all()
        spec_map = {spec.item_id: spec for spec in specs}
    supplier = db.get(models.Supplier, supplier_id) if supplier_id else None
    supplier_rate = max(0.0, float(getattr(supplier, "PpnSupplier", 0) or 0)) if supplier else 0.0

    rates = []
    for line in data.items:
        entered = getattr(line, "ppn_percent", None)
        if entered is not None:
            rate = float(entered)
        else:
            item = item_map.get(line.item_id)
            spec = spec_map.get(line.item_id)
            if spec and (spec.ppn_type or "").lower() == "none":
                rate = 0.0
            elif spec and float(spec.ppn_percent or 0) > 0:
                rate = float(spec.ppn_percent)
            elif item is not None and item.ppn_percent is not None:
                rate = float(item.ppn_percent)
            elif supplier_rate > 0:
                rate = supplier_rate
            else:
                rate = store_rate
        if rate < 0 or rate > 100:
            raise ValueError("Tarif PPN per baris harus berada di antara 0 dan 100 persen")
        rates.append(rate)
    return rates


def _sales_ppn_context(db: Session) -> tuple[bool, float]:
    """Ambil saklar PKP company-wide sebagai sumber keputusan final penjualan.

    Nilai dari browser tidak boleh menghidupkan PPN saat Accounting berstatus non-PKP,
    atau mematikan PPN hanya karena halaman kasir belum dimuat ulang setelah mode PKP aktif.
    """
    branch = (
        db.get(models.Branch, PUSAT_BRANCH_ID)
        or db.query(models.Branch).order_by(models.Branch.id).first()
    )
    is_pkp = bool(getattr(branch, "is_pkp", False)) if branch else False
    tarif = float(getattr(branch, "tarif_ppn", 0) or 0) if is_pkp else 0.0
    return is_pkp, max(0.0, tarif)


def _sale_line_ppn_rates(db: Session, items: list, tarif_toko: float) -> list[float]:
    """Tarif final per baris: mode toko -> master barang -> tarif standar toko.

    Item.ppn_percent=0 adalah override eksplisit barang Non-PPN. NULL berarti mengikuti
    tarif toko. Nilai baris dari browser sengaja tidak dipercaya untuk menjaga konsistensi
    jurnal dengan master Data Barang dan status PKP di Accounting.
    """
    if tarif_toko <= 0:
        return [0.0 for _ in items]

    ids = [it.item_id for it in items]
    rows = db.query(models.Item.id, models.Item.ppn_percent).filter(models.Item.id.in_(ids)).all()
    ppn_map = {item_id: ppn_percent for item_id, ppn_percent in rows}
    rates = []
    for it in items:
        master_rate = ppn_map.get(it.item_id)
        effective_rate = tarif_toko if master_rate is None else float(master_rate)
        rates.append(max(0.0, effective_rate))
    return rates
