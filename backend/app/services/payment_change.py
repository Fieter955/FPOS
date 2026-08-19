from typing import Optional


def calculate_change(
    paid: float,
    total: float,
    payments=None,
    cash_received: Optional[float] = None,
    payment_method: str = "cash",
) -> float:
    """Hitung kembalian tanpa mencampur uang bruto dan pembayaran bersih.

    `paid` dan rincian `payments` adalah nilai bersih yang masuk ke tagihan/jurnal.
    `cash_received` adalah uang fisik sebelum kembalian. Pemanggil lama yang tidak
    mengirim `cash_received` tetap memakai perilaku sebelumnya.
    """
    paid = float(paid or 0)
    total = float(total or 0)

    if cash_received is None:
        return round(max(0, paid - total), 2)

    cash_received = float(cash_received)
    if cash_received < 0:
        raise ValueError("Uang tunai yang diterima tidak boleh negatif")

    if payments is not None:
        cash_applied = sum(
            max(0, float(getattr(payment, "jumlah", 0) or 0))
            for payment in payments
            if getattr(payment, "metode", None) == "cash"
        )
    elif payment_method == "cash":
        # Kompatibilitas POS sederhana: ketika tidak ada rincian tender, `paid`
        # adalah bagian tunai bersih yang diterapkan ke tagihan.
        cash_applied = paid
    else:
        cash_applied = 0.0

    if cash_received + 0.01 < cash_applied:
        raise ValueError(
            "Uang tunai yang diterima lebih kecil dari pembayaran tunai bersih"
        )

    return round(max(0, cash_received - cash_applied), 2)
