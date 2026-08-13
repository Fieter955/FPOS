from sqlalchemy.orm import Session

from .. import models


PUSAT_BRANCH_ID = 1


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
