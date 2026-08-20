"""Renderer tunggal struk ESC/POS.

Konten dirender saat job dibuat agar cetak ulang, retry, dan perubahan data
setelahnya tidak mengubah dokumen yang sudah masuk antrean.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import textwrap
import unicodedata


PAYMENT_LABELS = {
    "cash": "Tunai",
    "deposit": "Saldo Pelanggan",
    "debit": "Kartu Debit",
    "card": "Kartu",
    "credit_card": "Kartu Kredit",
    "emoney": "E-Money/Transfer",
    "transfer": "Transfer",
    "credit": "Kredit/Piutang",
}


@dataclass(frozen=True)
class ReceiptSettings:
    store_name: str
    address: str = ""
    phone: str = ""
    footer: str = "Terima kasih telah berbelanja!"
    paper_width_mm: int = 80
    auto_print: bool = False

    @property
    def columns(self) -> int:
        return 32 if self.paper_width_mm == 58 else 48


def settings_from_branch(branch) -> ReceiptSettings:
    width = int(getattr(branch, "receipt_paper_width_mm", 80) or 80)
    if width not in {58, 80}:
        width = 80
    return ReceiptSettings(
        store_name=(getattr(branch, "receipt_name", None) or getattr(branch, "name", None) or "TOKO"),
        address=getattr(branch, "address", None) or "",
        phone=getattr(branch, "phone", None) or "",
        footer=getattr(branch, "receipt_footer", None) or "Terima kasih telah berbelanja!",
        paper_width_mm=width,
        auto_print=bool(getattr(branch, "receipt_auto_print", False)),
    )


def safe_text(value) -> str:
    """ASCII printer-safe tanpa karakter kontrol/ESC injection."""
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).replace("\xa0", " "))
    ascii_text = normalized.encode("ascii", errors="ignore").decode("ascii")
    ascii_text = ascii_text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(ch for ch in ascii_text if ch == "\n" or ord(ch) >= 32).strip()


def money(value) -> str:
    try:
        return f"{int(round(float(value or 0))):,}".replace(",", ".") + ",00"
    except (TypeError, ValueError):
        return "0,00"


def quantity(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number.is_integer():
        return str(int(number))
    return (f"{number:.4f}".rstrip("0").rstrip(".")).replace(".", ",")


class Receipt:
    def __init__(self, settings: ReceiptSettings):
        self.settings = settings
        self.width = settings.columns
        self.parts: list[str] = []

    def command(self, value: str) -> None:
        self.parts.append(value)

    def line(self, value="") -> None:
        text = safe_text(value)
        if not text:
            self.parts.append("\n")
            return
        for source_line in text.split("\n"):
            wrapped = textwrap.wrap(
                source_line,
                width=self.width,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
            self.parts.extend(f"{line}\n" for line in wrapped)

    def center(self, value="") -> None:
        self.command("\x1b\x61\x01")
        for source_line in safe_text(value).split("\n"):
            for wrapped in textwrap.wrap(source_line, width=self.width) or [""]:
                self.parts.append(f"{wrapped.center(self.width)}\n")
        self.command("\x1b\x61\x00")

    def rule(self, char="-") -> None:
        self.parts.append(char * self.width + "\n")

    def pair(self, left, right) -> None:
        left_text = safe_text(left)
        right_text = safe_text(right)
        if len(right_text) >= self.width:
            self.line(left_text)
            self.line(right_text)
            return
        max_left = self.width - len(right_text) - 1
        if len(left_text) > max_left:
            wrapped_left = textwrap.wrap(left_text, width=max_left) or [""]
            for wrapped in wrapped_left[:-1]:
                self.line(wrapped)
            left_text = wrapped_left[-1]
        spaces = max(1, self.width - len(left_text) - len(right_text))
        self.parts.append(f"{left_text}{' ' * spaces}{right_text}\n")

    def item(self, name, qty, unit, unit_price, total, detail=None) -> None:
        self.line(safe_text(name).upper() or "BARANG")
        left = f"{money(unit_price)} x {quantity(qty)} {safe_text(unit).upper() or 'PCS'}"
        self.pair(left, money(total))
        if detail:
            self.line(detail)

    def header(self, title: str) -> None:
        self.command("\x1b\x61\x01\x1d\x21\x11")
        self.line(self.settings.store_name.upper())
        self.command("\x1d\x21\x00")
        if self.settings.address:
            self.line(self.settings.address)
        if self.settings.phone:
            self.line(f"Telp: {self.settings.phone}")
        self.line()
        self.center(title)

    def footer(self) -> None:
        self.line()
        self.center(self.settings.footer)
        self.parts.append("\n\n\n")

    def render(self) -> str:
        return "".join(self.parts)


def _date(value) -> str:
    return value.strftime("%d-%m-%Y") if hasattr(value, "strftime") else safe_text(value) or "-"


def _time(value) -> str:
    return value.strftime("%H:%M:%S") if hasattr(value, "strftime") else "-"


def _creator_name(creator) -> str:
    return safe_text(getattr(creator, "full_name", None) or getattr(creator, "username", None) or "-").upper()


def build_sale_receipt(sale, settings: ReceiptSettings, creator=None) -> str:
    receipt = Receipt(settings)
    receipt.header("STRUK PENJUALAN")
    receipt.pair(f"No.   : {sale.number}", _date(sale.date))
    receipt.pair(f"Kasir : {_creator_name(creator)}", _time(getattr(sale, "created_at", None)))
    customer = getattr(sale, "customer", None)
    receipt.line(f"Pel.  : {getattr(customer, 'name', None) or 'UMUM'}")
    receipt.rule()

    items = list(getattr(sale, "items", None) or [])
    total_qty = 0.0
    gross_items = 0.0
    for line in items:
        item = getattr(line, "item", None)
        unit = getattr(getattr(item, "unit", None), "name", None) or "PCS"
        receipt.item(
            getattr(item, "name", None) or "BARANG",
            line.qty,
            unit,
            line.sell_price,
            line.total,
            f"Diskon barang: {quantity(line.discount)}%" if float(line.discount or 0) > 0.005 else None,
        )
        total_qty += float(line.qty or 0)
        gross_items += float(line.total or 0)

    receipt.rule()
    receipt.pair(f"BRS={len(items)}, QTY={quantity(total_qty)}", money(gross_items))
    discount = getattr(sale, "invoice_discount_gross", None)
    if discount is None:
        excluded_tax = (
            float(getattr(sale, "tax", 0) or 0)
            if not bool(getattr(sale, "is_tax_included", True))
            else 0.0
        )
        discount = max(
            0.0,
            gross_items
            + excluded_tax
            + float(getattr(sale, "other_cost", 0) or 0)
            - float(getattr(sale, "total", 0) or 0),
        )
    if float(discount or 0) > 0.005:
        receipt.pair("Diskon Faktur", f"-{money(discount)}")
    other_cost = float(getattr(sale, "other_cost", 0) or 0)
    if other_cost > 0.005:
        receipt.pair("Biaya Lain", money(other_cost))
    receipt.pair("TOTAL", money(getattr(sale, "total", 0)))
    tax = float(getattr(sale, "tax", 0) or 0)
    if tax > 0.005:
        tax_rate = float(getattr(sale, "tax_percent", 0) or 0)
        receipt.pair(f"Termasuk PPN{f'({tax_rate:g}%)' if tax_rate else ''}", money(tax))

    receipt.rule()
    payments = list(getattr(sale, "payments", None) or [])
    if payments:
        for payment in payments:
            method = safe_text(getattr(payment, "method", ""))
            amount = float(getattr(payment, "amount", 0) or 0)
            if method == "cash" and getattr(sale, "cash_received", None) is not None:
                amount = float(sale.cash_received or 0)
            receipt.pair(PAYMENT_LABELS.get(method, method.upper() or "Pembayaran"), money(amount))
    elif float(getattr(sale, "paid", 0) or 0) > 0.005:
        method = safe_text(getattr(sale, "payment_method", "cash"))
        amount = float(getattr(sale, "paid", 0) or 0)
        if method == "cash" and getattr(sale, "cash_received", None) is not None:
            amount = float(sale.cash_received or 0)
        receipt.pair(PAYMENT_LABELS.get(method, method.upper()), money(amount))

    credit = max(0.0, float(getattr(sale, "total", 0) or 0) - float(getattr(sale, "paid", 0) or 0))
    if credit > 0.005:
        receipt.pair(PAYMENT_LABELS["credit"], money(credit))
    change = float(getattr(sale, "change", 0) or 0)
    if change > 0.005:
        receipt.pair("Kembali", money(change))
    receipt.footer()
    return receipt.render()


def build_trade_in_receipt(trade, settings: ReceiptSettings) -> str:
    receipt = Receipt(settings)
    receipt.header("NOTA TUKAR TAMBAH")
    receipt.pair(f"No.   : {trade.number}", _date(trade.date))
    receipt.pair(f"Kasir : {_creator_name(getattr(trade, 'creator', None))}", _time(getattr(trade, "created_at", None)))
    receipt.line(f"Pel.  : {getattr(getattr(trade, 'customer', None), 'name', None) or 'UMUM'}")
    receipt.rule()

    if getattr(trade, "return_items", None):
        receipt.center("BARANG KEMBALI")
        for line in trade.return_items:
            receipt.item(
                getattr(getattr(line, "item", None), "name", None) or "BARANG",
                line.qty,
                "",
                line.return_price,
                line.total,
                f"Kondisi: {safe_text(getattr(line, 'condition', '-'))}",
            )
        receipt.pair("TOTAL KEMBALI", money(trade.return_subtotal))
        receipt.rule()

    if getattr(trade, "new_items", None):
        receipt.center("BARANG BARU")
        for line in trade.new_items:
            receipt.item(
                getattr(getattr(line, "item", None), "name", None) or "BARANG",
                line.qty,
                "",
                line.sell_price,
                line.total,
            )
        receipt.pair("TOTAL BARU", money(trade.new_subtotal))
        receipt.rule()

    difference = float(getattr(trade, "difference", 0) or 0)
    receipt.pair("SELISIH", money(abs(difference)))
    if difference > 0:
        receipt.line("Pelanggan membayar")
    elif difference < 0:
        receipt.line("Masuk saldo pelanggan")
    else:
        receipt.line("Impas")
    cash = float(getattr(trade, "cash_amount", 0) or 0)
    bank = float(getattr(trade, "bank_amount", 0) or 0)
    if cash > 0.005:
        receipt.pair("Tunai", money(cash))
    if bank > 0.005:
        receipt.pair("Bank/Transfer", money(bank))
    receipt.footer()
    return receipt.render()


def build_sale_return_receipt(return_doc, settings: ReceiptSettings) -> str:
    receipt = Receipt(settings)
    sale = return_doc.sale
    receipt.header("NOTA RETUR PENJUALAN")
    receipt.pair(f"No.   : {return_doc.number}", _date(return_doc.date))
    receipt.pair(f"Kasir : {_creator_name(getattr(return_doc, 'creator', None))}", _time(return_doc.created_at))
    receipt.line(f"Faktur: {getattr(sale, 'number', '-')}")
    receipt.line(f"Pel.  : {getattr(getattr(sale, 'customer', None), 'name', None) or 'UMUM'}")
    receipt.rule()
    for line in return_doc.items:
        item = getattr(line, "item", None)
        unit = getattr(getattr(item, "unit", None), "name", None) or "PCS"
        receipt.item(getattr(item, "name", None) or "BARANG", line.qty, unit, line.price, line.total)
    receipt.rule()
    receipt.pair("TOTAL RETUR", money(return_doc.total))
    receipt.line("Dikreditkan ke saldo pelanggan")
    if return_doc.reason:
        receipt.line(f"Alasan: {return_doc.reason}")
    if return_doc.notes:
        receipt.line(f"Catatan: {return_doc.notes}")
    receipt.footer()
    return receipt.render()


def build_purchase_return_receipt(return_doc, settings: ReceiptSettings) -> str:
    receipt = Receipt(settings)
    purchase = return_doc.purchase
    receipt.header("NOTA RETUR PEMBELIAN")
    receipt.pair(f"No.   : {return_doc.number}", _date(return_doc.date))
    receipt.pair(f"Kasir : {_creator_name(getattr(return_doc, 'creator', None))}", _time(return_doc.created_at))
    receipt.line(f"Faktur: {getattr(purchase, 'number', '-')}")
    receipt.line(f"Supplier: {getattr(getattr(purchase, 'supplier', None), 'name', None) or '-'}")
    receipt.rule()
    for line in return_doc.items:
        item = getattr(line, "item", None)
        unit = getattr(getattr(item, "unit", None), "name", None) or "PCS"
        receipt.item(getattr(item, "name", None) or "BARANG", line.qty, unit, line.price, line.total)
    receipt.rule()
    receipt.pair("TOTAL RETUR", money(return_doc.total))
    receipt.line("Dikreditkan ke saldo supplier")
    if return_doc.reason:
        receipt.line(f"Alasan: {return_doc.reason}")
    if return_doc.notes:
        receipt.line(f"Catatan: {return_doc.notes}")
    receipt.footer()
    return receipt.render()


def build_test_receipt(settings: ReceiptSettings) -> str:
    receipt = Receipt(settings)
    receipt.header("TEST PRINT")
    receipt.line("Koneksi agen printer berhasil.")
    receipt.pair("Lebar kertas", f"{settings.paper_width_mm}mm")
    receipt.pair("Waktu", datetime.now().strftime("%d-%m-%Y %H:%M:%S"))
    receipt.rule()
    receipt.item("Test Barang", 1, "PCS", 1000, 1000)
    receipt.pair("TOTAL", money(1000))
    receipt.footer()
    return receipt.render()
