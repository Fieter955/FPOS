import re
import unittest
from datetime import date, datetime
from types import SimpleNamespace

from app.services.receipt_renderer import (
    ReceiptSettings,
    build_purchase_return_receipt,
    build_sale_receipt,
    build_sale_return_receipt,
    safe_text,
)


def ns(**values):
    return SimpleNamespace(**values)


def visible_lines(receipt):
    plain = re.sub(r"\x1b\x61.|\x1d\x21.", "", receipt)
    return plain.splitlines()


class ReceiptRendererTests(unittest.TestCase):
    def make_sale(self):
        item = ns(name="Kopi Susu Gula Aren Ukuran Sangat Panjang", unit=ns(name="Botol"))
        return ns(
            number="JL202608200001",
            date=date(2026, 8, 20),
            created_at=datetime(2026, 8, 20, 14, 5, 6),
            customer=ns(name="Pelanggan Umum"),
            items=[ns(item=item, qty=2, sell_price=65_000, discount=10, total=117_000)],
            payments=[
                ns(method="cash", amount=70_000),
                ns(method="debit", amount=40_000),
            ],
            subtotal=117_000,
            invoice_discount_gross=7_000,
            discount=7_000,
            other_cost=10_000,
            total=120_000,
            tax=11_891.89,
            tax_percent=11,
            is_tax_included=True,
            paid=110_000,
            cash_received=80_000,
            payment_method="cash",
            change=10_000,
        )

    def test_sale_receipt_has_accurate_split_payment_and_totals(self):
        receipt = build_sale_receipt(
            self.make_sale(),
            ReceiptSettings("Toko Cabang A", "Jalan Utama", "081234", paper_width_mm=58),
            ns(full_name="Kasir Satu"),
        )

        self.assertIn("STRUK PENJUALAN", receipt)
        self.assertIn("Diskon barang: 10%", receipt)
        self.assertIn("Diskon Faktur", receipt)
        self.assertIn("Biaya Lain", receipt)
        self.assertIn("Tunai", receipt)
        self.assertIn("80.000,00", receipt)
        self.assertIn("Kartu Debit", receipt)
        self.assertIn("Kredit/Piutang", receipt)
        self.assertIn("Kembali", receipt)
        self.assertTrue(all(len(line) <= 32 for line in visible_lines(receipt)))

    def test_80mm_receipt_uses_48_columns(self):
        receipt = build_sale_receipt(
            self.make_sale(), ReceiptSettings("Toko", paper_width_mm=80), ns(username="kasir")
        )
        self.assertTrue(all(len(line) <= 48 for line in visible_lines(receipt)))
        self.assertIn("-" * 48, receipt)

    def test_control_characters_and_unicode_are_printer_safe(self):
        self.assertEqual(safe_text("Es Kopi\x1b\x07 – café 😀"), "Es Kopi  cafe")

    def test_legacy_excluded_tax_sale_derives_invoice_discount(self):
        sale = self.make_sale()
        sale.invoice_discount_gross = None
        sale.is_tax_included = False
        sale.tax = 12_100
        sale.other_cost = 0
        sale.total = 119_100  # 117.000 - 10.000 diskon + 12.100 PPN

        receipt = build_sale_receipt(sale, ReceiptSettings("Toko"), ns(username="kasir"))

        self.assertIn("Diskon Faktur", receipt)
        self.assertIn("-10.000,00", receipt)

    def test_both_return_receipts_include_credit_destination(self):
        line = ns(item=ns(name="Barang A", unit=ns(name="PCS")), qty=1, price=25_000, total=25_000)
        creator = ns(username="admin")
        sale_return = ns(
            number="RS1", date=date(2026, 8, 20), created_at=datetime.now(),
            creator=creator, sale=ns(number="JL1", customer=ns(name="Budi")),
            items=[line], total=25_000, reason="Rusak", notes=None,
        )
        purchase_return = ns(
            number="RP1", date=date(2026, 8, 20), created_at=datetime.now(),
            creator=creator, purchase=ns(number="BL1", supplier=ns(name="Supplier A")),
            items=[line], total=25_000, reason="Rusak", notes=None,
        )
        settings = ReceiptSettings("Toko")

        self.assertIn("saldo pelanggan", build_sale_return_receipt(sale_return, settings))
        self.assertIn("saldo supplier", build_purchase_return_receipt(purchase_return, settings))


if __name__ == "__main__":
    unittest.main()
